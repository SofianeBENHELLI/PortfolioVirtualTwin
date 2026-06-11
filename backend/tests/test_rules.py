import pytest
from pydantic import ValidationError

from app.strategy.twin import RuleCondition, StrategyTwin

EXAMPLE_YAML = """
strategy_name: Quality Growth With Risk Control
universe:
  asset_classes: [stocks, ETFs]
  regions: [US, Europe]
  symbols: [AAPL, MSFT, NVDA, SPY]
  exclusions: [GME]
investment_thesis:
  style: quality growth
  horizon: 3 to 18 months
  description: Strong fundamentals, positive momentum, controlled valuation risk.
entry_rules:
  - {metric: quality_score, op: ">", value: 75}
  - {metric: momentum_score, op: ">", value: 60}
  - {metric: price_above_200_day_average, op: "==", value: true}
exit_rules:
  - {metric: drawdown_from_entry, op: ">", value: 12}
  - {metric: quality_score, op: "<", value: 50}
risk_management:
  max_position_weight_pct: 8
  max_sector_weight_pct: 25
  max_portfolio_drawdown_pct: 15
  max_daily_loss_pct: 3
  max_number_of_positions: 25
execution:
  mode: paper_trading_only
  broker: sim
  human_approval_required: true
benchmark: SPY
"""


def test_yaml_roundtrip():
    twin = StrategyTwin.from_yaml(EXAMPLE_YAML)
    assert twin.strategy_name == "Quality Growth With Risk Control"
    twin2 = StrategyTwin.from_yaml(twin.to_yaml())
    assert twin2 == twin


def test_paper_only_is_enforced_by_type():
    with pytest.raises(ValidationError):
        StrategyTwin.from_yaml(EXAMPLE_YAML.replace("paper_trading_only", "live"))
    with pytest.raises(ValidationError):
        StrategyTwin.from_yaml(EXAMPLE_YAML.replace("human_approval_required: true",
                                                    "human_approval_required: false"))


def test_unknown_metric_rejected():
    with pytest.raises(ValidationError):
        RuleCondition(metric="vibes", op=">", value=1)


@pytest.mark.parametrize("op,observed,value,expected", [
    (">", 80, 75, True), (">", 70, 75, False),
    (">=", 75, 75, True), ("<", 40, 50, True),
    ("<=", 50, 50, True), ("==", True, True, True),
    ("!=", "extreme", "extreme", False),
])
def test_condition_evaluate(op, observed, value, expected):
    cond = RuleCondition(metric="quality_score", op=op, value=value)
    assert cond.evaluate(observed) is expected


def test_condition_evaluate_missing_metric_returns_none():
    cond = RuleCondition(metric="quality_score", op=">", value=75)
    assert cond.evaluate(None) is None


def test_backtest_coverage_split():
    twin = StrategyTwin.from_yaml(EXAMPLE_YAML)
    ok, skipped = twin.backtest_coverage()
    ok_metrics = {r.metric for r in ok}
    skipped_metrics = {r.metric for r in skipped}
    assert "momentum_score" in ok_metrics
    assert "drawdown_from_entry" in ok_metrics
    assert "quality_score" in skipped_metrics
