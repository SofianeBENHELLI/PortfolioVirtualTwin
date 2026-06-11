"""Deterministic 0-100 risk score for proposals and recommendations.

Pure function over portfolio state, strategy limits, symbol stats and the macro regime.
Each factor is a 0..1 utilization; the weighted sum is scaled to 0-100 and banded.
Factors are returned for explainability and persisted alongside the score.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.risk.gateway import PortfolioState
from app.strategy.twin import StrategyTwin

WEIGHTS = {
    "position_size": 0.25,
    "sector_concentration": 0.15,
    "symbol_volatility": 0.20,
    "drawdown_proximity": 0.20,
    "order_frequency": 0.05,
    "macro_regime": 0.15,
}

BANDS = [(25, "low"), (50, "moderate"), (75, "elevated"), (101, "high")]


@dataclass
class RiskScore:
    score: float            # 0-100
    band: str               # low | moderate | elevated | high
    factors: dict[str, dict]  # name -> {utilization, detail}


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def macro_utilization(regimes: dict | None) -> tuple[float, str]:
    if not regimes:
        return 0.25, "no macro snapshot — neutral assumption"
    score = 0.0
    notes = []
    vol = regimes.get("volatility_regime")
    if vol == "elevated":
        score += 0.4
        notes.append("volatility elevated")
    elif vol == "high":
        score += 0.7
        notes.append("volatility HIGH")
    if regimes.get("risk_off"):
        score += 0.3
        notes.append("risk-off")
    if regimes.get("war_risk") == "high":
        score += 0.3
        notes.append("war risk HIGH")
    elif regimes.get("war_risk") == "elevated":
        score += 0.15
        notes.append("war risk elevated")
    if regimes.get("oil_shock"):
        score += 0.1
        notes.append("oil shock")
    return _clamp(score), ", ".join(notes) or "calm regime"


def compute_risk_score(
    state: PortfolioState,
    twin: StrategyTwin,
    *,
    symbol: str,
    side: str,
    notional: float,
    symbol_volatility_30d: float | None = None,
    macro_regimes: dict | None = None,
) -> RiskScore:
    rm = twin.risk_management
    factors: dict[str, dict] = {}

    # 1. position size utilization (sells reduce risk)
    if side == "sell" or state.equity <= 0:
        size_u = 0.0
        size_d = "sell order / no equity at risk"
    else:
        new_weight = (state.positions.get(symbol, 0.0) + notional) / state.equity * 100
        size_u = _clamp(new_weight / max(rm.max_position_weight_pct, 0.01))
        size_d = f"resulting weight {new_weight:.1f}% of {rm.max_position_weight_pct:.0f}% limit"
    factors["position_size"] = {"utilization": size_u, "detail": size_d}

    # 2. sector concentration
    if side == "sell" or state.equity <= 0:
        sect_u, sect_d = 0.0, "sell order"
    else:
        sector = state.sectors.get(symbol, "Unknown")
        sector_value = sum(v for s, v in state.positions.items()
                           if state.sectors.get(s, "Unknown") == sector)
        new_sector = (sector_value + notional) / state.equity * 100
        sect_u = _clamp(new_sector / max(rm.max_sector_weight_pct, 0.01))
        sect_d = f"sector '{sector}' would be {new_sector:.1f}% of {rm.max_sector_weight_pct:.0f}% limit"
    factors["sector_concentration"] = {"utilization": sect_u, "detail": sect_d}

    # 3. symbol volatility (annualized %, 60%+ treated as max risk)
    if symbol_volatility_30d is None:
        vol_u, vol_d = 0.35, "volatility unknown — conservative assumption"
    else:
        vol_u = _clamp(symbol_volatility_30d / 60.0)
        vol_d = f"30d volatility {symbol_volatility_30d:.0f}% annualized"
    factors["symbol_volatility"] = {"utilization": vol_u, "detail": vol_d}

    # 4. drawdown proximity
    if state.peak_equity and state.peak_equity > 0:
        dd = max(0.0, (state.peak_equity - state.equity) / state.peak_equity * 100)
        dd_u = _clamp(dd / max(rm.max_portfolio_drawdown_pct, 0.01))
        dd_d = f"drawdown {dd:.1f}% of {rm.max_portfolio_drawdown_pct:.0f}% limit"
    else:
        dd_u, dd_d = 0.0, "no drawdown history"
    factors["drawdown_proximity"] = {"utilization": dd_u, "detail": dd_d}

    # 5. order frequency
    freq_u = _clamp(state.orders_today / max(rm.max_orders_per_day, 1))
    factors["order_frequency"] = {"utilization": freq_u,
                                  "detail": f"{state.orders_today}/{rm.max_orders_per_day} orders today"}

    # 6. macro regime
    macro_u, macro_d = macro_utilization(macro_regimes)
    factors["macro_regime"] = {"utilization": macro_u, "detail": macro_d}

    score = sum(WEIGHTS[name] * f["utilization"] for name, f in factors.items()) * 100
    band = next(b for limit, b in BANDS if score < limit)
    return RiskScore(score=round(score, 1), band=band, factors=factors)


def score_recommendation(db, user_id: int, twin: StrategyTwin, symbol: str, action: str,
                         symbol_volatility_30d: float | None) -> RiskScore:
    """Risk score for a recommendation (no concrete order yet): assumes the strategy's
    target position size against the user's paper portfolio state (or a neutral state)."""
    from sqlalchemy import select

    from app.models import MacroSnapshot, Portfolio
    from app.risk.gateway import PortfolioState, build_state

    macro = db.scalar(select(MacroSnapshot).order_by(MacroSnapshot.as_of.desc()).limit(1))
    portfolio = db.scalar(select(Portfolio).where(Portfolio.user_id == user_id,
                                                  Portfolio.kind == "paper").limit(1))
    if portfolio is not None:
        from app.execution import service as exec_service
        prices = exec_service.latest_prices_for(db, portfolio, extra=[symbol])
        state = build_state(db, portfolio, prices)
    else:
        state = PortfolioState(cash=100_000.0, equity=100_000.0, positions={}, position_qty={},
                               sectors={}, day_start_equity=None, peak_equity=100_000.0,
                               open_orders=[], orders_today=0,
                               macro_regimes=macro.regimes if macro else None)
    target_weight = min(twin.risk_management.max_position_weight_pct,
                        100.0 / max(1, twin.risk_management.max_number_of_positions)) / 100.0
    notional = state.equity * target_weight
    side = "sell" if action == "sell" else "buy"
    return compute_risk_score(state, twin, symbol=symbol, side=side, notional=notional,
                              symbol_volatility_30d=symbol_volatility_30d,
                              macro_regimes=state.macro_regimes)
