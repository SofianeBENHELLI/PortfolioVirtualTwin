from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import audit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.execution import service as exec_service
from app.models import (
    Approval, ExecutionEvent, OrderProposal, PaperOrder, Portfolio, RiskCheck, User,
)
from app.portfolio import service as portfolio_service
from app.strategy import service as strategy_service

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


class PortfolioCreate(BaseModel):
    name: str = ""  # defaults per kind below
    kind: str = "paper"  # paper | real_tracked
    broker: str = "sim"  # sim | alpaca_paper (paper kind only)
    initial_cash: float = 100_000.0
    strategy_id: int | None = None


class HoldingUpsert(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float


class ProposalCreate(BaseModel):
    symbol: str
    side: str  # buy | sell
    qty: float
    order_type: str = "market"
    limit_price: float | None = None
    rationale: str = ""


class Decision(BaseModel):
    decision: str  # approved | rejected
    note: str = ""
    confirm_text: str = ""  # must be 'CONFIRM' to approve real orders


class CapsUpdate(BaseModel):
    max_order_notional: float | None = None
    max_live_orders_per_day: int | None = None
    strategy_id: int | None = None


class RecordFill(BaseModel):
    filled_qty: float
    fill_price: float


def _portfolio_out(p: Portfolio) -> dict:
    return {"id": p.id, "name": p.name, "kind": p.kind, "broker": p.broker, "mode": p.mode,
            "cash": p.cash, "initial_cash": p.initial_cash, "strategy_id": p.strategy_id,
            "live_armed": p.live_armed, "max_order_notional": p.max_order_notional,
            "max_live_orders_per_day": p.max_live_orders_per_day}


@router.get("")
def list_portfolios(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Portfolio).where(Portfolio.user_id == user.id)).all()
    return [_portfolio_out(p) for p in rows]


@router.post("")
def create_portfolio(payload: PortfolioCreate, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    if payload.kind not in ("paper", "real_tracked"):
        raise HTTPException(422, "kind must be 'paper' or 'real_tracked'")
    if payload.kind == "paper":
        if payload.broker not in ("sim", "alpaca_paper"):
            raise HTTPException(422, "broker must be 'sim' or 'alpaca_paper'")
        if payload.broker == "alpaca_paper" and not get_settings().alpaca_api_key:
            raise HTTPException(409, "Alpaca keys not configured — set ALPACA_API_KEY / ALPACA_SECRET_KEY or use the sim broker")
        broker = payload.broker
        name = payload.name or "Paper Portfolio"
    else:
        # real portfolios use the manual-execution broker: the app manages the order
        # lifecycle, the user executes at their actual broker and records the fill
        broker = "manual"
        name = payload.name or "Real Portfolio (tracked)"
    if payload.strategy_id is not None:
        strategy_service.get_strategy(db, user.id, payload.strategy_id)  # ownership check
    p = Portfolio(user_id=user.id, name=name, kind=payload.kind, broker=broker, mode="paper",
                  initial_cash=payload.initial_cash, cash=payload.initial_cash if payload.kind == "paper" else 0.0,
                  strategy_id=payload.strategy_id)
    db.add(p)
    db.flush()
    audit(db, "portfolio.created", user_id=user.id, entity="portfolio", entity_id=p.id,
          payload={"kind": p.kind, "broker": p.broker, "initial_cash": p.initial_cash})
    db.commit()
    return _portfolio_out(p)


# --------------------------------------------------- real portfolio holdings (manual)

def _require_real(p: Portfolio) -> None:
    if p.kind != "real_tracked":
        raise HTTPException(409, "Holdings can only be edited on a real (tracked) portfolio — paper positions come from filled orders")


@router.post("/{portfolio_id}/holdings")
def upsert_holding(portfolio_id: int, payload: HoldingUpsert,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models import Position
    p = exec_service.get_portfolio(db, user.id, portfolio_id)
    _require_real(p)
    symbol = payload.symbol.strip().upper()
    if not symbol or payload.qty <= 0 or payload.avg_entry_price <= 0:
        raise HTTPException(422, "symbol, positive qty and positive avg_entry_price required")
    pos = db.scalar(select(Position).where(Position.portfolio_id == p.id, Position.symbol == symbol))
    if pos is None:
        pos = Position(portfolio_id=p.id, symbol=symbol)
        db.add(pos)
    pos.qty = payload.qty
    pos.avg_entry_price = payload.avg_entry_price
    audit(db, "holding.upserted", user_id=user.id, entity="position", entity_id=symbol,
          payload={"portfolio_id": p.id, "qty": payload.qty, "avg_entry_price": payload.avg_entry_price})
    db.commit()
    return {"ok": True, "symbol": symbol}


@router.delete("/{portfolio_id}/holdings/{symbol}")
def delete_holding(portfolio_id: int, symbol: str,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models import Position
    p = exec_service.get_portfolio(db, user.id, portfolio_id)
    _require_real(p)
    pos = db.scalar(select(Position).where(Position.portfolio_id == p.id,
                                           Position.symbol == symbol.upper()))
    if pos is None:
        raise HTTPException(404, "Holding not found")
    db.delete(pos)
    audit(db, "holding.deleted", user_id=user.id, entity="position", entity_id=symbol.upper(),
          payload={"portfolio_id": p.id})
    db.commit()
    return {"ok": True}


@router.get("/{portfolio_id}/summary")
def summary(portfolio_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = exec_service.get_portfolio(db, user.id, portfolio_id)
    prices = exec_service.latest_prices_for(db, p)
    return portfolio_service.summary(db, p, prices)


@router.get("/{portfolio_id}/equity")
def equity(portfolio_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = exec_service.get_portfolio(db, user.id, portfolio_id)
    return portfolio_service.equity_history(db, p)


# ------------------------------------------------------------------ proposals

@router.post("/{portfolio_id}/proposals")
def create_proposal(portfolio_id: int, payload: ProposalCreate,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = exec_service.get_portfolio(db, user.id, portfolio_id)
    if p.strategy_id is None:
        raise HTTPException(409, "Portfolio has no linked strategy — link one to enable trading")
    if payload.side not in ("buy", "sell"):
        raise HTTPException(422, "side must be buy or sell")
    if payload.qty <= 0:
        raise HTTPException(422, "qty must be positive")
    version, twin = strategy_service.active_twin(db, user.id, p.strategy_id)
    proposal = exec_service.create_proposal(
        db, user.id, p, twin, version.id, symbol=payload.symbol, side=payload.side,
        qty=payload.qty, order_type=payload.order_type, limit_price=payload.limit_price,
        rationale=payload.rationale or "manual order", source="manual",
    )
    return proposal_out(db, proposal)


@router.get("/{portfolio_id}/proposals")
def list_proposals(portfolio_id: int, status: str | None = None,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    exec_service.get_portfolio(db, user.id, portfolio_id)
    q = select(OrderProposal).where(OrderProposal.portfolio_id == portfolio_id)
    if status:
        q = q.where(OrderProposal.status == status)
    rows = db.scalars(q.order_by(OrderProposal.created_at.desc()).limit(100)).all()
    return [proposal_out(db, r) for r in rows]


@router.post("/{portfolio_id}/proposals/{proposal_id}/decision")
def decide(portfolio_id: int, proposal_id: int, payload: Decision,
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    exec_service.get_portfolio(db, user.id, portfolio_id)
    proposal = db.get(OrderProposal, proposal_id)
    if proposal is None or proposal.portfolio_id != portfolio_id:
        raise HTTPException(404, "Proposal not found")
    if payload.decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision must be approved or rejected")
    proposal = exec_service.decide(db, user.id, proposal, payload.decision, payload.note,
                                   confirm_text=payload.confirm_text)
    return proposal_out(db, proposal)


def proposal_out(db: Session, p: OrderProposal) -> dict:
    checks = db.scalars(select(RiskCheck).where(RiskCheck.proposal_id == p.id)).all()
    approval = db.scalar(select(Approval).where(Approval.proposal_id == p.id))
    return {
        "id": p.id, "symbol": p.symbol, "side": p.side, "qty": p.qty,
        "order_type": p.order_type, "limit_price": p.limit_price, "status": p.status,
        "risk_passed": p.risk_passed, "rationale": p.rationale, "source": p.source,
        "risk_score": p.risk_score, "risk_factors": p.risk_factors or {},
        "created_at": p.created_at.isoformat(),
        "risk_checks": [{"name": c.check_name, "passed": c.passed, "detail": c.detail,
                         "observed": c.observed, "limit": c.limit} for c in checks],
        "approval": {"decision": approval.decision, "note": approval.note} if approval else None,
    }


# ----------------------------------------------- arming, readiness, caps, manual fills

@router.get("/{portfolio_id}/readiness")
def readiness(portfolio_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.risk.readiness import run_checklist
    p = exec_service.get_portfolio(db, user.id, portfolio_id)
    checks = run_checklist(db, p)
    return {"armed": p.live_armed, "all_passed": all(c.passed for c in checks),
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks]}


@router.post("/{portfolio_id}/arm")
def arm(portfolio_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.risk.readiness import run_checklist
    p = exec_service.get_portfolio(db, user.id, portfolio_id)
    checks = run_checklist(db, p)
    payload = {"checks": [{"name": c.name, "passed": c.passed} for c in checks]}
    if not all(c.passed for c in checks):
        audit(db, "portfolio.arm_refused", user_id=user.id, entity="portfolio", entity_id=p.id, payload=payload)
        db.commit()
        failed = [c for c in checks if not c.passed]
        raise HTTPException(409, "Readiness checklist failed: " + "; ".join(f"{c.name}: {c.detail}" for c in failed))
    p.live_armed = True
    audit(db, "portfolio.armed", user_id=user.id, entity="portfolio", entity_id=p.id, payload=payload)
    db.commit()
    return _portfolio_out(p)


@router.post("/{portfolio_id}/disarm")
def disarm(portfolio_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = exec_service.get_portfolio(db, user.id, portfolio_id)
    p.live_armed = False
    audit(db, "portfolio.disarmed", user_id=user.id, entity="portfolio", entity_id=p.id)
    db.commit()
    return _portfolio_out(p)


@router.patch("/{portfolio_id}")
def update_caps(portfolio_id: int, payload: CapsUpdate,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = exec_service.get_portfolio(db, user.id, portfolio_id)
    changes: dict = {}
    if payload.max_order_notional is not None:
        if payload.max_order_notional <= 0:
            raise HTTPException(422, "max_order_notional must be positive")
        p.max_order_notional = payload.max_order_notional
        changes["max_order_notional"] = payload.max_order_notional
    if payload.max_live_orders_per_day is not None:
        if payload.max_live_orders_per_day <= 0:
            raise HTTPException(422, "max_live_orders_per_day must be positive")
        p.max_live_orders_per_day = payload.max_live_orders_per_day
        changes["max_live_orders_per_day"] = payload.max_live_orders_per_day
    if payload.strategy_id is not None:
        strategy_service.get_strategy(db, user.id, payload.strategy_id)
        p.strategy_id = payload.strategy_id
        changes["strategy_id"] = payload.strategy_id
    if changes:
        audit(db, "portfolio.updated", user_id=user.id, entity="portfolio", entity_id=p.id, payload=changes)
        db.commit()
    return _portfolio_out(p)


@router.post("/{portfolio_id}/orders/{order_id}/record-fill")
def record_fill(portfolio_id: int, order_id: int, payload: RecordFill,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    exec_service.get_portfolio(db, user.id, portfolio_id)
    order = db.get(PaperOrder, order_id)
    if order is None or order.portfolio_id != portfolio_id:
        raise HTTPException(404, "Order not found")
    order = exec_service.record_external_fill(db, user.id, order, payload.filled_qty, payload.fill_price)
    return {"id": order.id, "status": order.status, "filled_qty": order.filled_qty,
            "filled_avg_price": order.filled_avg_price}


@router.post("/{portfolio_id}/orders/{order_id}/cancel")
def cancel(portfolio_id: int, order_id: int,
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    exec_service.get_portfolio(db, user.id, portfolio_id)
    order = db.get(PaperOrder, order_id)
    if order is None or order.portfolio_id != portfolio_id:
        raise HTTPException(404, "Order not found")
    order = exec_service.cancel_order(db, user.id, order)
    return {"id": order.id, "status": order.status}


# ------------------------------------------------------------------ orders & events

@router.get("/{portfolio_id}/orders")
def list_orders(portfolio_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    exec_service.get_portfolio(db, user.id, portfolio_id)
    rows = db.scalars(select(PaperOrder).where(PaperOrder.portfolio_id == portfolio_id)
                      .order_by(PaperOrder.created_at.desc()).limit(200)).all()
    return [{"id": o.id, "proposal_id": o.proposal_id, "broker": o.broker, "symbol": o.symbol,
             "side": o.side, "qty": o.qty, "order_type": o.order_type, "limit_price": o.limit_price,
             "status": o.status, "filled_qty": o.filled_qty, "filled_avg_price": o.filled_avg_price,
             "created_at": o.created_at.isoformat()} for o in rows]


@router.get("/{portfolio_id}/executions")
def list_executions(portfolio_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    exec_service.get_portfolio(db, user.id, portfolio_id)
    rows = db.scalars(
        select(ExecutionEvent).join(PaperOrder, ExecutionEvent.order_id == PaperOrder.id, isouter=True)
        .where((PaperOrder.portfolio_id == portfolio_id) | (ExecutionEvent.order_id.is_(None)))
        .order_by(ExecutionEvent.created_at.desc()).limit(200)
    ).all()
    return [{"id": e.id, "order_id": e.order_id, "proposal_id": e.proposal_id, "event": e.event,
             "detail": e.detail, "created_at": e.created_at.isoformat()} for e in rows]
