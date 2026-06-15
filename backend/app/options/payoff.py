from __future__ import annotations

from dataclasses import dataclass

from app.options.schemas import OptionLegInput


BUY_ACTIONS = {"buy", "buy_to_open"}


@dataclass(frozen=True)
class PayoffSummary:
    net_debit: float
    max_loss: float | None
    max_gain: float | None
    breakevens: list[float]
    pnl_points: list[dict]
    unlimited_loss: bool
    unlimited_gain: bool


def leg_sign(leg: OptionLegInput) -> int:
    return 1 if leg.action in BUY_ACTIONS else -1


def entry_price(leg: OptionLegInput) -> float:
    if leg.premium is not None:
        return leg.premium
    if leg.action in BUY_ACTIONS:
        return float(leg.ask or 0.0)
    return float(leg.bid or 0.0)


def mid_price(leg: OptionLegInput) -> float:
    if leg.bid is not None and leg.ask is not None:
        return (leg.bid + leg.ask) / 2
    return entry_price(leg)


def spread_pct_mid(leg: OptionLegInput) -> float:
    if leg.bid is None or leg.ask is None:
        return 0.0
    mid = mid_price(leg)
    if mid <= 0:
        return 1.0
    return (leg.ask - leg.bid) / mid


def intrinsic_value(leg: OptionLegInput, underlying_price: float) -> float:
    if leg.right == "call":
        return max(0.0, underlying_price - leg.strike)
    return max(0.0, leg.strike - underlying_price)


def net_debit(legs: list[OptionLegInput]) -> float:
    return sum(leg_sign(leg) * entry_price(leg) * leg.qty * leg.multiplier for leg in legs)


def pnl_at_expiry(legs: list[OptionLegInput], underlying_price: float) -> float:
    value = sum(
        leg_sign(leg) * intrinsic_value(leg, underlying_price) * leg.qty * leg.multiplier
        for leg in legs
    )
    return value - net_debit(legs)


def call_slope_at_infinity(legs: list[OptionLegInput]) -> float:
    return sum(
        leg_sign(leg) * leg.qty * leg.multiplier
        for leg in legs
        if leg.right == "call"
    )


def price_grid(legs: list[OptionLegInput], underlying_price: float) -> list[float]:
    strikes = sorted({leg.strike for leg in legs})
    high = max(max(strikes) * 2, underlying_price * 2, 1.0)
    anchors = {0.0, underlying_price, high, *strikes}
    for strike in strikes:
        anchors.add(max(0.0, strike - 0.01))
        anchors.add(strike + 0.01)
    return sorted(anchors)


def breakevens_from_grid(points: list[tuple[float, float]]) -> list[float]:
    breakevens: list[float] = []
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        if abs(y1) < 1e-9:
            breakevens.append(round(x1, 4))
            continue
        if y1 * y2 < 0 and abs(y2 - y1) > 1e-9:
            x = x1 + (0 - y1) * (x2 - x1) / (y2 - y1)
            breakevens.append(round(x, 4))
    if points and abs(points[-1][1]) < 1e-9:
        breakevens.append(round(points[-1][0], 4))
    return sorted(set(breakevens))


def summarize_payoff(legs: list[OptionLegInput], underlying_price: float) -> PayoffSummary:
    points = [(price, pnl_at_expiry(legs, price)) for price in price_grid(legs, underlying_price)]
    finite_min = min(pnl for _, pnl in points)
    finite_max = max(pnl for _, pnl in points)
    slope = call_slope_at_infinity(legs)
    unlimited_gain = slope > 0
    unlimited_loss = slope < 0
    max_loss = None if unlimited_loss else max(0.0, -finite_min)
    max_gain = None if unlimited_gain else max(0.0, finite_max)
    return PayoffSummary(
        net_debit=round(net_debit(legs), 2),
        max_loss=round(max_loss, 2) if max_loss is not None else None,
        max_gain=round(max_gain, 2) if max_gain is not None else None,
        breakevens=breakevens_from_grid(points),
        pnl_points=[{"underlying_price": round(price, 4), "pnl": round(pnl, 2)} for price, pnl in points],
        unlimited_loss=unlimited_loss,
        unlimited_gain=unlimited_gain,
    )

