"""Real (tracked) portfolios: manual holdings, live valuation, and the guarded
trading pipeline (arming + caps + CONFIRM + manual execution)."""
import pytest
from fastapi.testclient import TestClient

PRICES = {"AAPL": 120.0, "SPY": 400.0}

TWIN = {
    "strategy_name": "real-test",
    "universe": {"symbols": ["AAPL", "SPY"]},
    "exit_rules": [{"metric": "drawdown_from_entry", "op": ">", "value": 12}],
    "risk_management": {"max_position_weight_pct": 60, "max_sector_weight_pct": 100,
                        "max_number_of_positions": 10, "max_daily_loss_pct": 5,
                        "max_portfolio_drawdown_pct": 20, "max_orders_per_day": 10},
}


@pytest.fixture()
def client(db, monkeypatch):
    from app.execution import service as exec_service
    monkeypatch.setattr(exec_service, "latest_prices_for", lambda *a, **k: dict(PRICES))
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def auth(client):
    r = client.post("/api/auth/register", json={"email": "r@example.com", "password": "password1"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _create_real(client, auth, with_strategy=False) -> int:
    sid = None
    if with_strategy:
        sid = client.post("/api/strategies", json={"twin": TWIN}, headers=auth).json()["id"]
    r = client.post("/api/portfolios",
                    json={"kind": "real_tracked", "name": "My broker account", "strategy_id": sid},
                    headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "real_tracked" and body["broker"] == "manual"
    return body["id"]


def test_holdings_crud_and_valuation(client, auth):
    pid = _create_real(client, auth)
    r = client.post(f"/api/portfolios/{pid}/holdings",
                    json={"symbol": "aapl", "qty": 10, "avg_entry_price": 100.0}, headers=auth)
    assert r.status_code == 200

    s = client.get(f"/api/portfolios/{pid}/summary", headers=auth).json()
    assert s["kind"] == "real_tracked"
    assert s["equity"] == pytest.approx(1200.0)
    assert s["cost_basis"] == pytest.approx(1000.0)
    assert s["total_pnl"] == pytest.approx(200.0)

    assert client.delete(f"/api/portfolios/{pid}/holdings/AAPL", headers=auth).status_code == 200


def test_unarmed_real_portfolio_blocked_by_gateway(client, auth):
    pid = _create_real(client, auth, with_strategy=True)
    r = client.post(f"/api/portfolios/{pid}/proposals",
                    json={"symbol": "AAPL", "side": "buy", "qty": 1}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "risk_blocked"
    failed = {c["name"] for c in body["risk_checks"] if not c["passed"]}
    assert "live_armed" in failed


def test_arm_requires_checklist_then_full_real_cycle(client, auth):
    pid = _create_real(client, auth, with_strategy=True)

    # checklist passes (strategy has stop rule, caps default ok, no holdings)
    ready = client.get(f"/api/portfolios/{pid}/readiness", headers=auth).json()
    assert ready["all_passed"], ready
    assert client.post(f"/api/portfolios/{pid}/arm", headers=auth).status_code == 200

    # propose within the €1000 default notional cap
    r = client.post(f"/api/portfolios/{pid}/proposals",
                    json={"symbol": "AAPL", "side": "buy", "qty": 5}, headers=auth)  # 5*120=600
    body = r.json()
    assert body["status"] == "risk_passed", body["risk_checks"]
    assert body["risk_score"] is not None
    prop_id = body["id"]

    # approving WITHOUT typed CONFIRM is refused
    r = client.post(f"/api/portfolios/{pid}/proposals/{prop_id}/decision",
                    json={"decision": "approved"}, headers=auth)
    assert r.status_code == 428

    # with CONFIRM → order goes pending external (manual broker never auto-fills)
    r = client.post(f"/api/portfolios/{pid}/proposals/{prop_id}/decision",
                    json={"decision": "approved", "confirm_text": "CONFIRM"}, headers=auth)
    assert r.status_code == 200
    orders = client.get(f"/api/portfolios/{pid}/orders", headers=auth).json()
    assert orders[0]["status"] == "open" and orders[0]["broker"] == "manual"

    # record the real-world fill → position appears, cash untouched (external)
    r = client.post(f"/api/portfolios/{pid}/orders/{orders[0]['id']}/record-fill",
                    json={"filled_qty": 5, "fill_price": 119.0}, headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "filled"
    s = client.get(f"/api/portfolios/{pid}/summary", headers=auth).json()
    assert s["cash"] == 0.0
    assert any(p["symbol"] == "AAPL" and p["qty"] == 5 for p in s["positions"])


def test_notional_cap_blocks_large_real_order(client, auth):
    pid = _create_real(client, auth, with_strategy=True)
    client.post(f"/api/portfolios/{pid}/arm", headers=auth)
    r = client.post(f"/api/portfolios/{pid}/proposals",
                    json={"symbol": "AAPL", "side": "buy", "qty": 50}, headers=auth)  # 6000 > 1000 cap
    body = r.json()
    assert body["status"] == "risk_blocked"
    failed = {c["name"] for c in body["risk_checks"] if not c["passed"]}
    assert "order_notional_cap" in failed


def test_arm_refused_without_stop_rule(client, auth):
    twin = dict(TWIN, exit_rules=[], strategy_name="no-stop")
    sid = client.post("/api/strategies", json={"twin": twin}, headers=auth).json()["id"]
    r = client.post("/api/portfolios", json={"kind": "real_tracked", "strategy_id": sid}, headers=auth)
    pid = r.json()["id"]
    r = client.post(f"/api/portfolios/{pid}/arm", headers=auth)
    assert r.status_code == 409 and "stop" in r.json()["detail"].lower()


def test_kill_switch_cancels_disarms_blocks(client, auth):
    pid = _create_real(client, auth, with_strategy=True)
    client.post(f"/api/portfolios/{pid}/arm", headers=auth)
    r = client.post(f"/api/portfolios/{pid}/proposals",
                    json={"symbol": "AAPL", "side": "buy", "qty": 5}, headers=auth)
    prop_id = r.json()["id"]
    client.post(f"/api/portfolios/{pid}/proposals/{prop_id}/decision",
                json={"decision": "approved", "confirm_text": "CONFIRM"}, headers=auth)

    r = client.post("/api/kill-switch", json={"engage": True, "reason": "test"}, headers=auth)
    assert r.json()["engaged"] is True and r.json()["orders_cancelled"] == 1

    # portfolio disarmed and gateway blocks everything
    assert client.get("/api/portfolios", headers=auth).json()[0]["live_armed"] is False
    r = client.post(f"/api/portfolios/{pid}/proposals",
                    json={"symbol": "AAPL", "side": "buy", "qty": 1}, headers=auth)
    failed = {c["name"] for c in r.json()["risk_checks"] if not c["passed"]}
    assert "kill_switch" in failed

    # disengage clears the gate but portfolio must be re-armed explicitly
    client.post("/api/kill-switch", json={"engage": False}, headers=auth)
    r = client.post(f"/api/portfolios/{pid}/proposals",
                    json={"symbol": "AAPL", "side": "buy", "qty": 1}, headers=auth)
    failed = {c["name"] for c in r.json()["risk_checks"] if not c["passed"]}
    assert "kill_switch" not in failed and "live_armed" in failed


def test_paper_portfolio_cannot_edit_holdings(client, auth):
    r = client.post("/api/portfolios", json={"kind": "paper", "broker": "sim"}, headers=auth)
    pid = r.json()["id"]
    r = client.post(f"/api/portfolios/{pid}/holdings",
                    json={"symbol": "AAPL", "qty": 1, "avg_entry_price": 1.0}, headers=auth)
    assert r.status_code == 409


def test_invalid_kind_rejected(client, auth):
    assert client.post("/api/portfolios", json={"kind": "live"}, headers=auth).status_code == 422
