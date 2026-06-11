"""Watchlist endpoints with mocked market data and mocked LLM-produced signals."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(db, monkeypatch):
    from app.routers import watchlist as wl

    monkeypatch.setattr(wl, "gather_symbol_data", lambda symbols, benchmark: {
        s: {"price": 100.0, "indicators": {"rsi_14": 55.0, "volume_confirmation": True},
            "fundamentals": {"sector": "Technology", "trailingPE": 30.1}}
        for s in symbols
    })
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def auth(client):
    r = client.post("/api/auth/register", json={"email": "w@example.com", "password": "password1"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_add_refresh_and_list(client, auth):
    assert client.post("/api/watchlist", json={"symbol": "aapl"}, headers=auth).status_code == 200
    assert client.post("/api/watchlist", json={"symbol": "AAPL"}, headers=auth).status_code == 409
    assert client.post("/api/watchlist", json={"symbol": "not a symbol!"}, headers=auth).status_code == 422

    r = client.post("/api/watchlist/refresh", headers=auth)
    assert r.status_code == 200 and r.json()["refreshed"] == ["AAPL"]

    rows = client.get("/api/watchlist", headers=auth).json()
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "AAPL" and row["price"] == 100.0
    assert row["fundamentals"]["sector"] == "Technology"
    assert row["indicators"]["rsi_14"] == 55.0
    assert row["bull"] is None and row["bear"] is None


def test_signals_joined_per_symbol(client, auth, db):
    client.post("/api/watchlist", json={"symbol": "AAPL"}, headers=auth)
    from app.models import AgentRun, Recommendation, User
    user = db.query(User).filter_by(email="w@example.com").one()
    run = AgentRun(user_id=user.id, graph="bullbear", status="done")
    db.add(run)
    db.flush()
    for perspective, action, strength in (("bull", "buy", 72.0), ("bear", "sell", 38.0)):
        db.add(Recommendation(agent_run_id=run.id, user_id=user.id, symbol="AAPL", action=action,
                              confidence=strength / 100, thesis=f"{perspective} case",
                              invalidation="x",
                              data_used={"perspective": perspective, "signal_strength": strength,
                                         "key_points": ["a", "b"]}))
    db.commit()

    row = client.get("/api/watchlist", headers=auth).json()[0]
    assert row["bull"]["signal_strength"] == 72.0
    assert row["bear"]["signal_strength"] == 38.0
    assert row["bull"]["thesis"] == "bull case"


def test_remove(client, auth):
    client.post("/api/watchlist", json={"symbol": "MSFT"}, headers=auth)
    assert client.delete("/api/watchlist/MSFT", headers=auth).status_code == 200
    assert client.get("/api/watchlist", headers=auth).json() == []


def test_refresh_empty_watchlist_409(client, auth):
    assert client.post("/api/watchlist/refresh", headers=auth).status_code == 409


def test_bullbear_503_without_llm_key(client, auth):
    client.post("/api/watchlist", json={"symbol": "AAPL"}, headers=auth)
    r = client.post("/api/watchlist/bullbear", json={"strategy_id": 1}, headers=auth)
    assert r.status_code == 503  # no OPENAI_API_KEY in tests
