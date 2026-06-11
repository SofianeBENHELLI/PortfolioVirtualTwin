from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.llm import require_llm
from app.audit.service import audit
from app.core.db import get_db
from app.core.security import get_current_user
from app.data.macro import REGIME_THRESHOLDS, build_snapshot
from app.models import MacroReport, MacroSnapshot, User

router = APIRouter(prefix="/api/macro", tags=["macro"])


def _snap_out(s: MacroSnapshot) -> dict:
    return {"id": s.id, "indicators": s.indicators, "fred": s.fred, "war": s.war,
            "gpr": s.gpr, "regimes": s.regimes, "as_of": s.as_of.isoformat()}


def latest_snapshot(db: Session) -> MacroSnapshot | None:
    return db.scalar(select(MacroSnapshot).order_by(MacroSnapshot.as_of.desc()).limit(1))


def refresh_macro(db: Session, user_id: int | None = None, actor: str = "user") -> MacroSnapshot:
    """Fetch all sources, persist a snapshot, raise alerts on regime transitions."""
    from app.monitor.scheduler import _alert_once  # local import avoids cycle

    prev = latest_snapshot(db)
    data = build_snapshot()
    snap = MacroSnapshot(indicators=data["indicators"], fred=data["fred"], war=data["war"],
                         gpr=data["gpr"], regimes=data["regimes"])
    db.add(snap)
    audit(db, "macro.refreshed", user_id=user_id, actor=actor, entity="macro_snapshot",
          payload={"regimes": {k: v for k, v in data["regimes"].items() if k != "thresholds"}})
    db.commit()

    if prev is not None and user_id is not None:
        _raise_transition_alerts(db, user_id, prev.regimes, snap.regimes)
    return snap


def _raise_transition_alerts(db: Session, user_id: int, old: dict, new: dict) -> None:
    from app.monitor.scheduler import _alert_once

    transitions = [
        ("war_risk", "high", "critical", "War risk regime is HIGH",
         "Geopolitical coverage intensity / GPR index crossed the high threshold. Review exposure and hedging policy."),
        ("risk_off", True, "warning", "Markets entered RISK-OFF regime",
         "VIX elevated or equities down with gold bid. The risk gateway is damping new position sizes."),
        ("oil_shock", True, "warning", "Oil shock detected",
         "WTI moved more than the 5-day shock threshold. Energy-sensitive positions may gap."),
        ("gold_rush", True, "info", "Gold rush regime",
         "Gold above its 200-day average with strong 30-day momentum — classic fear bid."),
        ("volatility_regime", "high", "warning", "Volatility regime is HIGH",
         "VIX above the high threshold. Position-size damping active."),
    ]
    for key, trigger, level, title, body in transitions:
        if new.get(key) == trigger and old.get(key) != trigger:
            _alert_once(db, user_id, None, level, "macro", title, body)
    db.commit()


@router.get("")
def get_macro(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    snap = latest_snapshot(db)
    return {"snapshot": _snap_out(snap) if snap else None, "thresholds": REGIME_THRESHOLDS}


@router.post("/refresh")
def refresh(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    snap = refresh_macro(db, user_id=user.id)
    return _snap_out(snap)


@router.post("/brief")
def brief(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_llm(db, user.id)
    snap = latest_snapshot(db)
    if snap is None:
        raise HTTPException(409, "No macro snapshot yet — refresh macro data first")
    from app.agents.graphs import run_macro_brief
    try:
        report = run_macro_brief(db, user.id, snap)
    except Exception as exc:
        from app.agents.llm import friendly_llm_error
        raise HTTPException(502, f"Macro brief failed: {friendly_llm_error(str(exc))}")
    return {"id": report.id, "narrative": report.narrative, "created_at": report.created_at.isoformat()}


@router.get("/reports")
def reports(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.scalars(select(MacroReport).where(MacroReport.user_id == user.id)
                      .order_by(MacroReport.created_at.desc()).limit(20)).all()
    return [{"id": r.id, "narrative": r.narrative, "created_at": r.created_at.isoformat()} for r in rows]
