"""Pure-gate tests: every gate, pass and fail paths, no DB required."""
import pytest

from app.risk.gateway import PortfolioState, run_gates
from app.strategy.twin import StrategyTwin

TWIN = StrategyTwin.model_validate({
    "strategy_name": "t",
    "universe": {"symbols": ["AAPL", "MSFT", "SPY"], "exclusions": ["GME"]},
    "risk_management": {
        "max_position_weight_pct": 10, "max_sector_weight_pct": 30,
        "max_portfolio_drawdown_pct": 15, "max_daily_loss_pct": 3,
        "max_number_of_positions": 3, "max_orders_per_day": 5,
    },
})


def make_state(**kw) -> PortfolioState:
    base = dict(cash=50_000.0, equity=100_000.0,
                positions={"AAPL": 30_000.0, "MSFT": 20_000.0},
                position_qty={"AAPL": 100.0, "MSFT": 50.0},
                sectors={"AAPL": "Tech", "MSFT": "Tech"},
                day_start_equity=100_000.0, peak_equity=100_000.0,
                open_orders=[], orders_today=0)
    base.update(kw)
    return PortfolioState(**base)


def results_by_name(state, **order):
    defaults = dict(symbol="AAPL", side="buy", qty=10, price=100.0)
    defaults.update(order)
    return {r.name: r for r in run_gates(state, TWIN, **defaults)}


def test_all_pass_for_reasonable_buy():
    r = results_by_name(make_state(positions={}, position_qty={}, sectors={}))
    assert all(g.passed for g in r.values()), [g.name for g in r.values() if not g.passed]


def test_excluded_symbol_blocked():
    r = results_by_name(make_state(), symbol="GME")
    assert not r["instrument_whitelist"].passed


def test_non_whitelisted_symbol_blocked():
    r = results_by_name(make_state(), symbol="TSLA")
    assert not r["instrument_whitelist"].passed


def test_insufficient_cash_blocked():
    r = results_by_name(make_state(cash=500.0), qty=10, price=100.0)
    assert not r["cash_available"].passed


def test_position_size_limit():
    # AAPL already 30% of equity > 10% limit; any additional buy fails
    r = results_by_name(make_state(), qty=1, price=100.0)
    assert not r["max_position_size"].passed
    # sells always pass sizing
    r = results_by_name(make_state(), side="sell", qty=10)
    assert r["max_position_size"].passed


def test_sector_limit():
    # Tech is 50% > 30% limit
    r = results_by_name(make_state(), symbol="MSFT", qty=1, price=100.0)
    assert not r["max_sector_exposure"].passed


def test_max_positions():
    state = make_state(positions={"AAPL": 10.0, "MSFT": 10.0, "SPY": 10.0},
                       position_qty={"AAPL": 1, "MSFT": 1, "SPY": 1},
                       sectors={})
    # new 4th symbol exceeds max 3 — use a whitelisted-but-not-held symbol
    twin = TWIN.model_copy(deep=True)
    twin.universe.symbols.append("NVDA")
    r = {g.name: g for g in run_gates(state, twin, symbol="NVDA", side="buy", qty=0.01, price=100.0)}
    assert not r["max_positions"].passed
    # adding to an existing position is fine
    r2 = results_by_name(state, symbol="AAPL", qty=0.001, price=100.0)
    assert r2["max_positions"].passed


def test_daily_loss_blocks_buys_not_sells():
    state = make_state(equity=96_000.0)  # -4% on the day, limit 3%
    assert not results_by_name(state, qty=0.01, price=1.0)["max_daily_loss"].passed
    assert results_by_name(state, side="sell", qty=1)["max_daily_loss"].passed


def test_drawdown_blocks_buys_not_sells():
    state = make_state(equity=80_000.0, peak_equity=100_000.0, day_start_equity=80_500.0)  # -20% dd
    assert not results_by_name(state, qty=0.01, price=1.0)["max_drawdown"].passed
    assert results_by_name(state, side="sell", qty=1)["max_drawdown"].passed


def test_duplicate_order_blocked():
    state = make_state(open_orders=[("AAPL", "buy")])
    assert not results_by_name(state, qty=0.01, price=1.0)["duplicate_order"].passed
    assert results_by_name(state, side="sell", qty=1)["duplicate_order"].passed


def test_order_frequency():
    assert not results_by_name(make_state(orders_today=5))["order_frequency"].passed
    assert results_by_name(make_state(orders_today=4))["order_frequency"].passed


def test_sell_inventory_no_shorting():
    r = results_by_name(make_state(), side="sell", qty=1000)  # only 100 held
    assert not r["sell_inventory"].passed


def test_paper_mode_gate_blocks_non_paper():
    r = {g.name: g for g in run_gates(make_state(), TWIN, symbol="AAPL", side="buy",
                                      qty=1, price=1.0, proposal_mode="live")}
    assert not r["paper_mode"].passed
