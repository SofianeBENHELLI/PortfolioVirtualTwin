import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import audit
from app.backtest.engine import quantstats_report, run_backtest
from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.core.events import bus
from app.core.security import get_current_user
from app.models import BacktestRun, User
from app.strategy import service as strategy_service
from app.strategy.twin import StrategyTwin

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


class BacktestCreate(BaseModel):
    strategy_id: int
    symbols: list[str]
    start: str  # YYYY-MM-DD
    end: str
    initial_cash: float = 100_000.0
    with_tearsheet: bool = False


def _out(r: BacktestRun) -> dict:
    return {"id": r.id, "strategy_version_id": r.strategy_version_id, "status": r.status,
            "params": r.params, "metrics": r.metrics, "equity_curve": r.equity_curve,
            "skipped_rules": r.skipped_rules, "error": r.error,
            "has_tearsheet": bool(r.report_path),
            "created_at": r.created_at.isoformat()}


def _execute(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(BacktestRun, run_id)
        from app.models import StrategyVersion
        sv = db.get(StrategyVersion, run.strategy_version_id)
        twin = StrategyTwin.model_validate(sv.twin)
        p = run.params
        result = run_backtest(
            twin, p["symbols"],
            datetime.fromisoformat(p["start"]), datetime.fromisoformat(p["end"]),
            p.get("initial_cash", 100_000.0),
        )
        run.metrics = result["metrics"]
        run.equity_curve = result["equity_curve"]
        run.skipped_rules = result["skipped_rules"]
        if p.get("with_tearsheet"):
            path = str(get_settings().reports_dir / f"backtest_{run.id}.html")
            try:
                quantstats_report(result["returns"], result["bench_returns"], path,
                                  title=twin.strategy_name)
                run.report_path = path
            except Exception as exc:  # tearsheet is best-effort, metrics already saved
                run.error = f"tearsheet failed: {exc}"
        run.status = "done"
        run.finished_at = datetime.now(timezone.utc)
        audit(db, "backtest.finished", user_id=run.user_id, actor="system",
              entity="backtest_run", entity_id=run.id, payload={"metrics": run.metrics})
        db.commit()
        bus.publish("backtest", {"id": run.id, "status": "done"})
    except Exception as exc:
        db.rollback()
        run = db.get(BacktestRun, run_id)
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        bus.publish("backtest", {"id": run.id, "status": "failed"})
    finally:
        db.close()


@router.post("")
def create(payload: BacktestCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    version, twin = strategy_service.active_twin(db, user.id, payload.strategy_id)
    symbols = payload.symbols or twin.universe.symbols
    if not symbols:
        raise HTTPException(422, "No symbols: pass symbols or set universe.symbols in the strategy")
    run = BacktestRun(user_id=user.id, strategy_version_id=version.id,
                      params={"symbols": [s.upper() for s in symbols], "start": payload.start,
                              "end": payload.end, "initial_cash": payload.initial_cash,
                              "with_tearsheet": payload.with_tearsheet})
    db.add(run)
    db.flush()
    audit(db, "backtest.started", user_id=user.id, entity="backtest_run", entity_id=run.id,
          payload=run.params)
    db.commit()
    threading.Thread(target=_execute, args=(run.id,), daemon=True).start()
    return _out(run)


@router.get("")
def list_runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(BacktestRun).where(BacktestRun.user_id == user.id)
                      .order_by(BacktestRun.created_at.desc()).limit(50)).all()
    return [_out(r) for r in rows]


@router.get("/{run_id}")
def get_run(run_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(BacktestRun, run_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "Backtest not found")
    return _out(r)


@router.get("/{run_id}/tearsheet")
def tearsheet(run_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(BacktestRun, run_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "Backtest not found")
    if not r.report_path:
        raise HTTPException(404, "No tearsheet for this run")
    return FileResponse(r.report_path, media_type="text/html")
