"""End-to-end pipeline against the sim broker with mocked market prices:
proposal → risk gateway → approval → fill → position/P&L → portfolio summary."""
import pytest

from app.core.security import hash_password
from app.execution import service as exec_service
from app.models import Approval, AuditLog, Portfolio, RiskCheck, User
from app.portfolio import service as portfolio_service
from app.strategy import service as strategy_service
from app.strategy.twin import StrategyTwin

PRICES = {"AAPL": 100.0, "SPY": 400.0}


@pytest.fixture()
def world(db, monkeypatch):
    monkeypatch.setattr(exec_service, "latest_prices_for", lambda *a, **k: dict(PRICES))
    user = User(email="t@t.com", password_hash=hash_password("password1"))
    db.add(user)
    db.commit()
    twin = StrategyTwin.model_validate({
        "strategy_name": "test", "universe": {"symbols": ["AAPL", "SPY"]},
        "risk_management": {"max_position_weight_pct": 50, "max_sector_weight_pct": 100,
                            "max_number_of_positions": 10, "max_daily_loss_pct": 5,
                            "max_portfolio_drawdown_pct": 20, "max_orders_per_day": 10},
    })
    strategy = strategy_service.create_strategy(db, user.id, twin)
    portfolio = Portfolio(user_id=user.id, broker="sim", initial_cash=100_000.0, cash=100_000.0,
                          strategy_id=strategy.id)
    db.add(portfolio)
    db.commit()
    version, twin = strategy_service.active_twin(db, user.id, strategy.id)
    return db, user, portfolio, twin, version


def test_full_buy_sell_cycle(world):
    db, user, portfolio, twin, version = world

    # 1. propose a buy — gateway runs and persists checks
    proposal = exec_service.create_proposal(db, user.id, portfolio, twin, version.id,
                                            symbol="AAPL", side="buy", qty=100,
                                            rationale="test buy")
    assert proposal.status == "risk_passed"
    checks = db.query(RiskCheck).filter_by(proposal_id=proposal.id).all()
    assert len(checks) >= 10 and all(c.passed for c in checks)

    # 2. approve — sim broker fills at market
    proposal = exec_service.decide(db, user.id, proposal, "approved", "looks good")
    assert proposal.status == "filled"
    db.refresh(portfolio)
    assert portfolio.cash == pytest.approx(100_000 - 100 * 100.0)

    summary = portfolio_service.summary(db, portfolio, PRICES)
    assert summary["n_positions"] == 1
    assert summary["equity"] == pytest.approx(100_000.0)

    # 3. price moves up, sell half → realized P&L
    PRICES["AAPL"] = 110.0
    proposal2 = exec_service.create_proposal(db, user.id, portfolio, twin, version.id,
                                             symbol="AAPL", side="sell", qty=50)
    assert proposal2.status == "risk_passed"
    exec_service.decide(db, user.id, proposal2, "approved")
    db.refresh(portfolio)

    summary = portfolio_service.summary(db, portfolio, PRICES)
    assert summary["realized_pnl"] == pytest.approx(50 * 10.0)
    assert summary["unrealized_pnl"] == pytest.approx(50 * 10.0)
    assert summary["equity"] == pytest.approx(101_000.0)
    assert summary["total_pnl"] == pytest.approx(1_000.0)

    PRICES["AAPL"] = 100.0  # reset module-level dict for other tests


def test_rejected_proposal_never_reaches_broker(world):
    db, user, portfolio, twin, version = world
    proposal = exec_service.create_proposal(db, user.id, portfolio, twin, version.id,
                                            symbol="AAPL", side="buy", qty=10)
    proposal = exec_service.decide(db, user.id, proposal, "rejected", "not today")
    assert proposal.status == "rejected"
    from app.models import PaperOrder
    assert db.query(PaperOrder).count() == 0


def test_risk_blocked_proposal_cannot_be_approved(world):
    db, user, portfolio, twin, version = world
    # excluded via whitelist: TSLA not in universe
    proposal = exec_service.create_proposal(db, user.id, portfolio, twin, version.id,
                                            symbol="TSLA", side="buy", qty=1)
    assert proposal.status == "risk_blocked"
    with pytest.raises(Exception):
        exec_service.decide(db, user.id, proposal, "approved")


def test_audit_trail_written(world):
    db, user, portfolio, twin, version = world
    proposal = exec_service.create_proposal(db, user.id, portfolio, twin, version.id,
                                            symbol="AAPL", side="buy", qty=1)
    exec_service.decide(db, user.id, proposal, "approved")
    actions = {a.action for a in db.query(AuditLog).all()}
    assert {"order.proposed", "risk.gateway_run", "order.approved",
            "order.submitted", "order.filled"} <= actions
    approval = db.query(Approval).filter_by(proposal_id=proposal.id).one()
    assert approval.decision == "approved"
