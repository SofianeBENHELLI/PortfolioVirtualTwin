import pytest
from fastapi.testclient import TestClient


SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "JPM", "LLY",
    "V", "UNH", "XOM", "MA", "COST", "HD", "PG", "NFLX", "AMD", "CRM",
    "ORCL", "ADBE", "QCOM", "CSCO", "INTU",
]


def _fake_data(symbols, benchmark):
    out = {}
    for i, sym in enumerate(symbols):
        rank = i + 1
        out[sym] = {
            "price": 100.0,
            "indicators": {
                "momentum_6m_return_pct": rank * 1.5,
                "relative_strength": rank * 0.8,
                "price_above_200_day_average": True,
                "price_above_50_day_average": True,
                "rsi_14": 58.0,
                "volatility_30d": 18.0 + rank * 0.4,
                "volume_confirmation": True,
            },
            "fundamentals": {
                "sector": "Technology" if rank % 3 else "Healthcare",
                "revenueGrowth": 0.08 + rank * 0.004,
                "earningsGrowth": 0.10 + rank * 0.003,
                "profitMargins": 0.18,
                "grossMargins": 0.62,
                "debtToEquity": 35.0,
                "freeCashflow": 1_000_000_000,
                "beta": 0.9 + rank * 0.01,
                "targetMeanPrice": 100.0 * (1.03 + rank * 0.01),
                "forwardPE": 24.0,
            },
        }
    return out


@pytest.fixture()
def client(db, monkeypatch):
    from app.agents import graphs

    monkeypatch.setattr(graphs, "gather_symbol_data", _fake_data)
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def auth(client):
    r = client.post("/api/auth/register", json={"email": "d@example.com", "password": "password1"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_stock_discovery_returns_top_20_with_target_and_risk(client, auth, db):
    from app.models import MacroSnapshot, Recommendation

    db.add(MacroSnapshot(regimes={"risk_off": False, "volatility_regime": "calm", "war_risk": "low"}))
    db.commit()
    strategy = client.post("/api/strategies", json={"twin": {
        "strategy_name": "quality growth discovery",
        "universe": {"symbols": SYMBOLS},
        "investment_thesis": {"style": "quality growth momentum", "horizon": "6 months"},
        "entry_rules": [
            {"metric": "quality_score", "op": ">=", "value": 55},
            {"metric": "price_above_200_day_average", "op": "==", "value": True},
        ],
        "risk_management": {"max_position_weight_pct": 8, "max_sector_weight_pct": 25,
                            "max_portfolio_drawdown_pct": 15, "max_daily_loss_pct": 3,
                            "max_number_of_positions": 20, "max_orders_per_day": 20},
    }}, headers=auth).json()

    r = client.post("/api/agents/stock-discovery",
                    json={"strategy_id": strategy["id"], "limit": 20}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["graph"] == "stock_discovery"
    assert body["status"] == "done"
    recs = body["recommendations"]
    assert len(recs) == 20
    assert recs[0]["symbol"] == "INTU"  # highest modeled target/score in fixture
    assert recs[0]["action"] == "track"
    assert recs[0]["target_growth_pct"] > recs[-1]["target_growth_pct"]
    assert recs[0]["risk_level"] in ("low", "moderate", "elevated", "high")
    assert recs[0]["data_used"]["perspective"] == "stock_discovery"
    assert recs[0]["data_used"]["target_growth_pct"] > recs[-1]["data_used"]["target_growth_pct"]
    assert recs[0]["data_used"]["risk_level"] in ("low", "moderate", "elevated", "high")
    assert "strategy_fit" in recs[0]["data_used"]["scores"]

    persisted = db.query(Recommendation).filter_by(agent_run_id=body["id"]).all()
    assert len(persisted) == 20


def test_stock_discovery_can_add_proposals_to_watchlist(client, auth):
    r = client.post("/api/agents/stock-discovery",
                    json={"symbols": SYMBOLS[:5], "limit": 3, "add_to_watchlist": True}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert len(body["recommendations"]) == 3
    assert len(body["added_to_watchlist"]) == 3

    watched = client.get("/api/watchlist", headers=auth).json()
    assert {row["symbol"] for row in watched} == set(body["added_to_watchlist"])


def test_stock_discovery_validates_limit(client, auth):
    r = client.post("/api/agents/stock-discovery", json={"symbols": SYMBOLS[:3], "limit": 0}, headers=auth)
    assert r.status_code == 422
