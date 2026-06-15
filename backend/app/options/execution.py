from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import audit
from app.models import (
    OptionPaperOrder,
    OptionPaperOrderLeg,
    OptionPosition,
    OptionTradeCandidate,
    Portfolio,
    PortfolioSnapshot,
    Position,
)
from app.options.payoff import BUY_ACTIONS, entry_price, leg_sign, mid_price
from app.options.schemas import OptionLegInput

COMMISSION_PER_CONTRACT = 0.65


def legs_from_candidate(candidate: OptionTradeCandidate) -> list[OptionLegInput]:
    return [OptionLegInput.model_validate(leg) for leg in candidate.legs]


def commission_for(legs: list[OptionLegInput]) -> float:
    return round(sum(leg.qty for leg in legs) * COMMISSION_PER_CONTRACT, 2)


def opening_mid_value(legs: list[OptionLegInput]) -> float:
    return round(sum(leg_sign(leg) * mid_price(leg) * leg.qty * leg.multiplier for leg in legs), 2)


def closing_value(legs: list[OptionLegInput]) -> float:
    value = 0.0
    for leg in legs:
        if leg.action in BUY_ACTIONS:
            price = leg.bid if leg.bid is not None else mid_price(leg)
            value += price * leg.qty * leg.multiplier
        else:
            price = leg.ask if leg.ask is not None else mid_price(leg)
            value -= price * leg.qty * leg.multiplier
    return round(value, 2)


def approve_candidate(db: Session, user_id: int, candidate: OptionTradeCandidate, note: str = "") -> OptionTradeCandidate:
    if candidate.user_id != user_id:
        raise HTTPException(404, "Option candidate not found")
    if candidate.status not in ("risk_passed", "risk_blocked"):
        raise HTTPException(409, f"Candidate is '{candidate.status}', cannot decide")
    if not candidate.risk_passed or candidate.status != "risk_passed":
        raise HTTPException(409, "Cannot approve an option candidate that failed risk checks")
    if candidate.portfolio_id is None:
        raise HTTPException(409, "Candidate is not linked to a portfolio")

    portfolio = db.get(Portfolio, candidate.portfolio_id)
    if portfolio is None or portfolio.user_id != user_id:
        raise HTTPException(404, "Portfolio not found")
    if portfolio.kind != "paper":
        raise HTTPException(409, "Options execution is paper-only")

    existing = db.scalar(select(OptionPaperOrder).where(OptionPaperOrder.candidate_id == candidate.id))
    if existing is not None:
        raise HTTPException(409, "Candidate already has an option order")

    legs = legs_from_candidate(candidate)
    max_loss = (candidate.payoff or {}).get("max_loss")
    collateral = float(max_loss or 0.0)
    if collateral > portfolio.cash + 1e-9:
        raise HTTPException(409, f"Insufficient cash for option max loss collateral (${collateral:,.2f})")

    net_debit = round(sum(leg_sign(leg) * entry_price(leg) * leg.qty * leg.multiplier for leg in legs), 2)
    mid_value = opening_mid_value(legs)
    commission = commission_for(legs)

    order = OptionPaperOrder(
        candidate_id=candidate.id,
        portfolio_id=portfolio.id,
        ticker=candidate.ticker,
        strategy=candidate.strategy,
        intent="open",
        status="filled",
        net_debit=net_debit,
        mid_value=mid_value,
        commission=commission,
        detail={"note": note, "fill_model": "buy legs at ask, sell legs at bid"},
        filled_at=datetime.now(timezone.utc),
    )
    db.add(order)
    db.flush()

    for leg in legs:
        fill_price = entry_price(leg)
        db.add(OptionPaperOrderLeg(
            order_id=order.id,
            action=leg.action,
            right=leg.right,
            strike=leg.strike,
            expiry=leg.expiry,
            qty=leg.qty,
            multiplier=leg.multiplier,
            fill_price=fill_price,
            fill_value=round(leg_sign(leg) * fill_price * leg.qty * leg.multiplier, 2),
        ))

    portfolio.cash -= net_debit + commission
    unrealized = round(mid_value - net_debit - commission, 2)
    position = OptionPosition(
        candidate_id=candidate.id,
        portfolio_id=portfolio.id,
        ticker=candidate.ticker,
        strategy=candidate.strategy,
        status="open",
        legs=candidate.legs,
        entry_debit=net_debit,
        current_value=mid_value,
        max_loss=max_loss,
        max_gain=(candidate.payoff or {}).get("max_gain"),
        commissions=commission,
        unrealized_pnl=unrealized,
    )
    db.add(position)
    candidate.status = "filled"
    db.flush()

    equity = portfolio.cash + _stock_positions_value(db, portfolio.id) + _open_options_value(db, portfolio.id)
    db.add(PortfolioSnapshot(portfolio_id=portfolio.id, equity=equity, cash=portfolio.cash))
    audit(db, "option.candidate_approved", user_id=user_id, entity="option_trade_candidate",
          entity_id=str(candidate.id), payload={"note": note})
    audit(db, "option.order_filled", user_id=user_id, actor="system", entity="option_paper_order",
          entity_id=str(order.id), payload={"net_debit": net_debit, "mid_value": mid_value, "commission": commission})
    audit(db, "option.position_opened", user_id=user_id, actor="system", entity="option_position",
          entity_id=str(position.id), payload={"ticker": position.ticker, "strategy": position.strategy})
    db.commit()
    return candidate


def reject_candidate(db: Session, user_id: int, candidate: OptionTradeCandidate, note: str = "") -> OptionTradeCandidate:
    if candidate.user_id != user_id:
        raise HTTPException(404, "Option candidate not found")
    if candidate.status not in ("risk_passed", "risk_blocked"):
        raise HTTPException(409, f"Candidate is '{candidate.status}', cannot reject")
    candidate.status = "rejected"
    audit(db, "option.candidate_rejected", user_id=user_id, entity="option_trade_candidate",
          entity_id=str(candidate.id), payload={"note": note})
    db.commit()
    return candidate


def close_position(db: Session, user_id: int, position: OptionPosition, note: str = "") -> OptionPosition:
    portfolio = db.get(Portfolio, position.portfolio_id)
    if portfolio is None or portfolio.user_id != user_id:
        raise HTTPException(404, "Option position not found")
    if position.status != "open":
        raise HTTPException(409, f"Position is '{position.status}', cannot close")
    legs = [OptionLegInput.model_validate(leg) for leg in position.legs]
    close_value = closing_value(legs)
    commission = commission_for(legs)

    order = OptionPaperOrder(
        candidate_id=position.candidate_id,
        portfolio_id=portfolio.id,
        ticker=position.ticker,
        strategy=position.strategy,
        intent="close",
        status="filled",
        net_debit=-close_value,
        mid_value=0.0,
        commission=commission,
        detail={"note": note, "fill_model": "close long legs at bid, short legs at ask"},
        filled_at=datetime.now(timezone.utc),
    )
    db.add(order)
    db.flush()

    for leg in legs:
        was_long = leg.action in BUY_ACTIONS
        fill_price = (leg.bid if leg.bid is not None else mid_price(leg)) if was_long else (
            leg.ask if leg.ask is not None else mid_price(leg)
        )
        fill_value = (fill_price if was_long else -fill_price) * leg.qty * leg.multiplier
        db.add(OptionPaperOrderLeg(
            order_id=order.id,
            action="sell_to_close" if was_long else "buy_to_close",
            right=leg.right,
            strike=leg.strike,
            expiry=leg.expiry,
            qty=leg.qty,
            multiplier=leg.multiplier,
            fill_price=fill_price,
            fill_value=round(fill_value, 2),
        ))

    portfolio.cash += close_value - commission
    realized = round(close_value - position.entry_debit - position.commissions - commission, 2)
    position.realized_pnl = realized
    position.unrealized_pnl = 0.0
    position.current_value = 0.0
    position.commissions = round(position.commissions + commission, 2)
    position.status = "closed"
    position.closed_at = datetime.now(timezone.utc)
    candidate = db.get(OptionTradeCandidate, position.candidate_id)
    if candidate:
        candidate.status = "closed"
    db.flush()

    equity = portfolio.cash + _stock_positions_value(db, portfolio.id) + _open_options_value(db, portfolio.id)
    db.add(PortfolioSnapshot(portfolio_id=portfolio.id, equity=equity, cash=portfolio.cash))
    audit(db, "option.position_closed", user_id=user_id, actor="system", entity="option_position",
          entity_id=str(position.id), payload={"close_value": close_value, "realized_pnl": realized, "note": note})
    db.commit()
    return position


def _open_options_value(db: Session, portfolio_id: int) -> float:
    return sum(
        row.current_value
        for row in db.scalars(
            select(OptionPosition).where(OptionPosition.portfolio_id == portfolio_id, OptionPosition.status == "open")
        )
    )


def _stock_positions_value(db: Session, portfolio_id: int) -> float:
    return sum(
        row.qty * row.avg_entry_price
        for row in db.scalars(select(Position).where(Position.portfolio_id == portfolio_id, Position.qty > 0))
    )
