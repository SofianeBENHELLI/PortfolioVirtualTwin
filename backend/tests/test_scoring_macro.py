"""Risk-score properties + deterministic macro regime flags."""
import pytest

from app.data.macro import compute_regimes
from app.risk.gateway import PortfolioState, macro_damping
from app.risk.scoring import compute_risk_score
from app.strategy.twin import StrategyTwin

TWIN = StrategyTwin.model_validate({
    "strategy_name": "t",
    "risk_management": {"max_position_weight_pct": 10, "max_sector_weight_pct": 30,
                        "max_portfolio_drawdown_pct": 15, "max_daily_loss_pct": 3,
                        "max_number_of_positions": 10, "max_orders_per_day": 10},
})


def neutral_state(**kw) -> PortfolioState:
    base = dict(cash=100_000.0, equity=100_000.0, positions={}, position_qty={}, sectors={},
                day_start_equity=100_000.0, peak_equity=100_000.0, open_orders=[], orders_today=0)
    base.update(kw)
    return PortfolioState(**base)


def test_score_monotonic_in_order_size():
    small = compute_risk_score(neutral_state(), TWIN, symbol="AAPL", side="buy",
                               notional=1_000, symbol_volatility_30d=20)
    big = compute_risk_score(neutral_state(), TWIN, symbol="AAPL", side="buy",
                             notional=9_000, symbol_volatility_30d=20)
    assert big.score > small.score


def test_sell_scores_below_equivalent_buy():
    state = neutral_state(positions={"AAPL": 8_000}, position_qty={"AAPL": 80}, sectors={"AAPL": "Tech"})
    buy = compute_risk_score(state, TWIN, symbol="AAPL", side="buy", notional=2_000, symbol_volatility_30d=30)
    sell = compute_risk_score(state, TWIN, symbol="AAPL", side="sell", notional=2_000, symbol_volatility_30d=30)
    assert sell.score < buy.score


def test_hostile_macro_raises_score_and_damps_gateway():
    hostile = {"volatility_regime": "high", "risk_off": True, "war_risk": "high"}
    calm = {"volatility_regime": "calm", "risk_off": False, "war_risk": "low"}
    s_calm = compute_risk_score(neutral_state(), TWIN, symbol="AAPL", side="buy",
                                notional=5_000, symbol_volatility_30d=20, macro_regimes=calm)
    s_hot = compute_risk_score(neutral_state(), TWIN, symbol="AAPL", side="buy",
                               notional=5_000, symbol_volatility_30d=20, macro_regimes=hostile)
    assert s_hot.score > s_calm.score
    assert macro_damping(hostile)[0] < 1.0
    assert macro_damping(calm)[0] == 1.0


def test_score_bands_and_bounds():
    s = compute_risk_score(neutral_state(), TWIN, symbol="AAPL", side="buy",
                           notional=10_000, symbol_volatility_30d=80,
                           macro_regimes={"volatility_regime": "high", "risk_off": True,
                                          "war_risk": "high", "oil_shock": True})
    assert 0 <= s.score <= 100 and s.band in ("low", "moderate", "elevated", "high")
    assert set(s.factors) == {"position_size", "sector_concentration", "symbol_volatility",
                              "drawdown_proximity", "order_frequency", "macro_regime"}


# ------------------------------------------------------------- regime flags

def _ind(vix=14.0, sp5=1.0, wti5=2.0, gold30=1.0, gold_above=False):
    return {
        "vix": {"value": vix},
        "sp500": {"chg_5d_pct": sp5},
        "wti": {"chg_5d_pct": wti5},
        "gold": {"chg_5d_pct": 1.0, "chg_30d_pct": gold30, "above_200d": gold_above},
    }


def test_calm_regime():
    r = compute_regimes(_ind(), {}, {"available": False}, {"available": False})
    assert r["volatility_regime"] == "calm"
    assert not r["risk_off"] and not r["oil_shock"] and not r["gold_rush"]
    assert r["war_risk"] == "low" and r["curve_inverted"] is None


def test_risk_off_and_high_vol():
    r = compute_regimes(_ind(vix=30.0), {}, {"available": False}, {"available": False})
    assert r["volatility_regime"] == "high" and r["risk_off"]


def test_oil_crash_and_gold_rush_and_war():
    r = compute_regimes(
        _ind(wti5=-12.0, gold30=8.0, gold_above=True),
        {"t10y2y": -0.3},
        {"available": True, "z_score": 2.5},
        {"available": True, "percentile_1y": 95.0},
    )
    assert r["oil_shock"] and r["oil_shock_direction"] == "crash"
    assert r["gold_rush"]
    assert r["war_risk"] == "high"
    assert r["curve_inverted"] is True
