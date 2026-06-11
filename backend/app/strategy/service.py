from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import audit
from app.models import Strategy, StrategyVersion
from app.strategy.twin import StrategyTwin


def create_strategy(db: Session, user_id: int, twin: StrategyTwin, comment: str = "initial") -> Strategy:
    strategy = Strategy(user_id=user_id, name=twin.strategy_name)
    db.add(strategy)
    db.flush()
    version = StrategyVersion(strategy_id=strategy.id, version=1, twin=twin.model_dump(mode="json"), comment=comment)
    db.add(version)
    db.flush()
    strategy.active_version_id = version.id
    audit(db, "strategy.created", user_id=user_id, entity="strategy", entity_id=strategy.id,
          payload={"name": twin.strategy_name, "version": 1})
    db.commit()
    return strategy


def add_version(db: Session, user_id: int, strategy_id: int, twin: StrategyTwin, comment: str = "") -> StrategyVersion:
    strategy = get_strategy(db, user_id, strategy_id)
    last = db.scalar(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy.id)
        .order_by(StrategyVersion.version.desc())
        .limit(1)
    )
    next_v = (last.version if last else 0) + 1
    version = StrategyVersion(strategy_id=strategy.id, version=next_v, twin=twin.model_dump(mode="json"), comment=comment)
    db.add(version)
    db.flush()
    strategy.active_version_id = version.id
    strategy.name = twin.strategy_name
    audit(db, "strategy.version_created", user_id=user_id, entity="strategy", entity_id=strategy.id,
          payload={"version": next_v, "comment": comment})
    db.commit()
    return version


def get_strategy(db: Session, user_id: int, strategy_id: int) -> Strategy:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None or strategy.user_id != user_id:
        raise HTTPException(404, "Strategy not found")
    return strategy


def get_version(db: Session, user_id: int, version_id: int) -> StrategyVersion:
    version = db.get(StrategyVersion, version_id)
    if version is None:
        raise HTTPException(404, "Strategy version not found")
    get_strategy(db, user_id, version.strategy_id)  # ownership check
    return version


def active_twin(db: Session, user_id: int, strategy_id: int) -> tuple[StrategyVersion, StrategyTwin]:
    strategy = get_strategy(db, user_id, strategy_id)
    if strategy.active_version_id is None:
        raise HTTPException(409, "Strategy has no active version")
    version = db.get(StrategyVersion, strategy.active_version_id)
    return version, StrategyTwin.model_validate(version.twin)
