from __future__ import annotations

from dataclasses import dataclass

from app.options.payoff import entry_price, leg_sign, mid_price, pnl_at_expiry, spread_pct_mid, summarize_payoff
from app.options.schemas import OptionLegInput, RiskProfileName


@dataclass(frozen=True)
class RiskProfile:
    name: str
    max_loss_pct_nav: float
    max_spread_pct_mid: float
    min_volume: int
    min_open_interest: int
    max_stress_loss_pct_nav: float


PROFILES: dict[RiskProfileName, RiskProfile] = {
    "conservative": RiskProfile("conservative", 1.0, 0.10, 200, 1000, 1.5),
    "balanced": RiskProfile("balanced", 2.0, 0.15, 100, 500, 2.5),
    "aggressive": RiskProfile("aggressive", 4.0, 0.20, 25, 150, 5.0),
}


def _check(name: str, passed: bool, detail: str, observed: str = "", limit: str = "") -> dict:
    return {"name": name, "passed": passed, "detail": detail, "observed": observed, "limit": limit}


def _stressed_leg_price(leg: OptionLegInput, underlying_price: float, d_underlying: float,
                        d_iv_points: float) -> float:
    base = mid_price(leg)
    if leg.delta is None and leg.gamma is None and leg.vega is None:
        return max(0.0, base)
    delta_move = (leg.delta or 0.0) * d_underlying
    gamma_move = 0.5 * (leg.gamma or 0.0) * d_underlying * d_underlying
    vega_move = (leg.vega or 0.0) * d_iv_points
    return max(0.0, base + delta_move + gamma_move + vega_move)


def mark_to_market_stress_pnl(legs: list[OptionLegInput], underlying_price: float,
                              underlying_move_pct: float = 0.0, iv_points: float = 0.0) -> float:
    d_underlying = underlying_price * underlying_move_pct
    return sum(
        leg_sign(leg)
        * (_stressed_leg_price(leg, underlying_price, d_underlying, iv_points) - entry_price(leg))
        * leg.qty
        * leg.multiplier
        for leg in legs
    )


def stress_scenarios(legs: list[OptionLegInput], underlying_price: float) -> dict[str, float]:
    has_greeks = any(
        leg.delta is not None or leg.gamma is not None or leg.vega is not None
        for leg in legs
    )
    scenarios = {
        "underlying_plus_5pct": 0.05,
        "underlying_minus_5pct": -0.05,
        "underlying_plus_10pct": 0.10,
        "underlying_minus_10pct": -0.10,
        "gap_up": 0.08,
        "gap_down": -0.08,
        "market_crash": -0.15,
    }
    out: dict[str, float] = {}
    for name, move in scenarios.items():
        if has_greeks:
            out[name] = round(mark_to_market_stress_pnl(legs, underlying_price, move, 0.0), 2)
        else:
            out[name] = round(pnl_at_expiry(legs, max(0.0, underlying_price * (1 + move))), 2)
    out["iv_crush"] = round(mark_to_market_stress_pnl(legs, underlying_price, 0.0, -10.0), 2)
    out["iv_expansion"] = round(mark_to_market_stress_pnl(legs, underlying_price, 0.0, 10.0), 2)
    out["volatility_spike"] = round(mark_to_market_stress_pnl(legs, underlying_price, -0.05, 15.0), 2)
    liquidity_penalty = sum((spread_pct_mid(leg) * mid_price(leg) * leg.qty * leg.multiplier) for leg in legs)
    out["liquidity_disappears"] = round(min(out.values()) - 3 * liquidity_penalty, 2)
    return out


def evaluate_candidate(legs: list[OptionLegInput], underlying_price: float, account_nav: float,
                       risk_profile: RiskProfileName = "balanced") -> dict:
    profile = PROFILES[risk_profile]
    payoff = summarize_payoff(legs, underlying_price)
    max_loss_limit = account_nav * profile.max_loss_pct_nav / 100
    stress_limit = account_nav * profile.max_stress_loss_pct_nav / 100

    checks = [
        _check(
            "defined_risk",
            payoff.max_loss is not None and not payoff.unlimited_loss,
            "maximum loss is finite" if payoff.max_loss is not None and not payoff.unlimited_loss
            else "undefined downside from the option structure",
            observed="unlimited_loss" if payoff.unlimited_loss else str(payoff.max_loss),
            limit="finite max loss required",
        ),
        _check(
            "max_loss_per_trade",
            payoff.max_loss is not None and payoff.max_loss <= max_loss_limit,
            "max loss inside per-trade budget" if payoff.max_loss is not None and payoff.max_loss <= max_loss_limit
            else "max loss exceeds per-trade budget",
            observed="unlimited" if payoff.max_loss is None else f"${payoff.max_loss:,.2f}",
            limit=f"${max_loss_limit:,.2f}",
        ),
    ]

    for i, leg in enumerate(legs, start=1):
        spread_pct = spread_pct_mid(leg)
        checks.append(_check(
            f"leg_{i}_spread",
            spread_pct <= profile.max_spread_pct_mid,
            "spread is acceptable" if spread_pct <= profile.max_spread_pct_mid else "spread is too wide",
            observed=f"{spread_pct * 100:.1f}%",
            limit=f"{profile.max_spread_pct_mid * 100:.1f}%",
        ))
        volume = leg.volume if leg.volume is not None else 0
        checks.append(_check(
            f"leg_{i}_volume",
            volume >= profile.min_volume,
            "volume is acceptable" if volume >= profile.min_volume else "volume is below liquidity floor",
            observed=str(volume),
            limit=str(profile.min_volume),
        ))
        oi = leg.open_interest if leg.open_interest is not None else 0
        checks.append(_check(
            f"leg_{i}_open_interest",
            oi >= profile.min_open_interest,
            "open interest is acceptable" if oi >= profile.min_open_interest else "open interest is below liquidity floor",
            observed=str(oi),
            limit=str(profile.min_open_interest),
        ))

    stress = stress_scenarios(legs, underlying_price)
    worst_stress = min(stress.values()) if stress else 0.0
    checks.append(_check(
        "stress_loss",
        abs(min(0.0, worst_stress)) <= stress_limit,
        "worst stress loss inside budget" if abs(min(0.0, worst_stress)) <= stress_limit
        else "worst stress loss exceeds budget",
        observed=f"${worst_stress:,.2f}",
        limit=f"${stress_limit:,.2f}",
    ))

    reward_to_risk = 0.0
    if payoff.max_loss and payoff.max_loss > 0:
        reward_to_risk = 3.0 if payoff.max_gain is None else payoff.max_gain / payoff.max_loss
    avg_liquidity = sum(max(0.0, 1 - spread_pct_mid(leg) / profile.max_spread_pct_mid) for leg in legs) / len(legs)
    stress_quality = max(0.0, 1 - abs(min(0.0, worst_stress)) / stress_limit) if stress_limit else 0.0
    score = (
        (1.0 if payoff.max_loss is not None and not payoff.unlimited_loss else 0.0) * 35
        + min(reward_to_risk / 3.0, 1.0) * 25
        + avg_liquidity * 25
        + stress_quality * 15
    )

    passed = all(c["passed"] for c in checks)
    return {
        "risk_passed": passed,
        "score": round(score, 1),
        "payoff": {
            "net_debit": payoff.net_debit,
            "max_loss": payoff.max_loss,
            "max_gain": payoff.max_gain,
            "breakevens": payoff.breakevens,
            "unlimited_loss": payoff.unlimited_loss,
            "unlimited_gain": payoff.unlimited_gain,
            "pnl_points": payoff.pnl_points,
        },
        "stress_results": stress,
        "risk_checks": checks,
        "worst_stress_pnl": worst_stress,
        "risk_profile": profile.name,
    }

