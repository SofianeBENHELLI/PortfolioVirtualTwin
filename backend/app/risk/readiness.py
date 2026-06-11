"""Live-readiness checklist: ALL checks must pass before a real portfolio can be armed.
Deterministic, persisted in the audit log on every arm attempt."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MarketDataSnapshot, Portfolio, Position, Strategy, StrategyVersion, SystemState
from app.strategy.twin import StrategyTwin


@dataclass
class ReadinessCheck:
    name: str
    passed: bool
    detail: str


def run_checklist(db: Session, portfolio: Portfolio) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []

    checks.append(ReadinessCheck(
        "is_real_portfolio", portfolio.kind == "real_tracked",
        "arming applies to real (tracked) portfolios only" if portfolio.kind != "real_tracked"
        else "real portfolio"))

    sys_state = db.scalar(select(SystemState).limit(1))
    engaged = bool(sys_state and sys_state.kill_switch_engaged)
    checks.append(ReadinessCheck("kill_switch_clear", not engaged,
                                 "kill switch is engaged — disengage first" if engaged else "kill switch clear"))

    twin: StrategyTwin | None = None
    if portfolio.strategy_id is None:
        checks.append(ReadinessCheck("strategy_linked", False, "no strategy linked to this portfolio"))
    else:
        strategy = db.get(Strategy, portfolio.strategy_id)
        version = db.get(StrategyVersion, strategy.active_version_id) if strategy and strategy.active_version_id else None
        if version is None:
            checks.append(ReadinessCheck("strategy_linked", False, "strategy has no active version"))
        else:
            twin = StrategyTwin.model_validate(version.twin)
            checks.append(ReadinessCheck("strategy_linked", True, f"'{twin.strategy_name}' v{version.version}"))

    has_stop = bool(twin and any(c.metric == "drawdown_from_entry" for c in twin.exit_rules))
    checks.append(ReadinessCheck(
        "stop_loss_rule", has_stop,
        "strategy has a drawdown_from_entry exit rule" if has_stop
        else "strategy must define a drawdown_from_entry exit rule (stop-loss) before real trading"))

    caps_ok = portfolio.max_order_notional > 0 and portfolio.max_live_orders_per_day > 0
    checks.append(ReadinessCheck(
        "caps_configured", caps_ok,
        f"max ${portfolio.max_order_notional:,.0f}/order, {portfolio.max_live_orders_per_day} orders/day"
        if caps_ok else "per-order notional cap and daily order cap must be > 0"))

    # market data freshness only matters once there are holdings to monitor
    held = db.scalars(select(Position).where(Position.portfolio_id == portfolio.id, Position.qty > 0)).all()
    if held:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        stale = []
        for p in held:
            snap = db.scalar(select(MarketDataSnapshot).where(MarketDataSnapshot.symbol == p.symbol))
            as_of = snap.as_of if snap else None
            if as_of is not None and as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
            if as_of is None or as_of < cutoff:
                stale.append(p.symbol)
        checks.append(ReadinessCheck(
            "market_data_fresh", not stale,
            "all holdings priced within 24h" if not stale
            else f"stale/missing prices for {', '.join(stale)} — refresh My Stocks data"))
    else:
        checks.append(ReadinessCheck("market_data_fresh", True, "no holdings yet — nothing to monitor"))

    return checks
