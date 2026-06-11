"""Alerts, audit log, market quotes, and the SSE event stream."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from pydantic import BaseModel

from app.audit.service import audit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.events import bus
from app.core.security import get_current_user
from app.data.provider import get_provider
from app.models import Alert, AuditLog, ExecutionEvent, PaperOrder, Portfolio, SystemState, User

router = APIRouter(prefix="/api", tags=["system"])


class KillSwitch(BaseModel):
    engage: bool
    reason: str = ""


def get_system_state(db: Session) -> SystemState:
    state = db.scalar(select(SystemState).limit(1))
    if state is None:
        state = SystemState()
        db.add(state)
        db.commit()
    return state


@router.get("/health")
def health():
    s = get_settings()
    return {"status": "ok", "mode": s.trading_mode,
            "shared_llm_configured": bool(s.openai_api_key),
            "alpaca_configured": bool(s.alpaca_api_key),
            "live_trading_enabled": s.live_trading_enabled}


@router.get("/kill-switch")
def kill_switch_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    state = get_system_state(db)
    return {"engaged": state.kill_switch_engaged, "reason": state.kill_switch_reason,
            "updated_at": state.updated_at.isoformat()}


@router.post("/kill-switch")
def kill_switch(payload: KillSwitch, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """ENGAGE: cancel every open order, disarm every real portfolio, halt all new
    proposals (gateway gate). DISENGAGE: clears the flag — portfolios stay disarmed
    and must be re-armed individually through the readiness checklist."""
    from app.execution.brokers import get_broker

    state = get_system_state(db)
    state.kill_switch_engaged = payload.engage
    state.kill_switch_reason = payload.reason

    cancelled = 0
    if payload.engage:
        open_orders = db.scalars(select(PaperOrder).where(PaperOrder.status == "open")).all()
        for order in open_orders:
            portfolio = db.get(Portfolio, order.portfolio_id)
            if portfolio.user_id != user.id:
                continue
            try:
                get_broker(portfolio.broker).cancel(order.broker_order_id)
            except Exception:
                pass  # cancel best-effort; order is force-cancelled locally either way
            order.status = "cancelled"
            db.add(ExecutionEvent(order_id=order.id, proposal_id=order.proposal_id,
                                  event="cancelled", detail={"reason": "kill switch engaged"}))
            cancelled += 1
        for p in db.scalars(select(Portfolio).where(Portfolio.user_id == user.id,
                                                    Portfolio.live_armed == True)):  # noqa: E712
            p.live_armed = False
        db.add(Alert(user_id=user.id, level="critical", kind="system",
                     title="KILL SWITCH ENGAGED",
                     body=f"All open orders cancelled ({cancelled}), real portfolios disarmed. "
                          f"Reason: {payload.reason or 'not given'}"))
    audit(db, "system.kill_switch", user_id=user.id,
          payload={"engage": payload.engage, "reason": payload.reason, "orders_cancelled": cancelled})
    db.commit()
    bus.publish("kill_switch", {"engaged": payload.engage})
    return {"engaged": state.kill_switch_engaged, "orders_cancelled": cancelled}


@router.get("/alerts")
def alerts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Alert).where(Alert.user_id == user.id)
                      .order_by(Alert.created_at.desc()).limit(100)).all()
    return [{"id": a.id, "level": a.level, "kind": a.kind, "title": a.title, "body": a.body,
             "acknowledged": a.acknowledged, "created_at": a.created_at.isoformat()} for a in rows]


@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if a is None or a.user_id != user.id:
        raise HTTPException(404, "Alert not found")
    a.acknowledged = True
    db.commit()
    return {"ok": True}


@router.get("/audit")
def audit_log(limit: int = Query(100, le=500), user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.user_id == user.id)
                      .order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return [{"id": r.id, "actor": r.actor, "action": r.action, "entity": r.entity,
             "entity_id": r.entity_id, "payload": r.payload,
             "created_at": r.created_at.isoformat()} for r in rows]


@router.get("/quotes")
def quotes(symbols: str, user: User = Depends(get_current_user)):
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:50]
    if not syms:
        raise HTTPException(422, "symbols query param required (comma-separated)")
    return get_provider().latest_prices(syms)


@router.get("/events")
async def events(user: User = Depends(get_current_user)):
    """SSE stream: proposal, order, fill, alert, portfolio, backtest, agent_run events."""
    return EventSourceResponse(bus.subscribe())
