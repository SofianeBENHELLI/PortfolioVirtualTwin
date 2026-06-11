"""Background monitor: refreshes prices, polls open orders, snapshots equity, and
evaluates exit-rule / risk-limit alerts. Runs as a single asyncio task in-process —
adequate for ≤10 users, no external queue needed."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.audit.service import audit
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.events import bus
from app.execution import service as exec_service
from app.models import Alert, MarketDataSnapshot, Portfolio, PortfolioSnapshot, Position, Strategy, StrategyVersion
from app.portfolio import service as portfolio_service
from app.strategy.twin import StrategyTwin

log = logging.getLogger("pvt.monitor")


def _alert_once(db, user_id: int, portfolio_id: int, level: str, kind: str, title: str, body: str) -> None:
    """De-duplicate: skip if an unacknowledged alert with the same title exists."""
    existing = db.scalar(select(Alert).where(Alert.user_id == user_id, Alert.title == title,
                                             Alert.acknowledged == False))  # noqa: E712
    if existing:
        return
    db.add(Alert(user_id=user_id, portfolio_id=portfolio_id, level=level, kind=kind,
                 title=title, body=body))
    audit(db, "alert.raised", user_id=user_id, actor="system", entity="alert",
          payload={"kind": kind, "title": title})
    bus.publish("alert", {"level": level, "kind": kind, "title": title})


def tick() -> None:
    """One monitor pass over every portfolio. Synchronous; runs in a worker thread."""
    db = SessionLocal()
    try:
        portfolios = db.scalars(select(Portfolio)).all()
        for pf in portfolios:
            try:
                exec_service.poll_open_orders(db, pf)
                prices = exec_service.latest_prices_for(db, pf)
                for sym, price in prices.items():
                    snap = db.scalar(select(MarketDataSnapshot).where(MarketDataSnapshot.symbol == sym))
                    if snap is None:
                        db.add(MarketDataSnapshot(symbol=sym, price=price))
                    else:
                        snap.price = price
                        snap.as_of = datetime.now(timezone.utc)
                summary = portfolio_service.summary(db, pf, prices)
                db.add(PortfolioSnapshot(portfolio_id=pf.id, equity=summary["equity"], cash=pf.cash))
                db.commit()
                _evaluate_risk(db, pf, summary)
                db.commit()
                bus.publish("portfolio", {"portfolio_id": pf.id, "equity": summary["equity"],
                                          "daily_pnl": summary["daily_pnl"]})
            except Exception:
                db.rollback()
                log.exception("monitor pass failed for portfolio %s", pf.id)
    finally:
        db.close()


def _evaluate_risk(db, pf: Portfolio, summary: dict) -> None:
    if pf.strategy_id is None:
        return
    strategy = db.get(Strategy, pf.strategy_id)
    if strategy is None or strategy.active_version_id is None:
        return
    version = db.get(StrategyVersion, strategy.active_version_id)
    twin = StrategyTwin.model_validate(version.twin)
    rm = twin.risk_management

    if summary["drawdown_pct"] >= rm.max_portfolio_drawdown_pct:
        _alert_once(db, pf.user_id, pf.id, "critical", "risk_limit",
                    f"Max drawdown limit breached ({summary['drawdown_pct']:.1f}%)",
                    f"Strategy limit is {rm.max_portfolio_drawdown_pct}%. New buy orders are blocked by the risk gateway.")
    elif summary["drawdown_pct"] >= rm.max_portfolio_drawdown_pct * 0.8:
        _alert_once(db, pf.user_id, pf.id, "warning", "risk_limit",
                    f"Drawdown approaching limit ({summary['drawdown_pct']:.1f}%)",
                    f"Limit: {rm.max_portfolio_drawdown_pct}%.")

    if summary["daily_pnl_pct"] <= -rm.max_daily_loss_pct:
        _alert_once(db, pf.user_id, pf.id, "critical", "risk_limit",
                    f"Daily loss limit hit ({summary['daily_pnl_pct']:.2f}%)",
                    f"Strategy limit is {rm.max_daily_loss_pct}%. Buys blocked until tomorrow.")

    for sector, weight in summary["sector_weights"].items():
        if weight > rm.max_sector_weight_pct:
            _alert_once(db, pf.user_id, pf.id, "warning", "risk_limit",
                        f"Sector concentration: {sector} at {weight:.1f}%",
                        f"Limit: {rm.max_sector_weight_pct}%. Consider trimming or hedging with a sector ETF.")

    # exit-rule monitoring: drawdown_from_entry per position
    for cond in twin.exit_rules:
        if cond.metric != "drawdown_from_entry":
            continue
        try:
            threshold = float(cond.value)
        except (TypeError, ValueError):
            continue
        for p in summary["positions"]:
            dd = -p["unrealized_pnl_pct"]
            if dd >= threshold:
                _alert_once(db, pf.user_id, pf.id, "critical", "exit_rule",
                            f"Exit rule hit: {p['symbol']} down {dd:.1f}% from entry",
                            f"Rule: {cond.describe()}. Propose a sell order from the Trading Console.")

    # hedge triggers: volatility_regime / sector_concentration evaluable now
    _evaluate_hedge_triggers(db, pf, twin, summary)


def _evaluate_hedge_triggers(db, pf: Portfolio, twin: StrategyTwin, summary: dict) -> None:
    from app.models import MacroSnapshot
    macro = db.scalar(select(MacroSnapshot).order_by(MacroSnapshot.as_of.desc()).limit(1))
    regimes = macro.regimes if macro else {}
    max_sector = max(summary["sector_weights"].values(), default=0.0)
    observed_map = {
        "volatility_regime": regimes.get("volatility_regime"),
        "sector_concentration": max_sector,
    }
    instruments = ", ".join(twin.hedging.allowed_instruments) or "index ETF / cash"
    for cond in twin.hedging.hedge_triggers:
        observed = observed_map.get(cond.metric)
        if observed is None:
            continue
        if cond.evaluate(observed):
            _alert_once(db, pf.user_id, pf.id, "warning", "hedge_trigger",
                        f"Hedge trigger: {cond.describe()} (observed: {observed})",
                        f"Strategy hedging policy suggests protection via {instruments}. "
                        f"Propose a hedge from the Trading Console.")


def _nag_pending_manual_orders(db) -> None:
    """Manual (real) orders left unexecuted for >4h get a reminder alert."""
    from app.models import PaperOrder
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    stale = db.scalars(select(PaperOrder).where(PaperOrder.broker == "manual",
                                                PaperOrder.status == "open")).all()
    for order in stale:
        created = order.created_at if order.created_at.tzinfo else order.created_at.replace(tzinfo=timezone.utc)
        if created < cutoff:
            pf = db.get(Portfolio, order.portfolio_id)
            _alert_once(db, pf.user_id, pf.id, "warning", "pending_execution",
                        f"Real order #{order.id} ({order.side} {order.qty} {order.symbol}) still pending",
                        "You approved this order but no fill has been recorded. Execute it at your "
                        "broker and record the fill, or cancel it in the Trading Console.")
    db.commit()


def macro_tick() -> None:
    """Refresh macro data for all users (snapshot is global; alerts go to each user)."""
    from app.models import User
    from app.routers.macro import refresh_macro
    db = SessionLocal()
    try:
        users = db.scalars(select(User)).all()
        if not users:
            return
        refresh_macro(db, user_id=users[0].id, actor="system")
    except Exception:
        log.exception("macro refresh failed")
    finally:
        db.close()


async def monitor_loop() -> None:
    settings = get_settings()
    interval = settings.quote_refresh_seconds
    macro_interval = max(1, settings.macro_refresh_hours) * 3600
    await asyncio.sleep(3)  # let the app finish booting
    last_macro = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            await asyncio.to_thread(tick)
            db = SessionLocal()
            try:
                _nag_pending_manual_orders(db)
            finally:
                db.close()
            if loop.time() - last_macro >= macro_interval:
                last_macro = loop.time()
                await asyncio.to_thread(macro_tick)
        except Exception:
            log.exception("monitor tick crashed")
        await asyncio.sleep(interval)
