"""Deterministic Risk Gateway.

Every OrderProposal passes through run_gateway() before it can be approved or
executed. No LLM is involved anywhere in this module. Each gate produces a
persisted RiskCheck row (append-only) so the user can always see exactly why an
order was allowed or blocked.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import audit
from app.core.config import get_settings
from app.models import (
    Asset,
    ExecutionEvent,
    OrderProposal,
    PaperOrder,
    Portfolio,
    PortfolioSnapshot,
    Position,
    RiskCheck,
)
from app.strategy.twin import StrategyTwin


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str
    observed: str = ""
    limit: str = ""


@dataclass
class PortfolioState:
    """Inputs the gates need, assembled once. Pure gates = easy unit tests."""
    cash: float
    equity: float
    positions: dict[str, float]        # symbol -> market value
    position_qty: dict[str, float]     # symbol -> qty
    sectors: dict[str, str]            # symbol -> sector
    day_start_equity: float | None
    peak_equity: float | None
    open_orders: list[tuple[str, str]]  # (symbol, side)
    orders_today: int
    # real-portfolio guardrails
    kind: str = "paper"                # paper | real_tracked
    live_armed: bool = False
    kill_switch_engaged: bool = False
    max_order_notional: float = 0.0
    max_live_orders_per_day: int = 0
    # macro regime flags from the latest MacroSnapshot (None = no snapshot yet)
    macro_regimes: dict | None = None


# --------------------------------------------------------------------------- gates

def gate_paper_mode(state: PortfolioState, proposal_mode: str) -> GateResult:
    s = get_settings()
    ok = s.trading_mode == "paper" and proposal_mode == "paper"
    return GateResult("paper_mode", ok,
                      "system is paper-only" if ok else "BLOCKED: non-paper mode requested",
                      observed=proposal_mode, limit="paper")


def gate_universe(twin: StrategyTwin, symbol: str) -> GateResult:
    excl = {e.upper() for e in twin.universe.exclusions}
    if symbol.upper() in excl:
        return GateResult("instrument_whitelist", False, f"{symbol} is in strategy exclusions",
                          observed=symbol, limit=f"exclusions={sorted(excl)}")
    wl = {s.upper() for s in twin.universe.symbols}
    if wl and symbol.upper() not in wl:
        return GateResult("instrument_whitelist", False, f"{symbol} not in strategy universe whitelist",
                          observed=symbol, limit=f"whitelist({len(wl)} symbols)")
    return GateResult("instrument_whitelist", True, f"{symbol} allowed by universe")


def gate_cash(state: PortfolioState, side: str, notional: float) -> GateResult:
    if side == "sell":
        return GateResult("cash_available", True, "sell order, no cash required")
    if state.kind == "real_tracked":
        # cash for a real account lives at the external broker — the notional cap gates size
        return GateResult("cash_available", True, "real portfolio: cash managed at your broker")
    ok = notional <= state.cash
    return GateResult("cash_available", ok,
                      "sufficient cash" if ok else "insufficient cash",
                      observed=f"need ${notional:,.2f}", limit=f"cash ${state.cash:,.2f}")


def gate_position_size(state: PortfolioState, twin: StrategyTwin, symbol: str, side: str, notional: float) -> GateResult:
    limit_pct = twin.risk_management.max_position_weight_pct
    if side == "sell":
        return GateResult("max_position_size", True, "sell reduces exposure")
    if state.equity <= 0:
        if state.kind == "real_tracked":
            # no valued holdings yet — the per-order notional cap governs sizing
            return GateResult("max_position_size", True, "no holdings valued yet — notional cap governs size")
        return GateResult("max_position_size", False, "portfolio has no equity")
    current = state.positions.get(symbol, 0.0)
    new_weight = (current + notional) / state.equity * 100
    ok = new_weight <= limit_pct + 1e-9
    return GateResult("max_position_size", ok,
                      f"resulting weight {new_weight:.1f}%",
                      observed=f"{new_weight:.1f}%", limit=f"{limit_pct:.1f}%")


def gate_sector_exposure(state: PortfolioState, twin: StrategyTwin, symbol: str, side: str, notional: float) -> GateResult:
    limit_pct = twin.risk_management.max_sector_weight_pct
    if side == "sell":
        return GateResult("max_sector_exposure", True, "sell reduces exposure")
    if state.equity <= 0:
        if state.kind == "real_tracked":
            return GateResult("max_sector_exposure", True, "no holdings valued yet — notional cap governs size")
        return GateResult("max_sector_exposure", False, "portfolio has no equity")
    sector = state.sectors.get(symbol, "Unknown")
    sector_value = sum(v for s, v in state.positions.items() if state.sectors.get(s, "Unknown") == sector)
    new_weight = (sector_value + notional) / state.equity * 100
    ok = new_weight <= limit_pct + 1e-9
    return GateResult("max_sector_exposure", ok,
                      f"sector '{sector}' would be {new_weight:.1f}%",
                      observed=f"{new_weight:.1f}%", limit=f"{limit_pct:.1f}%")


def gate_max_positions(state: PortfolioState, twin: StrategyTwin, symbol: str, side: str) -> GateResult:
    limit_n = twin.risk_management.max_number_of_positions
    if side == "sell" or symbol in state.positions:
        return GateResult("max_positions", True, "not a new position")
    n = len(state.positions)
    ok = n + 1 <= limit_n
    return GateResult("max_positions", ok, f"would hold {n + 1} positions",
                      observed=str(n + 1), limit=str(limit_n))


def gate_daily_loss(state: PortfolioState, twin: StrategyTwin, side: str) -> GateResult:
    limit_pct = twin.risk_management.max_daily_loss_pct
    if state.day_start_equity is None or state.day_start_equity <= 0:
        return GateResult("max_daily_loss", True, "no intraday baseline yet")
    loss_pct = max(0.0, (state.day_start_equity - state.equity) / state.day_start_equity * 100)
    # once the daily loss limit is breached, only risk-reducing (sell) orders pass
    ok = loss_pct < limit_pct or side == "sell"
    return GateResult("max_daily_loss", ok,
                      f"daily loss {loss_pct:.2f}%" + ("" if ok else " — buys blocked"),
                      observed=f"{loss_pct:.2f}%", limit=f"{limit_pct:.1f}%")


def gate_drawdown(state: PortfolioState, twin: StrategyTwin, side: str) -> GateResult:
    limit_pct = twin.risk_management.max_portfolio_drawdown_pct
    if state.peak_equity is None or state.peak_equity <= 0:
        return GateResult("max_drawdown", True, "no equity history yet")
    dd_pct = max(0.0, (state.peak_equity - state.equity) / state.peak_equity * 100)
    ok = dd_pct < limit_pct or side == "sell"
    return GateResult("max_drawdown", ok,
                      f"drawdown {dd_pct:.2f}%" + ("" if ok else " — buys blocked"),
                      observed=f"{dd_pct:.2f}%", limit=f"{limit_pct:.1f}%")


def gate_duplicate(state: PortfolioState, symbol: str, side: str) -> GateResult:
    dup = (symbol, side) in state.open_orders
    return GateResult("duplicate_order", not dup,
                      "no duplicate open order" if not dup else f"open {side} order for {symbol} already exists",
                      observed=f"{symbol}/{side}")


def gate_order_frequency(state: PortfolioState, twin: StrategyTwin) -> GateResult:
    limit_n = twin.risk_management.max_orders_per_day
    ok = state.orders_today < limit_n
    return GateResult("order_frequency", ok, f"{state.orders_today} orders today",
                      observed=str(state.orders_today), limit=f"{limit_n}/day")


def gate_sell_inventory(state: PortfolioState, symbol: str, side: str, qty: float) -> GateResult:
    if side == "buy":
        return GateResult("sell_inventory", True, "buy order")
    held = state.position_qty.get(symbol, 0.0)
    ok = qty <= held + 1e-9
    return GateResult("sell_inventory", ok,
                      f"selling {qty} of {held} held" + ("" if ok else " — no shorting in paper MVP"),
                      observed=f"{qty}", limit=f"held {held}")


def gate_kill_switch(state: PortfolioState) -> GateResult:
    ok = not state.kill_switch_engaged
    return GateResult("kill_switch", ok,
                      "kill switch not engaged" if ok else "KILL SWITCH ENGAGED — all trading halted")


def gate_live_armed(state: PortfolioState) -> GateResult:
    if state.kind == "paper":
        return GateResult("live_armed", True, "paper portfolio — arming not required")
    ok = state.live_armed
    return GateResult("live_armed", ok,
                      "real portfolio is armed" if ok
                      else "real portfolio is NOT armed — pass the readiness checklist and arm it first")


def gate_order_notional(state: PortfolioState, notional: float) -> GateResult:
    if state.kind == "paper":
        return GateResult("order_notional_cap", True, "paper portfolio — no notional cap")
    ok = notional <= state.max_order_notional + 1e-9
    return GateResult("order_notional_cap", ok,
                      f"order notional ${notional:,.0f}",
                      observed=f"${notional:,.0f}", limit=f"${state.max_order_notional:,.0f} per order")


def gate_live_frequency(state: PortfolioState) -> GateResult:
    if state.kind == "paper":
        return GateResult("live_order_frequency", True, "paper portfolio")
    ok = state.orders_today < state.max_live_orders_per_day
    return GateResult("live_order_frequency", ok, f"{state.orders_today} real orders today",
                      observed=str(state.orders_today), limit=f"{state.max_live_orders_per_day}/day")


# Damping applied to position-size limits when the macro regime is hostile.
MACRO_DAMPING_FACTOR = 0.7


def macro_damping(regimes: dict | None) -> tuple[float, str]:
    """Returns (factor, reason). factor < 1 shrinks the allowed position size."""
    if not regimes:
        return 1.0, "no macro snapshot"
    hostile = []
    if regimes.get("risk_off"):
        hostile.append("risk-off")
    if regimes.get("volatility_regime") == "high":
        hostile.append("high volatility")
    if regimes.get("war_risk") == "high":
        hostile.append("war risk high")
    if not hostile:
        return 1.0, "regime calm"
    return MACRO_DAMPING_FACTOR, " + ".join(hostile)


def gate_macro_regime(state: PortfolioState, twin: StrategyTwin, symbol: str,
                      side: str, notional: float) -> GateResult:
    factor, reason = macro_damping(state.macro_regimes)
    if side == "sell" or factor >= 1.0:
        return GateResult("macro_regime", True, f"no damping ({reason})")
    damped_limit = twin.risk_management.max_position_weight_pct * factor
    if state.equity <= 0:
        if state.kind == "real_tracked":
            return GateResult("macro_regime", True,
                              f"hostile regime ({reason}) noted — notional cap governs size (no valued holdings)")
        return GateResult("macro_regime", False, "no equity")
    new_weight = (state.positions.get(symbol, 0.0) + notional) / state.equity * 100
    ok = new_weight <= damped_limit + 1e-9
    return GateResult("macro_regime", ok,
                      f"hostile regime ({reason}): position cap damped ×{factor}",
                      observed=f"{new_weight:.1f}%", limit=f"{damped_limit:.1f}% (damped)")


def run_gates(state: PortfolioState, twin: StrategyTwin, *, symbol: str, side: str,
              qty: float, price: float, proposal_mode: str = "paper") -> list[GateResult]:
    notional = qty * price
    return [
        gate_kill_switch(state),
        gate_paper_mode(state, proposal_mode),
        gate_live_armed(state),
        gate_universe(twin, symbol),
        gate_sell_inventory(state, symbol, side, qty),
        gate_cash(state, side, notional),
        gate_position_size(state, twin, symbol, side, notional),
        gate_sector_exposure(state, twin, symbol, side, notional),
        gate_max_positions(state, twin, symbol, side),
        gate_daily_loss(state, twin, side),
        gate_drawdown(state, twin, side),
        gate_duplicate(state, symbol, side),
        gate_order_frequency(state, twin),
        gate_order_notional(state, notional),
        gate_live_frequency(state),
        gate_macro_regime(state, twin, symbol, side, notional),
    ]


# ----------------------------------------------------------------- orchestration

def build_state(db: Session, portfolio: Portfolio, prices: dict[str, float]) -> PortfolioState:
    positions = db.scalars(select(Position).where(Position.portfolio_id == portfolio.id, Position.qty > 0)).all()
    pos_value = {p.symbol: p.qty * prices.get(p.symbol, p.avg_entry_price) for p in positions}
    equity = portfolio.cash + sum(pos_value.values())

    sectors: dict[str, str] = {}
    if positions:
        symbols = [p.symbol for p in positions]
        for a in db.scalars(select(Asset).where(Asset.symbol.in_(symbols))):
            sectors[a.symbol] = a.sector
        # fallback: sector from the latest market-data snapshot fundamentals
        from app.models import MarketDataSnapshot
        missing = [s for s in symbols if s not in sectors]
        if missing:
            for snap in db.scalars(select(MarketDataSnapshot).where(MarketDataSnapshot.symbol.in_(missing))):
                sector = (snap.indicators or {}).get("fundamentals", {}).get("sector")
                if sector:
                    sectors[snap.symbol] = str(sector)

    today_start = datetime.combine(datetime.now(timezone.utc).date(), time.min)
    day_snap = db.scalar(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.portfolio_id == portfolio.id, PortfolioSnapshot.as_of < today_start)
        .order_by(PortfolioSnapshot.as_of.desc()).limit(1)
    )
    peak = db.scalar(
        select(func.max(PortfolioSnapshot.equity)).where(PortfolioSnapshot.portfolio_id == portfolio.id)
    )
    open_orders = [
        (o.symbol, o.side)
        for o in db.scalars(select(PaperOrder).where(PaperOrder.portfolio_id == portfolio.id,
                                                     PaperOrder.status == "open"))
    ]
    orders_today = db.scalar(
        select(func.count(PaperOrder.id)).where(PaperOrder.portfolio_id == portfolio.id,
                                                PaperOrder.created_at >= today_start)
    ) or 0

    from app.models import MacroSnapshot, SystemState

    sys_state = db.scalar(select(SystemState).limit(1))
    macro = db.scalar(select(MacroSnapshot).order_by(MacroSnapshot.as_of.desc()).limit(1))

    return PortfolioState(
        cash=portfolio.cash,
        equity=equity,
        positions=pos_value,
        position_qty={p.symbol: p.qty for p in positions},
        sectors=sectors,
        day_start_equity=day_snap.equity if day_snap else None,
        peak_equity=max(peak, equity) if peak else equity,
        open_orders=open_orders,
        orders_today=orders_today,
        kind=portfolio.kind,
        live_armed=portfolio.live_armed,
        kill_switch_engaged=bool(sys_state and sys_state.kill_switch_engaged),
        max_order_notional=portfolio.max_order_notional,
        max_live_orders_per_day=portfolio.max_live_orders_per_day,
        macro_regimes=macro.regimes if macro else None,
    )


def run_gateway(db: Session, proposal: OrderProposal, twin: StrategyTwin,
                portfolio: Portfolio, prices: dict[str, float]) -> bool:
    """Run all gates, persist RiskCheck rows, update proposal status. Returns pass/fail."""
    state = build_state(db, portfolio, prices)
    price = prices.get(proposal.symbol)
    if price is None:
        results = [GateResult("price_available", False, f"no market price for {proposal.symbol}")]
    else:
        ref_price = proposal.limit_price if (proposal.order_type == "limit" and proposal.limit_price) else price
        results = run_gates(state, twin, symbol=proposal.symbol, side=proposal.side,
                            qty=proposal.qty, price=ref_price, proposal_mode=portfolio.mode)

    passed = all(r.passed for r in results)
    for r in results:
        db.add(RiskCheck(proposal_id=proposal.id, check_name=r.name, passed=r.passed,
                         detail=r.detail, observed=r.observed, limit=r.limit))
    proposal.risk_passed = passed
    proposal.status = "risk_passed" if passed else "risk_blocked"
    if not passed:
        db.add(ExecutionEvent(proposal_id=proposal.id, event="risk_blocked",
                              detail={"failed": [r.name for r in results if not r.passed]}))
    audit(db, "risk.gateway_run", user_id=proposal.user_id, actor="system",
          entity="order_proposal", entity_id=proposal.id,
          payload={"passed": passed, "failed_checks": [r.name for r in results if not r.passed]})
    db.commit()
    return passed
