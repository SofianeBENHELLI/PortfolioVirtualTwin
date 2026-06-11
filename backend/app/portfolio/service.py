"""Portfolio valuation, P&L, and risk analytics. Deterministic pandas/numpy math —
the numbers shown in the dashboards and used by the ExplainGraph narrative."""
from __future__ import annotations

from datetime import datetime, time, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, PaperOrder, Portfolio, PortfolioSnapshot, Position


def positions_with_prices(db: Session, portfolio: Portfolio, prices: dict[str, float]) -> list[dict]:
    out = []
    for p in db.scalars(select(Position).where(Position.portfolio_id == portfolio.id, Position.qty > 0)):
        price = prices.get(p.symbol, p.avg_entry_price)
        value = p.qty * price
        unrealized = (price - p.avg_entry_price) * p.qty
        out.append({
            "symbol": p.symbol, "qty": p.qty, "avg_entry_price": p.avg_entry_price,
            "price": price, "value": value, "unrealized_pnl": unrealized,
            "unrealized_pnl_pct": (price / p.avg_entry_price - 1) * 100 if p.avg_entry_price else 0.0,
            "realized_pnl": p.realized_pnl, "opened_at": p.opened_at.isoformat(),
        })
    return sorted(out, key=lambda x: -x["value"])


def summary(db: Session, portfolio: Portfolio, prices: dict[str, float]) -> dict:
    pos = positions_with_prices(db, portfolio, prices)
    positions_value = sum(p["value"] for p in pos)
    equity = portfolio.cash + positions_value
    unrealized = sum(p["unrealized_pnl"] for p in pos)
    realized = sum(p["realized_pnl"] for p in pos) + _closed_realized(db, portfolio)
    # real (tracked) portfolios have no cash leg here — P&L is measured against cost basis
    cost_basis = sum(p["qty"] * p["avg_entry_price"] for p in pos)
    baseline = cost_basis if portfolio.kind == "real_tracked" else portfolio.initial_cash
    total_pnl = equity - baseline

    snaps = db.scalars(
        select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == portfolio.id)
        .order_by(PortfolioSnapshot.as_of)
    ).all()
    equities = [s.equity for s in snaps] + [equity]
    peak = max(equities) if equities else equity
    drawdown_pct = (peak - equity) / peak * 100 if peak > 0 else 0.0
    max_dd_pct = _max_drawdown_pct(equities)

    today_start = datetime.combine(datetime.now(timezone.utc).date(), time.min)
    prev = next((s for s in reversed(snaps) if s.as_of < today_start), None)
    day_base = prev.equity if prev else baseline
    daily_pnl = equity - day_base

    daily_rets = _daily_returns(snaps)
    vol_pct = float(np.std(daily_rets) * np.sqrt(252) * 100) if len(daily_rets) >= 2 else 0.0

    # concentration
    sectors: dict[str, str] = {}
    if pos:
        for a in db.scalars(select(Asset).where(Asset.symbol.in_([p["symbol"] for p in pos]))):
            sectors[a.symbol] = a.sector
    sector_weights: dict[str, float] = {}
    for p in pos:
        sec = sectors.get(p["symbol"], "Unknown")
        sector_weights[sec] = sector_weights.get(sec, 0.0) + (p["value"] / equity * 100 if equity else 0.0)
    top_weight = max((p["value"] / equity * 100 for p in pos), default=0.0) if equity else 0.0

    open_orders = db.scalars(select(PaperOrder).where(PaperOrder.portfolio_id == portfolio.id,
                                                      PaperOrder.status == "open")).all()
    best = max(pos, key=lambda p: p["unrealized_pnl"], default=None)
    worst = min(pos, key=lambda p: p["unrealized_pnl"], default=None)

    return {
        "portfolio_id": portfolio.id, "name": portfolio.name, "mode": portfolio.mode,
        "kind": portfolio.kind,
        "broker": portfolio.broker, "cash": portfolio.cash, "positions_value": positions_value,
        "equity": equity, "initial_cash": portfolio.initial_cash, "cost_basis": cost_basis,
        "total_pnl": total_pnl, "total_pnl_pct": total_pnl / baseline * 100 if baseline else 0.0,
        "daily_pnl": daily_pnl, "daily_pnl_pct": daily_pnl / day_base * 100 if day_base else 0.0,
        "unrealized_pnl": unrealized, "realized_pnl": realized,
        "drawdown_pct": drawdown_pct, "max_drawdown_pct": max_dd_pct,
        "volatility_pct": vol_pct,
        "n_positions": len(pos), "top_position_weight_pct": top_weight,
        "sector_weights": sector_weights,
        "open_orders": len(open_orders),
        "best_position": best["symbol"] if best else None,
        "worst_position": worst["symbol"] if worst else None,
        "positions": pos,
    }


def equity_history(db: Session, portfolio: Portfolio) -> dict:
    snaps = db.scalars(
        select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == portfolio.id)
        .order_by(PortfolioSnapshot.as_of)
    ).all()
    return {"dates": [s.as_of.isoformat() for s in snaps], "equity": [s.equity for s in snaps]}


def _closed_realized(db: Session, portfolio: Portfolio) -> float:
    closed = db.scalars(select(Position).where(Position.portfolio_id == portfolio.id, Position.qty <= 0)).all()
    return sum(p.realized_pnl for p in closed)


def _max_drawdown_pct(equities: list[float]) -> float:
    peak, max_dd = float("-inf"), 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak * 100)
    return max_dd


def _daily_returns(snaps: list[PortfolioSnapshot]) -> list[float]:
    by_day: dict[str, float] = {}
    for s in snaps:
        by_day[s.as_of.date().isoformat()] = s.equity  # last snapshot of each day wins
    vals = list(by_day.values())
    return [(vals[i] / vals[i - 1] - 1) for i in range(1, len(vals)) if vals[i - 1] > 0]
