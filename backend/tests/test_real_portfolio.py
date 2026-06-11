"""Real (tracked) portfolios: manual holdings, live valuation, and the hard rule
that they can never be traded from the app."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(db, monkeypatch):
    from app.execution import service as exec_service
    monkeypatch.setattr(exec_service, "latest_prices_for", lambda *a, **k: {"AAPL": 120.0})
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def auth(client):
    r = client.post("/api/auth/register", json={"email": "r@example.com", "password": "password1"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _create_real(client, auth) -> int:
    r = client.post("/api/portfolios", json={"kind": "real_tracked", "name": "My broker account"},
                    headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "real_tracked" and body["broker"] == "none"
    return body["id"]


def test_holdings_crud_and_valuation(client, auth):
    pid = _create_real(client, auth)
    r = client.post(f"/api/portfolios/{pid}/holdings",
                    json={"symbol": "aapl", "qty": 10, "avg_entry_price": 100.0}, headers=auth)
    assert r.status_code == 200

    s = client.get(f"/api/portfolios/{pid}/summary", headers=auth).json()
    assert s["kind"] == "real_tracked"
    assert s["equity"] == pytest.approx(1200.0)        # 10 @ 120 live
    assert s["cost_basis"] == pytest.approx(1000.0)
    assert s["total_pnl"] == pytest.approx(200.0)      # vs cost basis, not initial_cash
    assert s["cash"] == 0.0

    assert client.delete(f"/api/portfolios/{pid}/holdings/AAPL", headers=auth).status_code == 200
    s = client.get(f"/api/portfolios/{pid}/summary", headers=auth).json()
    assert s["equity"] == 0.0


def test_real_portfolio_cannot_trade(client, auth):
    pid = _create_real(client, auth)
    r = client.post(f"/api/portfolios/{pid}/proposals",
                    json={"symbol": "AAPL", "side": "buy", "qty": 1}, headers=auth)
    assert r.status_code == 403
    assert "never trades real holdings" in r.json()["detail"]


def test_paper_portfolio_cannot_edit_holdings(client, auth):
    r = client.post("/api/portfolios", json={"kind": "paper", "broker": "sim"}, headers=auth)
    pid = r.json()["id"]
    r = client.post(f"/api/portfolios/{pid}/holdings",
                    json={"symbol": "AAPL", "qty": 1, "avg_entry_price": 1.0}, headers=auth)
    assert r.status_code == 409


def test_invalid_kind_rejected(client, auth):
    r = client.post("/api/portfolios", json={"kind": "live"}, headers=auth)
    assert r.status_code == 422
