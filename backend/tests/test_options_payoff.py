from app.options.payoff import summarize_payoff
from app.options.risk import evaluate_candidate
from app.options.schemas import OptionLegInput


def test_bull_call_spread_payoff_is_defined():
    legs = [
        OptionLegInput(action="buy_to_open", right="call", strike=100, expiry="2026-07-17",
                       bid=4.8, ask=5.0, volume=1000, open_interest=5000),
        OptionLegInput(action="sell_to_open", right="call", strike=110, expiry="2026-07-17",
                       bid=1.9, ask=2.0, volume=1000, open_interest=5000),
    ]
    payoff = summarize_payoff(legs, underlying_price=101)
    assert payoff.net_debit == 310
    assert payoff.max_loss == 310
    assert payoff.max_gain == 690
    assert payoff.breakevens == [103.1]
    assert payoff.unlimited_loss is False


def test_naked_short_call_has_unlimited_loss():
    legs = [
        OptionLegInput(action="sell_to_open", right="call", strike=100, expiry="2026-07-17",
                       bid=4.0, ask=4.2, volume=1000, open_interest=5000),
    ]
    payoff = summarize_payoff(legs, underlying_price=100)
    assert payoff.max_loss is None
    assert payoff.unlimited_loss is True


def test_option_risk_blocks_wide_spread_and_low_liquidity():
    legs = [
        OptionLegInput(action="buy_to_open", right="call", strike=100, expiry="2026-07-17",
                       bid=1.0, ask=2.0, volume=5, open_interest=20),
    ]
    result = evaluate_candidate(legs, underlying_price=100, account_nav=100_000, risk_profile="balanced")
    checks = {check["name"]: check for check in result["risk_checks"]}
    assert not checks["leg_1_spread"]["passed"]
    assert not checks["leg_1_volume"]["passed"]
    assert not checks["leg_1_open_interest"]["passed"]


def test_defined_liquid_spread_passes_risk_budget():
    legs = [
        OptionLegInput(action="buy_to_open", right="call", strike=100, expiry="2026-07-17",
                       bid=4.8, ask=5.0, volume=1000, open_interest=5000,
                       delta=0.55, gamma=0.03, vega=0.12),
        OptionLegInput(action="sell_to_open", right="call", strike=110, expiry="2026-07-17",
                       bid=1.9, ask=2.0, volume=1000, open_interest=5000,
                       delta=0.28, gamma=0.02, vega=0.09),
    ]
    result = evaluate_candidate(legs, underlying_price=101, account_nav=100_000, risk_profile="balanced")
    assert result["risk_passed"] is True
    assert result["payoff"]["max_loss"] == 310
    assert "underlying_minus_10pct" in result["stress_results"]

