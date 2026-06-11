from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models import Strategy, StrategyVersion, User
from app.strategy import service
from app.strategy.twin import StrategyTwin

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


class TwinPayload(BaseModel):
    twin: dict
    comment: str = ""


class YamlPayload(BaseModel):
    yaml_text: str
    comment: str = ""


def _version_out(v: StrategyVersion) -> dict:
    return {"id": v.id, "version": v.version, "twin": v.twin, "comment": v.comment,
            "created_at": v.created_at.isoformat()}


def _strategy_out(s: Strategy, db: Session) -> dict:
    active = db.get(StrategyVersion, s.active_version_id) if s.active_version_id else None
    return {"id": s.id, "name": s.name, "active_version_id": s.active_version_id,
            "active_version": _version_out(active) if active else None,
            "created_at": s.created_at.isoformat()}


@router.get("")
def list_strategies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Strategy).where(Strategy.user_id == user.id)).all()
    return [_strategy_out(s, db) for s in rows]


@router.post("")
def create(payload: TwinPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    twin = StrategyTwin.model_validate(payload.twin)
    strategy = service.create_strategy(db, user.id, twin, payload.comment or "initial")
    return _strategy_out(strategy, db)


@router.post("/yaml")
def create_from_yaml(payload: YamlPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        twin = StrategyTwin.from_yaml(payload.yaml_text)
    except Exception as exc:
        raise HTTPException(422, f"Invalid Strategy Twin YAML: {exc}")
    strategy = service.create_strategy(db, user.id, twin, payload.comment or "initial")
    return _strategy_out(strategy, db)


@router.get("/{strategy_id}")
def get_one(strategy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = service.get_strategy(db, user.id, strategy_id)
    out = _strategy_out(s, db)
    versions = db.scalars(select(StrategyVersion).where(StrategyVersion.strategy_id == s.id)
                          .order_by(StrategyVersion.version.desc())).all()
    out["versions"] = [_version_out(v) for v in versions]
    if out["active_version"]:
        twin = StrategyTwin.model_validate(out["active_version"]["twin"])
        out["active_version"]["yaml"] = twin.to_yaml()
        ok, skipped = twin.backtest_coverage()
        out["backtest_coverage"] = {"backtestable": [r.describe() for r in ok],
                                    "agent_evaluated": [r.describe() for r in skipped]}
    return out


@router.post("/{strategy_id}/versions")
def new_version(strategy_id: int, payload: TwinPayload, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    twin = StrategyTwin.model_validate(payload.twin)
    v = service.add_version(db, user.id, strategy_id, twin, payload.comment)
    return _version_out(v)


@router.post("/{strategy_id}/versions/yaml")
def new_version_yaml(strategy_id: int, payload: YamlPayload, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    try:
        twin = StrategyTwin.from_yaml(payload.yaml_text)
    except Exception as exc:
        raise HTTPException(422, f"Invalid Strategy Twin YAML: {exc}")
    v = service.add_version(db, user.id, strategy_id, twin, payload.comment)
    return _version_out(v)


@router.get("/{strategy_id}/versions/{version_id}/yaml")
def version_yaml(strategy_id: int, version_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    v = service.get_version(db, user.id, version_id)
    return {"yaml": StrategyTwin.model_validate(v.twin).to_yaml()}
