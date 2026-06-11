from app.execution.brokers import SimBroker


def test_market_order_fills_at_market():
    b = SimBroker()
    r = b.submit(symbol="AAPL", side="buy", qty=10, order_type="market",
                 limit_price=None, market_price=150.0)
    assert r.status == "filled" and r.filled_avg_price == 150.0 and r.filled_qty == 10


def test_market_order_rejected_without_price():
    r = SimBroker().submit(symbol="AAPL", side="buy", qty=10, order_type="market",
                           limit_price=None, market_price=None)
    assert r.status == "rejected"


def test_buy_limit_fills_only_at_or_below_limit():
    b = SimBroker()
    open_r = b.submit(symbol="AAPL", side="buy", qty=5, order_type="limit",
                      limit_price=100.0, market_price=110.0)
    assert open_r.status == "open"
    fill_r = b.submit(symbol="AAPL", side="buy", qty=5, order_type="limit",
                      limit_price=100.0, market_price=99.0)
    assert fill_r.status == "filled"


def test_sell_limit_fills_only_at_or_above_limit():
    b = SimBroker()
    assert b.submit(symbol="AAPL", side="sell", qty=5, order_type="limit",
                    limit_price=120.0, market_price=110.0).status == "open"
    assert b.submit(symbol="AAPL", side="sell", qty=5, order_type="limit",
                    limit_price=120.0, market_price=125.0).status == "filled"


def test_limit_without_price_rejected():
    r = SimBroker().submit(symbol="AAPL", side="buy", qty=5, order_type="limit",
                           limit_price=None, market_price=100.0)
    assert r.status == "rejected"
