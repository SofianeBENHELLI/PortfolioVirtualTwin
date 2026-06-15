from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


OptionAction = Literal["buy_to_open", "sell_to_open", "buy", "sell"]
OptionRight = Literal["call", "put"]
RiskProfileName = Literal["conservative", "balanced", "aggressive"]


class OptionLegInput(BaseModel):
    action: OptionAction
    right: OptionRight
    strike: float
    expiry: str
    qty: int = 1
    multiplier: int = 100
    bid: float | None = None
    ask: float | None = None
    premium: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    rho: float | None = None

    @field_validator("strike")
    @classmethod
    def strike_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("strike must be positive")
        return value

    @field_validator("qty", "multiplier")
    @classmethod
    def positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("qty and multiplier must be positive")
        return value

    @model_validator(mode="after")
    def quote_is_usable(self) -> "OptionLegInput":
        if self.premium is None and (self.bid is None or self.ask is None):
            raise ValueError("provide either premium or bid/ask for every option leg")
        if self.bid is not None and self.ask is not None:
            if self.bid < 0 or self.ask < 0:
                raise ValueError("bid and ask cannot be negative")
            if self.ask < self.bid:
                raise ValueError("ask cannot be below bid")
        return self


class PayoffRequest(BaseModel):
    underlying_price: float
    legs: list[OptionLegInput] = Field(min_length=1)


class RiskCheckRequest(PayoffRequest):
    account_nav: float = 100_000.0
    risk_profile: RiskProfileName = "balanced"
    strategy: str = "custom"
    ticker: str = ""


class CandidateCreate(RiskCheckRequest):
    portfolio_id: int | None = None
    thesis: str = ""
    market_regime: str = ""
    volatility_regime: str = ""
    exit_plan: str = ""
    invalidation: str = ""
    probability_estimate: float | None = None


class ContractUpsert(BaseModel):
    underlying_symbol: str
    occ_symbol: str
    expiry: str
    right: OptionRight
    strike: float
    multiplier: int = 100
    exercise_style: str = "american"
    exchange: str = ""


class QuoteCreate(BaseModel):
    bid: float
    ask: float
    bid_size: int = 0
    ask_size: int = 0
    volume: int = 0
    open_interest: int = 0
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    rho: float | None = None
    source_id: str = "manual"

    @model_validator(mode="after")
    def quote_prices_valid(self) -> "QuoteCreate":
        if self.bid < 0 or self.ask < 0:
            raise ValueError("bid and ask cannot be negative")
        if self.ask < self.bid:
            raise ValueError("ask cannot be below bid")
        return self

