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


def _debit_spread_payload(portfolio_id: int) -> dict:
    return {
        "portfolio_id": portfolio_id,
        "ticker": "SPY",
        "strategy": "debit_spread",
        "underlying_price": 101,
        "account_nav": 100_000,
        "thesis": "Defined-risk test spread.",
        "market_regime": "risk_on_trending",
        "volatility_regime": "normal_iv",
        "probability_estimate": 0.45,
        "legs": [
            {
                "action": "buy_to_open",
                "right": "call",
                "strike": 100,
                "expiry": "2026-07-17",
                "bid": 4.8,
                "ask": 5.0,
                "volume": 1000,
                "open_interest": 5000,
                "delta": 0.55,
                "gamma": 0.03,
                "vega": 0.12,
            },
            {
                "action": "sell_to_open",
                "right": "call",
                "strike": 110,
                "expiry": "2026-07-17",
                "bid": 1.9,
                "ask": 2.0,
                "volume": 1000,
                "open_interest": 5000,
                "delta": 0.28,
                "gamma": 0.02,
                "vega": 0.09,
            },
        ],
    }


def test_approve_option_candidate_fills_legs_and_updates_portfolio(client, auth):
    portfolio_id = client.post("/api/portfolios", json={"kind": "paper", "initial_cash": 100_000}, headers=auth).json()["id"]
    candidate = client.post("/api/options/candidates", json=_debit_spread_payload(portfolio_id), headers=auth).json()
    assert candidate["status"] == "risk_passed"

    approved = client.post(f"/api/options/candidates/{candidate['id']}/decision",
                           json={"decision": "approved", "note": "paper approve"}, headers=auth).json()
    assert approved["status"] == "filled"

    orders = client.get(f"/api/options/orders?portfolio_id={portfolio_id}", headers=auth).json()
    assert len(orders) == 1
    order = orders[0]
    assert order["status"] == "filled"
    assert order["intent"] == "open"
    assert order["net_debit"] == 310
    assert order["mid_value"] == 295
    assert order["commission"] == 1.3
    assert [leg["action"] for leg in order["legs"]] == ["buy_to_open", "sell_to_open"]

    positions = client.get(f"/api/options/positions?portfolio_id={portfolio_id}", headers=auth).json()
    assert len(positions) == 1
    position = positions[0]
    assert position["current_value"] == 295
    assert position["unrealized_pnl"] == pytest.approx(-16.3)

    summary = client.get(f"/api/portfolios/{portfolio_id}/summary", headers=auth).json()
    assert summary["cash"] == pytest.approx(99_688.7)
    assert summary["option_positions_value"] == pytest.approx(295)
    assert summary["equity"] == pytest.approx(99_983.7)
    assert summary["n_option_positions"] == 1


def test_close_option_position_realizes_pnl(client, auth):
    portfolio_id = client.post("/api/portfolios", json={"kind": "paper", "initial_cash": 100_000}, headers=auth).json()["id"]
    candidate = client.post("/api/options/candidates", json=_debit_spread_payload(portfolio_id), headers=auth).json()
    client.post(f"/api/options/candidates/{candidate['id']}/decision",
                json={"decision": "approved"}, headers=auth)
    position = client.get(f"/api/options/positions?portfolio_id={portfolio_id}", headers=auth).json()[0]

    closed = client.post(f"/api/options/positions/{position['id']}/close",
                         json={"decision": "approved", "note": "take off risk"}, headers=auth).json()
    assert closed["status"] == "closed"
    assert closed["realized_pnl"] == pytest.approx(-32.6)
    assert closed["current_value"] == 0

    open_positions = client.get(f"/api/options/positions?portfolio_id={portfolio_id}", headers=auth).json()
    assert open_positions == []
    all_positions = client.get(f"/api/options/positions?portfolio_id={portfolio_id}&status=", headers=auth).json()
    assert len(all_positions) == 1

    orders = client.get(f"/api/options/orders?portfolio_id={portfolio_id}", headers=auth).json()
    assert [o["intent"] for o in orders] == ["close", "open"]
    assert [leg["action"] for leg in orders[0]["legs"]] == ["sell_to_close", "buy_to_close"]

    summary = client.get(f"/api/portfolios/{portfolio_id}/summary", headers=auth).json()
    assert summary["cash"] == pytest.approx(99_967.4)
    assert summary["option_positions_value"] == 0
    assert summary["realized_pnl"] == pytest.approx(-32.6)


def test_blocked_option_candidate_cannot_be_approved(client, auth):
    portfolio_id = client.post("/api/portfolios", json={"kind": "paper", "initial_cash": 100_000}, headers=auth).json()["id"]
    payload = {
        "portfolio_id": portfolio_id,
        "ticker": "SPY",
        "strategy": "naked_short_call",
        "underlying_price": 500,
        "account_nav": 100_000,
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
    candidate = client.post("/api/options/candidates", json=payload, headers=auth).json()
    r = client.post(f"/api/options/candidates/{candidate['id']}/decision",
                    json={"decision": "approved"}, headers=auth)
    assert r.status_code == 409
