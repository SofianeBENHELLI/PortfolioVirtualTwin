"""LangGraph agent workflows.

Hard boundary: graphs end at DB rows (Recommendation, OrderProposal via the normal
pipeline, PerformanceReport). No graph node holds a broker handle; execution happens
only through app.execution.service after the risk gateway and human approval.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TypedDict

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.llm import get_chat_model, usage_from
from app.audit.service import audit
from app.data.provider import cached_history, latest_indicators
from app.models import AgentRun, Alert, Asset, PerformanceReport, Portfolio, Recommendation
from app.portfolio import service as portfolio_service
from app.strategy.twin import StrategyTwin


def _finish_run(db: Session, run: AgentRun, summary: str, pt: int, ct: int, status: str = "done",
                error: str = "") -> None:
    run.status = status
    run.summary = summary
    run.prompt_tokens += pt
    run.completion_tokens += ct
    run.error = error
    run.finished_at = datetime.now(timezone.utc)
    db.commit()


# ------------------------------------------------------------- StrategyCaptureGraph

CAPTURE_SYSTEM = """You are an investment strategy analyst. Convert the user's description
into a Strategy Twin JSON document. Use ONLY these metrics in entry_rules/exit_rules
conditions: momentum_score (0-100), price_above_200_day_average (bool),
price_above_50_day_average (bool), relative_strength (%), rsi_14, volatility_30d (%),
volume_confirmation (bool), drawdown_from_entry (%), quality_score (0-100),
valuation_risk (low|moderate|high|extreme), news_sentiment (-1..1), thesis_broken (bool).
entry_rules are ANDed; exit_rules are ORed. Be conservative with risk limits.
The execution mode MUST be paper_trading_only with human_approval_required true."""


def capture_strategy(db: Session, user_id: int, description: str,
                     current: dict | None = None) -> tuple[StrategyTwin, AgentRun]:
    run = AgentRun(user_id=user_id, graph="capture", inputs={"description": description[:2000]})
    db.add(run)
    db.commit()

    model = get_chat_model(temperature=0.1)
    structured = model.with_structured_output(StrategyTwin, include_raw=True)
    prompt = CAPTURE_SYSTEM + "\n\nUser strategy description:\n" + description
    if current:
        prompt += "\n\nCurrent Strategy Twin (modify it per the user's request):\n" + str(current)

    pt = ct = 0
    last_err = ""
    for attempt in range(3):
        try:
            result = structured.invoke(prompt + (f"\n\nPrevious attempt failed validation: {last_err}" if last_err else ""))
            raw = result.get("raw")
            if raw is not None:
                p, c = usage_from(raw)
                pt, ct = pt + p, ct + c
            twin = result["parsed"]
            if twin is None:
                last_err = str(result.get("parsing_error", "unknown parsing error"))
                continue
            _finish_run(db, run, f"Captured strategy '{twin.strategy_name}'", pt, ct)
            audit(db, "agent.capture_done", user_id=user_id, actor="agent", entity="agent_run",
                  entity_id=run.id, payload={"strategy_name": twin.strategy_name})
            db.commit()
            return twin, run
        except Exception as exc:  # noqa: BLE001 — surfaced to the user via AgentRun.error
            last_err = str(exc)
    _finish_run(db, run, "", pt, ct, status="failed", error=last_err)
    raise ValueError(f"Strategy capture failed after 3 attempts: {last_err}")


# ------------------------------------------------------------------- ResearchGraph

class TickerAnalysis(BaseModel):
    action: str = Field(description="buy | sell | hold")
    confidence: float = Field(ge=0, le=1)
    quality_score: float = Field(ge=0, le=100)
    valuation_risk: str = Field(description="low | moderate | high | extreme")
    thesis: str = Field(description="2-3 sentence investment thesis grounded in the provided data")
    invalidation: str = Field(description="what observable event would break this thesis")


class ResearchState(TypedDict):
    symbols: list[str]
    twin: dict
    data: dict
    analyses: dict
    pt: int
    ct: int


def _gather_node(state: ResearchState) -> dict:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=400)
    twin = StrategyTwin.model_validate(state["twin"])
    history = cached_history(state["symbols"] + [twin.benchmark], start, end)
    bench_df = history.get(twin.benchmark)
    data: dict = {}
    for sym in state["symbols"]:
        df = history.get(sym)
        if df is None or df.empty:
            continue
        entry = {"price": float(df["close"].iloc[-1]), "indicators": latest_indicators(df, bench_df)}
        try:
            import yfinance as yf
            info = yf.Ticker(sym).info or {}
            entry["fundamentals"] = {
                k: info.get(k) for k in ("sector", "trailingPE", "forwardPE", "revenueGrowth",
                                         "profitMargins", "debtToEquity", "marketCap")
                if info.get(k) is not None
            }
        except Exception:
            entry["fundamentals"] = {}
        data[sym] = entry
    return {"data": data}


def _analyze_node(state: ResearchState) -> dict:
    model = get_chat_model().with_structured_output(TickerAnalysis, include_raw=True)
    twin = StrategyTwin.model_validate(state["twin"])
    analyses: dict = {}
    pt = ct = 0
    for sym, payload in state["data"].items():
        prompt = (
            f"You are an equity analyst applying this strategy: style={twin.investment_thesis.style}, "
            f"horizon={twin.investment_thesis.horizon}. Thesis: {twin.investment_thesis.description}\n"
            f"Analyze {sym} using ONLY this data (cite numbers in your thesis):\n{payload}\n"
            f"Entry rules to respect: {[r.describe() for r in twin.entry_rules]}"
        )
        try:
            result = model.invoke(prompt)
            raw = result.get("raw")
            if raw is not None:
                p, c = usage_from(raw)
                pt, ct = pt + p, ct + c
            if result["parsed"] is not None:
                analyses[sym] = result["parsed"].model_dump()
        except Exception as exc:  # skip symbol, keep the run alive
            analyses[sym] = {"error": str(exc)}
    return {"analyses": analyses, "pt": pt, "ct": ct}


def _build_research_graph():
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(ResearchState)
    g.add_node("gather", _gather_node)
    g.add_node("analyze", _analyze_node)
    g.add_edge(START, "gather")
    g.add_edge("gather", "analyze")
    g.add_edge("analyze", END)
    return g.compile()


def run_research(db: Session, user_id: int, twin: StrategyTwin, version_id: int | None,
                 symbols: list[str]) -> AgentRun:
    symbols = [s.upper() for s in symbols][:10]  # token-cost cap per run
    run = AgentRun(user_id=user_id, graph="research", strategy_version_id=version_id,
                   inputs={"symbols": symbols})
    db.add(run)
    db.commit()
    try:
        graph = _build_research_graph()
        out = graph.invoke({"symbols": symbols, "twin": twin.model_dump(mode="json"),
                            "data": {}, "analyses": {}, "pt": 0, "ct": 0})
        n = 0
        for sym, a in out["analyses"].items():
            if "error" in a:
                continue
            db.add(Recommendation(
                agent_run_id=run.id, user_id=user_id, symbol=sym, action=a["action"],
                confidence=a["confidence"], thesis=a["thesis"], invalidation=a["invalidation"],
                data_used={"inputs": out["data"].get(sym, {}),
                           "scores": {"quality_score": a["quality_score"],
                                      "valuation_risk": a["valuation_risk"]}},
            ))
            n += 1
        _finish_run(db, run, f"Analyzed {len(symbols)} symbols, produced {n} recommendations",
                    out["pt"], out["ct"])
        audit(db, "agent.research_done", user_id=user_id, actor="agent", entity="agent_run",
              entity_id=run.id, payload={"recommendations": n})
        db.commit()
    except Exception as exc:
        _finish_run(db, run, "", 0, 0, status="failed", error=str(exc))
    return run


# ------------------------------------------------------------------ ProposalGraph
# Deterministic by design: sizing comes from the Strategy Twin's risk limits, the
# rationale comes from the recommendation. No LLM between recommendation and order.

def run_proposals(db: Session, user_id: int, portfolio: Portfolio, twin: StrategyTwin,
                  version_id: int | None, min_confidence: float = 0.6) -> list[int]:
    from app.execution import service as exec_service

    since = datetime.now(timezone.utc) - timedelta(days=3)
    recs = db.scalars(
        select(Recommendation)
        .where(Recommendation.user_id == user_id, Recommendation.created_at >= since,
               Recommendation.confidence >= min_confidence,
               Recommendation.action.in_(["buy", "sell"]))
        .order_by(Recommendation.confidence.desc())
    ).all()
    if not recs:
        return []

    prices = exec_service.latest_prices_for(db, portfolio, extra=[r.symbol for r in recs])
    state_summary = portfolio_service.summary(db, portfolio, prices)
    equity = state_summary["equity"]
    target_weight = min(twin.risk_management.max_position_weight_pct,
                        100.0 / max(1, twin.risk_management.max_number_of_positions)) / 100.0
    held = {p["symbol"]: p["qty"] for p in state_summary["positions"]}

    created: list[int] = []
    for rec in recs[:5]:  # at most 5 proposals per run
        price = prices.get(rec.symbol)
        if not price:
            continue
        if rec.action == "buy":
            qty = round(equity * target_weight / price, 4)
            if qty <= 0:
                continue
        else:
            qty = held.get(rec.symbol, 0.0)
            if qty <= 0:
                continue  # nothing to sell
        proposal = exec_service.create_proposal(
            db, user_id, portfolio, twin, version_id,
            symbol=rec.symbol, side=rec.action, qty=qty, order_type="market",
            rationale=f"[agent confidence {rec.confidence:.0%}] {rec.thesis} "
                      f"Invalidation: {rec.invalidation}",
            source="agent", recommendation_id=rec.id,
        )
        created.append(proposal.id)
    return created


# ------------------------------------------------------------------- ExplainGraph

def run_explain(db: Session, user_id: int, portfolio: Portfolio, kind: str = "on_demand") -> PerformanceReport:
    run = AgentRun(user_id=user_id, graph="explain", inputs={"portfolio_id": portfolio.id})
    db.add(run)
    db.commit()

    from app.execution import service as exec_service
    prices = exec_service.latest_prices_for(db, portfolio)
    stats = portfolio_service.summary(db, portfolio, prices)
    alerts = db.scalars(select(Alert).where(Alert.user_id == user_id)
                        .order_by(Alert.created_at.desc()).limit(10)).all()
    stats_for_llm = {k: v for k, v in stats.items() if k != "positions"}
    stats_for_llm["positions"] = stats["positions"][:15]

    prompt = (
        "You are the daily feedback engine of a PAPER trading strategy app. Using ONLY the data "
        "below, write a concise briefing (max 250 words) with sections: What happened (P&L and why, "
        "naming the largest contributor and detractor), Risk (drawdown, concentration, anything "
        "approaching limits), and Watch items. Use plain language and exact numbers from the data. "
        "Do not invent data.\n\n"
        f"Portfolio stats: {stats_for_llm}\n\n"
        f"Recent alerts: {[{'level': a.level, 'title': a.title} for a in alerts]}"
    )
    try:
        msg = get_chat_model(temperature=0.3).invoke(prompt)
        pt, ct = usage_from(msg)
        narrative = msg.content if isinstance(msg.content, str) else str(msg.content)
        report = PerformanceReport(user_id=user_id, portfolio_id=portfolio.id, kind=kind,
                                   narrative=narrative, stats=stats_for_llm)
        db.add(report)
        _finish_run(db, run, "Performance briefing generated", pt, ct)
        audit(db, "agent.explain_done", user_id=user_id, actor="agent",
              entity="performance_report", entity_id=report.id)
        db.commit()
        return report
    except Exception as exc:
        _finish_run(db, run, "", 0, 0, status="failed", error=str(exc))
        raise
