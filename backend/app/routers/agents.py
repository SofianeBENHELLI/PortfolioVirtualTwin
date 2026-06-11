import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import graphs
from app.agents.llm import friendly_llm_error, require_llm
from app.core.db import SessionLocal, get_db
from app.core.events import bus
from app.core.security import get_current_user
from app.execution import service as exec_service
from app.models import AgentRun, PerformanceReport, Recommendation, User
from app.strategy import service as strategy_service

router = APIRouter(prefix="/api/agents", tags=["agents"])


class CaptureRequest(BaseModel):
    description: str
    current_twin: dict | None = None


class ResearchRequest(BaseModel):
    strategy_id: int
    symbols: list[str] = []


class ProposalRequest(BaseModel):
    portfolio_id: int
    min_confidence: float = 0.6


class ExplainRequest(BaseModel):
    portfolio_id: int


@router.get("/status")
def status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.agents.llm import resolve_openai_key
    key, source = resolve_openai_key(db, user.id)
    return {"llm_available": bool(key), "key_source": source}


@router.post("/capture")
def capture(payload: CaptureRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_llm(db, user.id)
    try:
        twin, run = graphs.capture_strategy(db, user.id, payload.description, payload.current_twin)
    except ValueError as exc:
        raise HTTPException(502, friendly_llm_error(str(exc)))
    return {"twin": twin.model_dump(mode="json"), "yaml": twin.to_yaml(), "agent_run_id": run.id}


@router.post("/research")
def research(payload: ResearchRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_llm(db, user.id)
    version, twin = strategy_service.active_twin(db, user.id, payload.strategy_id)
    symbols = payload.symbols or twin.universe.symbols
    if not symbols:
        raise HTTPException(422, "No symbols: pass symbols or set universe.symbols in the strategy")
    run = graphs.run_research(db, user.id, twin, version.id, symbols)  # synchronous-on-purpose for ≤10 symbols
    bus.publish("agent_run", {"id": run.id, "graph": "research", "status": run.status})
    if run.status == "failed":
        raise HTTPException(502, f"Research run failed: {friendly_llm_error(run.error)}")
    return _run_out(db, run)


@router.post("/proposals")
def proposals(payload: ProposalRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    portfolio = exec_service.get_portfolio(db, user.id, payload.portfolio_id)
    if portfolio.strategy_id is None:
        raise HTTPException(409, "Portfolio has no linked strategy")
    version, twin = strategy_service.active_twin(db, user.id, portfolio.strategy_id)
    ids = graphs.run_proposals(db, user.id, portfolio, twin, version.id, payload.min_confidence)
    return {"created_proposal_ids": ids,
            "note": "Proposals passed through the deterministic risk gateway; pending your approval in the Trading Console."}


@router.post("/explain")
def explain(payload: ExplainRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_llm(db, user.id)
    portfolio = exec_service.get_portfolio(db, user.id, payload.portfolio_id)
    try:
        report = graphs.run_explain(db, user.id, portfolio)
    except Exception as exc:
        raise HTTPException(502, f"Explain run failed: {friendly_llm_error(str(exc))}")
    return {"id": report.id, "narrative": report.narrative, "stats": report.stats,
            "created_at": report.created_at.isoformat()}


@router.get("/runs")
def runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(AgentRun).where(AgentRun.user_id == user.id)
                      .order_by(AgentRun.started_at.desc()).limit(50)).all()
    return [_run_out(db, r, include_recs=False) for r in rows]


@router.get("/recommendations")
def recommendations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Recommendation).where(Recommendation.user_id == user.id)
                      .order_by(Recommendation.created_at.desc()).limit(100)).all()
    return [_rec_out(r) for r in rows]


@router.get("/reports")
def reports(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(PerformanceReport).where(PerformanceReport.user_id == user.id)
                      .order_by(PerformanceReport.created_at.desc()).limit(30)).all()
    return [{"id": r.id, "portfolio_id": r.portfolio_id, "kind": r.kind, "narrative": r.narrative,
             "created_at": r.created_at.isoformat()} for r in rows]


def _rec_out(r: Recommendation) -> dict:
    return {"id": r.id, "symbol": r.symbol, "action": r.action, "confidence": r.confidence,
            "risk_score": r.risk_score,
            "thesis": r.thesis, "invalidation": r.invalidation, "data_used": r.data_used,
            "created_at": r.created_at.isoformat()}


def _run_out(db: Session, r: AgentRun, include_recs: bool = True) -> dict:
    out = {"id": r.id, "graph": r.graph, "status": r.status, "summary": r.summary,
           "inputs": r.inputs, "prompt_tokens": r.prompt_tokens,
           "completion_tokens": r.completion_tokens, "error": r.error,
           "started_at": r.started_at.isoformat(),
           "finished_at": r.finished_at.isoformat() if r.finished_at else None}
    if include_recs:
        recs = db.scalars(select(Recommendation).where(Recommendation.agent_run_id == r.id)).all()
        out["recommendations"] = [_rec_out(x) for x in recs]
    return out
