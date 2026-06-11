"""Broker abstraction. MVP 1 contains ONLY paper implementations — there is no code
path that can reach a live trading endpoint. AlpacaPaperBroker hardcodes paper=True."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.config import get_settings


@dataclass
class BrokerOrderResult:
    broker_order_id: str
    status: str               # open | filled | rejected
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    detail: str = ""


class BrokerProtocol(Protocol):
    name: str

    def submit(self, *, symbol: str, side: str, qty: float, order_type: str,
               limit_price: float | None, market_price: float | None) -> BrokerOrderResult: ...

    def poll(self, broker_order_id: str, market_price: float | None) -> BrokerOrderResult: ...

    def cancel(self, broker_order_id: str) -> bool: ...


class SimBroker:
    """Internal fill simulator. Market orders fill immediately at the latest price;
    limit orders fill when the market price crosses the limit (checked on submit and
    on every monitor poll). Deterministic, no external dependency."""

    name = "sim"
    _seq = 0

    def submit(self, *, symbol: str, side: str, qty: float, order_type: str,
               limit_price: float | None, market_price: float | None) -> BrokerOrderResult:
        SimBroker._seq += 1
        oid = f"sim-{SimBroker._seq}"
        if market_price is None:
            return BrokerOrderResult(oid, "rejected", detail="no market price available")
        if order_type == "market":
            return BrokerOrderResult(oid, "filled", filled_qty=qty, filled_avg_price=market_price)
        # limit
        if limit_price is None:
            return BrokerOrderResult(oid, "rejected", detail="limit order without limit price")
        if self._crosses(side, limit_price, market_price):
            return BrokerOrderResult(oid, "filled", filled_qty=qty, filled_avg_price=market_price)
        return BrokerOrderResult(oid, "open", detail="limit not reached")

    def poll(self, broker_order_id: str, market_price: float | None) -> BrokerOrderResult:
        # state lives in the PaperOrder row; the execution service re-checks limits there
        return BrokerOrderResult(broker_order_id, "open")

    def cancel(self, broker_order_id: str) -> bool:
        return True

    @staticmethod
    def _crosses(side: str, limit_price: float, market_price: float) -> bool:
        return market_price <= limit_price if side == "buy" else market_price >= limit_price


class AlpacaPaperBroker:
    """Alpaca paper trading. SAFETY: paper=True is hardcoded and not configurable."""

    name = "alpaca_paper"

    def __init__(self) -> None:
        from alpaca.trading.client import TradingClient

        s = get_settings()
        if not s.alpaca_api_key or not s.alpaca_secret_key:
            raise RuntimeError("Alpaca API keys not configured (ALPACA_API_KEY / ALPACA_SECRET_KEY)")
        self._client = TradingClient(s.alpaca_api_key, s.alpaca_secret_key, paper=True)

    def submit(self, *, symbol: str, side: str, qty: float, order_type: str,
               limit_price: float | None, market_price: float | None) -> BrokerOrderResult:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        if order_type == "limit":
            req = LimitOrderRequest(symbol=symbol, qty=qty, side=order_side,
                                    time_in_force=TimeInForce.DAY, limit_price=limit_price)
        else:
            req = MarketOrderRequest(symbol=symbol, qty=qty, side=order_side,
                                     time_in_force=TimeInForce.DAY)
        try:
            order = self._client.submit_order(req)
        except Exception as exc:  # broker rejection is a normal outcome, surface it
            return BrokerOrderResult("", "rejected", detail=str(exc))
        return self._to_result(order)

    def poll(self, broker_order_id: str, market_price: float | None) -> BrokerOrderResult:
        order = self._client.get_order_by_id(broker_order_id)
        return self._to_result(order)

    def cancel(self, broker_order_id: str) -> bool:
        try:
            self._client.cancel_order_by_id(broker_order_id)
            return True
        except Exception:
            return False

    @staticmethod
    def _to_result(order) -> BrokerOrderResult:
        status = str(order.status.value if hasattr(order.status, "value") else order.status)
        mapped = {"filled": "filled", "canceled": "cancelled", "rejected": "rejected",
                  "expired": "cancelled"}.get(status, "open")
        return BrokerOrderResult(
            str(order.id), mapped,
            filled_qty=float(order.filled_qty or 0),
            filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price else None,
            detail=status,
        )


_sim = SimBroker()


def get_broker(name: str) -> BrokerProtocol:
    if name == "alpaca_paper":
        return AlpacaPaperBroker()
    return _sim
