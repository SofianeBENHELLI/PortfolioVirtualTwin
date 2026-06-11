"""Strategy Twin: the user's investment strategy as structured, validated data.

Rules are a constrained expression AST (metric, operator, value) — never free-text
code — so the exact same rule objects drive backtesting, the deterministic risk
gateway, live monitoring, and UI rendering.
"""
from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

Operator = Literal[">", ">=", "<", "<=", "==", "!="]

# Metrics the backtest engine can compute from OHLCV history. Anything else
# (fundamental/sentiment scores) is evaluated by agents at proposal time and is
# reported as "skipped" in backtests — transparency over silent omission.
BACKTESTABLE_METRICS = {
    "momentum_score",          # 0-100 cross-sectional rank of 126d return
    "price_above_200_day_average",  # bool
    "price_above_50_day_average",   # bool
    "relative_strength",       # 63d return minus benchmark 63d return, in %
    "rsi_14",
    "volatility_30d",          # annualized %, from daily returns
    "volume_confirmation",     # bool: volume > 20d average
    "drawdown_from_entry",     # %, exit rules only (maps to stop-loss)
}

KNOWN_METRICS = BACKTESTABLE_METRICS | {
    "quality_score", "valuation_risk", "news_sentiment", "earnings_quality",
    "revenue_growth", "margin_trend", "debt_to_ebitda", "analyst_revision_trend",
    "thesis_broken", "risk_score",
    # macro/portfolio metrics — evaluable live by the monitor (hedge triggers)
    "volatility_regime", "portfolio_beta", "sector_concentration",
}


class RuleCondition(BaseModel):
    metric: str
    op: Operator
    value: float | bool | str
    note: str = ""

    @field_validator("metric")
    @classmethod
    def metric_known(cls, v: str) -> str:
        if v not in KNOWN_METRICS:
            raise ValueError(f"unknown metric '{v}'. Known: {sorted(KNOWN_METRICS)}")
        return v

    def describe(self) -> str:
        return f"{self.metric} {self.op} {self.value}"

    def evaluate(self, observed: float | bool | str | None) -> bool | None:
        """Deterministic evaluation. None = metric unavailable (caller decides policy)."""
        if observed is None:
            return None
        a, b = observed, self.value
        match self.op:
            case ">":
                return a > b
            case ">=":
                return a >= b
            case "<":
                return a < b
            case "<=":
                return a <= b
            case "==":
                return a == b
            case "!=":
                return a != b
        return None


class Universe(BaseModel):
    asset_classes: list[Literal["stocks", "ETFs"]] = ["stocks", "ETFs"]
    regions: list[str] = ["US"]
    symbols: list[str] = Field(default_factory=list, description="explicit whitelist")
    exclusions: list[str] = Field(default_factory=list)


class InvestmentThesis(BaseModel):
    style: str = "quality growth"
    horizon: str = "3 to 18 months"
    description: str = ""


class Signals(BaseModel):
    fundamental: list[str] = Field(default_factory=list)
    technical: list[str] = Field(default_factory=list)
    sentiment: list[str] = Field(default_factory=list)


class RiskLimits(BaseModel):
    max_position_weight_pct: float = 8.0
    max_sector_weight_pct: float = 25.0
    max_portfolio_drawdown_pct: float = 15.0
    max_daily_loss_pct: float = 3.0
    max_number_of_positions: int = 25
    rebalance_frequency: str = "weekly"
    max_orders_per_day: int = 20


class HedgingPolicy(BaseModel):
    allowed_instruments: list[str] = ["index_ETF", "sector_ETF", "cash"]
    hedge_triggers: list[RuleCondition] = Field(default_factory=list)


class ExecutionPolicy(BaseModel):
    # SAFETY: only these two modes exist; both require a human approval on every order
    # (human_approval_required is the literal True — not configurable). live_with_approval
    # additionally requires an armed real portfolio + typed CONFIRM + readiness checklist.
    mode: Literal["paper_trading_only", "live_with_approval"] = "paper_trading_only"
    broker: Literal["sim", "alpaca_paper", "manual"] = "sim"
    order_types: list[Literal["market", "limit"]] = ["market", "limit"]
    human_approval_required: Literal[True] = True


class StrategyTwin(BaseModel):
    strategy_name: str
    universe: Universe = Field(default_factory=Universe)
    investment_thesis: InvestmentThesis = Field(default_factory=InvestmentThesis)
    signals: Signals = Field(default_factory=Signals)
    entry_rules: list[RuleCondition] = Field(default_factory=list)  # ALL must hold
    exit_rules: list[RuleCondition] = Field(default_factory=list)   # ANY triggers exit
    risk_management: RiskLimits = Field(default_factory=RiskLimits)
    hedging: HedgingPolicy = Field(default_factory=HedgingPolicy)
    execution: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    benchmark: str = "SPY"

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True)

    @classmethod
    def from_yaml(cls, text: str) -> "StrategyTwin":
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("YAML must define a mapping")
        return cls.model_validate(data)

    def backtest_coverage(self) -> tuple[list[RuleCondition], list[RuleCondition]]:
        """Split rules into (backtestable, skipped) for transparent reporting."""
        all_rules = self.entry_rules + self.exit_rules
        ok = [r for r in all_rules if r.metric in BACKTESTABLE_METRICS]
        skipped = [r for r in all_rules if r.metric not in BACKTESTABLE_METRICS]
        return ok, skipped
