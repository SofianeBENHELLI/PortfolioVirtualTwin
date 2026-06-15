from __future__ import annotations


STRATEGY_TEMPLATES: list[dict] = [
    {
        "name": "long_call",
        "label": "Long Call",
        "allowed_regimes": ["bull_trend", "vol_compression", "risk_on"],
        "risk_level": "medium",
        "time_decay": "negative",
        "greeks": {"delta": "positive", "gamma": "positive", "vega": "positive", "theta": "negative"},
        "failure_modes": ["sideways price action", "iv_crush", "late entry"],
    },
    {
        "name": "long_put",
        "label": "Long Put",
        "allowed_regimes": ["bear_trend", "risk_off", "event_hedge"],
        "risk_level": "medium",
        "time_decay": "negative",
        "greeks": {"delta": "negative", "gamma": "positive", "vega": "positive", "theta": "negative"},
        "failure_modes": ["sharp rebound", "iv_crush", "slow drift"],
    },
    {
        "name": "debit_spread",
        "label": "Debit Spread",
        "allowed_regimes": ["directional", "moderate_iv"],
        "risk_level": "medium_low",
        "time_decay": "mild_negative",
        "greeks": {"delta": "directional", "gamma": "capped", "vega": "reduced", "theta": "mixed"},
        "failure_modes": ["move too small", "move too late", "bad strike target"],
    },
    {
        "name": "credit_spread",
        "label": "Credit Spread",
        "allowed_regimes": ["high_iv", "range_with_clear_invalidation"],
        "risk_level": "medium",
        "time_decay": "positive",
        "greeks": {"delta": "directional_or_neutral", "gamma": "negative", "vega": "negative", "theta": "positive"},
        "failure_modes": ["gap through short strike", "volatility spike", "liquidity exit shock"],
    },
    {
        "name": "iron_condor",
        "label": "Iron Condor",
        "allowed_regimes": ["range_bound", "high_iv", "calm_event_window"],
        "risk_level": "medium",
        "time_decay": "positive",
        "greeks": {"delta": "near_neutral", "gamma": "negative", "vega": "negative", "theta": "positive"},
        "failure_modes": ["trend breakout", "macro shock", "correlation spike"],
    },
    {
        "name": "calendar",
        "label": "Calendar Spread",
        "allowed_regimes": ["range_bound", "term_structure_edge"],
        "risk_level": "medium",
        "time_decay": "positive_near_strike",
        "greeks": {"delta": "localized", "gamma": "mixed", "vega": "positive", "theta": "positive_near_strike"},
        "failure_modes": ["large move away from strike", "term structure repricing"],
    },
    {
        "name": "straddle",
        "label": "Long Straddle",
        "allowed_regimes": ["low_iv", "breakout", "pre_event"],
        "risk_level": "medium_high",
        "time_decay": "strong_negative",
        "greeks": {"delta": "near_neutral", "gamma": "positive", "vega": "positive", "theta": "negative"},
        "failure_modes": ["no realized move", "iv_crush", "wide spread"],
    },
]

