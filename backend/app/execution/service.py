"""Execution pipeline: OrderProposal → RiskGateway → human Approval → broker → fill →
portfolio update. The ONLY module that talks to brokers, and only for proposals with
risk_passed=True AND an Approval row with decision='approved'."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import audit
from app.core.events import bus
from app.data.provider import get_provider
from app.execution.brokers import get_broker
from app.models import (
    Approval,
    ExecutionEvent,
    OrderProposal,
    PaperOrder,
    Portfolio,
    PortfolioSnapshot,
    Position,
)
from app.risk.gateway import run_gateway
from app.strategy.twin import StrategyTwin


def get_portfolio(db: Session, user_id: int, portfolio_id: int) -> Portfolio:
    p = db.get(Portfolio, portfolio_id)
    if p is None or p.user_id != user_id:
        raise HTTPException(404, "Portfolio not found")
    return p


def latest_prices_for(db: Session, portfolio: Portfolio, extra: list[str] = []) -> dict[str, float]:
    symbols = {pos.symbol for pos in db.scalars(
        select(Position).where(Position.portfolio_id == portfolio.id, Position.qty > 0))}
    symbols.update(extra)
    if not symbols:
        return {}
    return get_provider().latest_prices(sorted(symbols))


def _symbol_volatility(db: Session, symbol: str) -> float | None:
    from app.models import MarketDataSnapshot
    snap = db.scalar(select(MarketDataSnapshot).where(MarketDataSnapshot.symbol == symbol))
    if snap and snap.indicators:
        v = snap.indicators.get("volatility_30d")
        return float(v) if v is not None else None
    return None


def create_proposal(db: Session, user_id: int, portfolio: Portfolio, twin: StrategyTwin,
                    strategy_version_id: int | None, *, symbol: str, side: str, qty: float,
                    order_type: str = "market", limit_price: float | None = None,
                    rationale: str = "", source: str = "manual",
                    recommendation_id: int | None = None) -> OrderProposal:
    proposal = OrderProposal(
        user_id=user_id, portfolio_id=portfolio.id, strategy_version_id=strategy_version_id,
        recommendation_id=recommendation_id, symbol=symbol.upper(), side=side, qty=qty,
        order_type=order_type, limit_price=limit_price, rationale=rationale, source=source,
    )
    db.add(proposal)
    db.flush()
    audit(db, "order.proposed", user_id=user_id, actor="agent" if source == "agent" else "user",
          entity="order_proposal", entity_id=proposal.id,
          payload={"symbol": proposal.symbol, "side": side, "qty": qty, "type": order_type})
    db.commit()

    prices = latest_prices_for(db, portfolio, extra=[proposal.symbol])

    # deterministic risk score (explainable, persisted) — computed before the gateway verdict
    from app.risk.gateway import build_state
    from app.risk.scoring import compute_risk_score
    state = build_state(db, portfolio, prices)
    ref_price = proposal.limit_price if (proposal.order_type == "limit" and proposal.limit_price) else prices.get(proposal.symbol, 0.0)
    score = compute_risk_score(state, twin, symbol=proposal.symbol, side=proposal.side,
                               notional=proposal.qty * (ref_price or 0.0),
                               symbol_volatility_30d=_symbol_volatility(db, proposal.symbol),
                               macro_regimes=state.macro_regimes)
    proposal.risk_score = score.score
    proposal.risk_factors = {"band": score.band, "factors": score.factors}
    db.commit()

    run_gateway(db, proposal, twin, portfolio, prices)
    bus.publish("proposal", {"id": proposal.id, "status": proposal.status, "symbol": proposal.symbol,
                             "risk_score": proposal.risk_score})
    return proposal


def decide(db: Session, user_id: int, proposal: OrderProposal, decision: str, note: str = "",
           confirm_text: str = "") -> OrderProposal:
    """Human approval step. Approving a risk-passed proposal submits it to the broker.
    Real portfolios additionally require the literal typed confirmation 'CONFIRM'."""
    if proposal.status not in ("risk_passed", "risk_blocked"):
        raise HTTPException(409, f"Proposal is '{proposal.status}', cannot decide")
    if decision == "approved" and proposal.status != "risk_passed":
        raise HTTPException(409, "Cannot approve a proposal that failed risk checks")
    portfolio_for_check = db.get(Portfolio, proposal.portfolio_id)
    if decision == "approved" and portfolio_for_check.kind == "real_tracked" and confirm_text != "CONFIRM":
        raise HTTPException(428, "Real order: type CONFIRM to approve — this concerns your actual money")

    db.add(Approval(proposal_id=proposal.id, user_id=user_id, decision=decision, note=note))
    audit(db, f"order.{decision}", user_id=user_id, entity="order_proposal", entity_id=proposal.id,
          payload={"note": note, "typed_confirmation": confirm_text == "CONFIRM",
                   "portfolio_kind": portfolio_for_check.kind})

    if decision == "rejected":
        proposal.status = "rejected"
        db.commit()
        bus.publish("proposal", {"id": proposal.id, "status": "rejected"})
        return proposal

    proposal.status = "approved"
    db.commit()
    _submit(db, proposal)
    return proposal


def _submit(db: Session, proposal: OrderProposal) -> None:
    portfolio = db.get(Portfolio, proposal.portfolio_id)
    broker = get_broker(portfolio.broker)
    prices = latest_prices_for(db, portfolio, extra=[proposal.symbol])
    market_price = prices.get(proposal.symbol)

    result = broker.submit(symbol=proposal.symbol, side=proposal.side, qty=proposal.qty,
                           order_type=proposal.order_type, limit_price=proposal.limit_price,
                           market_price=market_price)

    order = PaperOrder(
        proposal_id=proposal.id, portfolio_id=portfolio.id, broker=broker.name,
        broker_order_id=result.broker_order_id, symbol=proposal.symbol, side=proposal.side,
        qty=proposal.qty, order_type=proposal.order_type, limit_price=proposal.limit_price,
        status=result.status if result.status != "filled" else "open",
    )
    db.add(order)
    db.flush()
    db.add(ExecutionEvent(order_id=order.id, proposal_id=proposal.id, event="submitted",
                          detail={"broker": broker.name, "broker_order_id": result.broker_order_id}))
    proposal.status = "submitted" if result.status != "rejected" else "broker_rejected"
    audit(db, "order.submitted", user_id=proposal.user_id, actor="system",
          entity="paper_order", entity_id=order.id,
          payload={"broker": broker.name, "status": result.status, "detail": result.detail})
    db.commit()

    if result.status == "filled":
        apply_fill(db, order, result.filled_qty, result.filled_avg_price)
    elif result.status == "rejected":
        order.status = "rejected"
        db.add(ExecutionEvent(order_id=order.id, proposal_id=proposal.id, event="rejected",
                              detail={"reason": result.detail}))
        db.commit()
        bus.publish("order", {"id": order.id, "status": "rejected", "reason": result.detail})


def apply_fill(db: Session, order: PaperOrder, filled_qty: float, fill_price: float) -> None:
    """Update order, position, cash; snapshot equity; emit events. Idempotent per order."""
    if order.status == "filled":
        return
    portfolio = db.get(Portfolio, order.portfolio_id)
    order.status = "filled"
    order.filled_qty = filled_qty
    order.filled_avg_price = fill_price

    position = db.scalar(select(Position).where(Position.portfolio_id == portfolio.id,
                                                Position.symbol == order.symbol))
    if position is None:
        position = Position(portfolio_id=portfolio.id, symbol=order.symbol)
        db.add(position)
        db.flush()

    notional = filled_qty * fill_price
    # real (tracked) portfolios: cash lives at the external broker — only positions move here
    track_cash = portfolio.kind != "real_tracked"
    if order.side == "buy":
        total_cost = position.avg_entry_price * position.qty + notional
        position.qty += filled_qty
        position.avg_entry_price = total_cost / position.qty if position.qty else 0.0
        if track_cash:
            portfolio.cash -= notional
    else:
        position.realized_pnl += (fill_price - position.avg_entry_price) * filled_qty
        position.qty -= filled_qty
        if track_cash:
            portfolio.cash += notional
        if position.qty <= 1e-9:
            position.qty = 0.0

    db.add(ExecutionEvent(order_id=order.id, proposal_id=order.proposal_id, event="filled",
                          detail={"qty": filled_qty, "price": fill_price}))
    proposal = db.get(OrderProposal, order.proposal_id)
    if proposal:
        proposal.status = "filled"
    audit(db, "order.filled", user_id=portfolio.user_id, actor="system",
          entity="paper_order", entity_id=order.id,
          payload={"symbol": order.symbol, "side": order.side, "qty": filled_qty, "price": fill_price})

    prices = latest_prices_for(db, portfolio)
    prices.setdefault(order.symbol, fill_price)
    equity = portfolio.cash + sum(
        p.qty * prices.get(p.symbol, p.avg_entry_price)
        for p in db.scalars(select(Position).where(Position.portfolio_id == portfolio.id, Position.qty > 0))
    )
    db.add(PortfolioSnapshot(portfolio_id=portfolio.id, equity=equity, cash=portfolio.cash))
    db.commit()
    bus.publish("fill", {"order_id": order.id, "symbol": order.symbol, "side": order.side,
                         "qty": filled_qty, "price": fill_price})


def record_external_fill(db: Session, user_id: int, order: PaperOrder, filled_qty: float,
                         fill_price: float) -> PaperOrder:
    """User reports how a manual (real) order was actually executed at their broker."""
    if order.broker != "manual":
        raise HTTPException(409, "Only manual-execution orders can have an externally recorded fill")
    if order.status != "open":
        raise HTTPException(409, f"Order is '{order.status}', nothing to record")
    if filled_qty <= 0 or fill_price <= 0:
        raise HTTPException(422, "filled_qty and fill_price must be positive")
    if filled_qty > order.qty + 1e-9:
        raise HTTPException(422, f"filled_qty exceeds order quantity ({order.qty})")
    audit(db, "order.external_fill_recorded", user_id=user_id, entity="paper_order",
          entity_id=order.id, payload={"qty": filled_qty, "price": fill_price})
    apply_fill(db, order, filled_qty, fill_price)
    return order


def cancel_order(db: Session, user_id: int, order: PaperOrder, reason: str = "") -> PaperOrder:
    if order.status != "open":
        raise HTTPException(409, f"Order is '{order.status}', cannot cancel")
    portfolio = db.get(Portfolio, order.portfolio_id)
    get_broker(portfolio.broker).cancel(order.broker_order_id)
    order.status = "cancelled"
    db.add(ExecutionEvent(order_id=order.id, proposal_id=order.proposal_id, event="cancelled",
                          detail={"reason": reason or "user cancelled"}))
    proposal = db.get(OrderProposal, order.proposal_id)
    if proposal:
        proposal.status = "cancelled"
    audit(db, "order.cancelled", user_id=user_id, entity="paper_order", entity_id=order.id,
          payload={"reason": reason})
    db.commit()
    bus.publish("order", {"id": order.id, "status": "cancelled"})
    return order


def poll_open_orders(db: Session, portfolio: Portfolio) -> int:
    """Called by the monitor loop. Re-checks open limit orders (sim) / polls Alpaca."""
    open_orders = db.scalars(select(PaperOrder).where(PaperOrder.portfolio_id == portfolio.id,
                                                      PaperOrder.status == "open")).all()
    if not open_orders:
        return 0
    prices = latest_prices_for(db, portfolio, extra=[o.symbol for o in open_orders])
    filled = 0
    broker = get_broker(portfolio.broker)
    for order in open_orders:
        market_price = prices.get(order.symbol)
        if portfolio.broker == "sim":
            if order.order_type == "market" and market_price is not None:
                apply_fill(db, order, order.qty, market_price)
                filled += 1
            elif (order.order_type == "limit" and market_price is not None and order.limit_price is not None
                  and ((order.side == "buy" and market_price <= order.limit_price)
                       or (order.side == "sell" and market_price >= order.limit_price))):
                apply_fill(db, order, order.qty, market_price)
                filled += 1
        else:
            result = broker.poll(order.broker_order_id, market_price)
            if result.status == "filled" and result.filled_avg_price:
                apply_fill(db, order, result.filled_qty or order.qty, result.filled_avg_price)
                filled += 1
            elif result.status in ("cancelled", "rejected"):
                order.status = result.status
                db.add(ExecutionEvent(order_id=order.id, proposal_id=order.proposal_id,
                                      event=result.status, detail={"reason": result.detail}))
                db.commit()
    return filled
