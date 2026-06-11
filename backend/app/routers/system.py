"""Alerts, audit log, market quotes, and the SSE event stream."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.agents.llm import llm_available
from app.core.config import get_settings
from app.core.db import get_db
from app.core.events import bus
from app.core.security import get_current_user
from app.data.provider import get_provider
from app.models import Alert, AuditLog, User

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    s = get_settings()
    return {"status": "ok", "mode": s.trading_mode, "llm_available": llm_available(),
            "alpaca_configured": bool(s.alpaca_api_key)}


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
