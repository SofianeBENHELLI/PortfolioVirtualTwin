import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(db):
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def auth(client):
    r = client.post("/api/auth/register", json={"email": "o@example.com", "password": "password1"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_strategy_templates_endpoint(client, auth):
    r = client.get("/api/options/strategy-templates", headers=auth)
    assert r.status_code == 200
    names = {row["name"] for row in r.json()}
    assert {"long_call", "debit_spread", "iron_condor"} <= names


def test_candidate_creation_persists_risk_block(client, auth):
    payload = {
        "ticker": "SPY",
        "strategy": "naked_short_call",
        "underlying_price": 500,
        "account_nav": 100_000,
        "thesis": "Test candidate should be blocked.",
        "legs": [
            {
                "action": "sell_to_open",
                "right": "call",
                "strike": 510,
                "expiry": "2026-07-17",
                "bid": 6.0,
                "ask": 6.2,
                "volume": 1000,
                "open_interest": 5000,
            }
        ],
    }
    r = client.post("/api/options/candidates", json=payload, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "risk_blocked"
    assert body["risk_passed"] is False
    assert body["memo"]["risk_agent_status"] == "blocked"

    rows = client.get("/api/options/candidates", headers=auth).json()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "SPY"


def test_contract_and_quote_roundtrip(client, auth):
    contract = {
        "underlying_symbol": "SPY",
        "occ_symbol": "SPY260717C00510000",
        "expiry": "2026-07-17",
        "right": "call",
        "strike": 510,
    }
    r = client.post("/api/options/contracts", json=contract, headers=auth)
    assert r.status_code == 200
    assert r.json()["occ_symbol"] == "SPY260717C00510000"

    quote = {"bid": 5.8, "ask": 6.0, "volume": 800, "open_interest": 3200, "delta": 0.45}
    r = client.post("/api/options/contracts/SPY260717C00510000/quotes", json=quote, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["mid"] == 5.9
    assert body["spread"] == pytest.approx(0.2)

    rows = client.get("/api/options/contracts?underlying=SPY", headers=auth).json()
    assert len(rows) == 1
