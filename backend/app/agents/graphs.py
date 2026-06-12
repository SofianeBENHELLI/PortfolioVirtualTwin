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

    from app.agents.llm import require_llm
    model = get_chat_model(require_llm(db, user_id), temperature=0.1)
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
    api_key: str
    data: dict
    analyses: dict
    pt: int
    ct: int


FUNDAMENTAL_KEYS = (
    "sector", "industry", "longName", "trailingPE", "forwardPE", "priceToBook",
    "revenueGrowth", "earningsGrowth", "profitMargins", "grossMargins", "debtToEquity",
    "freeCashflow", "marketCap", "dividendYield", "beta", "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow", "targetMeanPrice", "recommendationKey",
)


def gather_symbol_data(symbols: list[str], benchmark: str) -> dict[str, dict]:
    """Open-source data bundle per symbol: latest price, technical indicators (computed
    from OHLCV history), and yfinance fundamentals. Shared by research, bull/bear, and
    the watchlist refresh."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=400)
    history = cached_history(symbols + [benchmark], start, end)
    bench_df = history.get(benchmark)
    data: dict[str, dict] = {}
    for sym in symbols:
        df = history.get(sym)
        if df is None or df.empty:
            continue
        entry = {"price": float(df["close"].iloc[-1]), "indicators": latest_indicators(df, bench_df)}
        try:
            import yfinance as yf
            info = yf.Ticker(sym).info or {}
            entry["fundamentals"] = {k: info.get(k) for k in FUNDAMENTAL_KEYS if info.get(k) is not None}
        except Exception:
            entry["fundamentals"] = {}
        data[sym] = entry
    return data


def _gather_node(state: ResearchState) -> dict:
    twin = StrategyTwin.model_validate(state["twin"])
    return {"data": gather_symbol_data(state["symbols"], twin.benchmark)}


def _analyze_node(state: ResearchState) -> dict:
    model = get_chat_model(state["api_key"]).with_structured_output(TickerAnalysis, include_raw=True)
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
        from app.agents.llm import require_llm
        out = graph.invoke({"symbols": symbols, "twin": twin.model_dump(mode="json"),
                            "api_key": require_llm(db, user_id),
                            "data": {}, "analyses": {}, "pt": 0, "ct": 0})
        from app.risk.scoring import score_recommendation
        n = 0
        for sym, a in out["analyses"].items():
            if "error" in a:
                continue
            vol = out["data"].get(sym, {}).get("indicators", {}).get("volatility_30d")
            try:
                rs = score_recommendation(db, user_id, twin, sym, a["action"], vol)
                risk_score = rs.score
            except Exception:
                risk_score = None
            db.add(Recommendation(
                agent_run_id=run.id, user_id=user_id, symbol=sym, action=a["action"],
                confidence=a["confidence"], risk_score=risk_score,
                thesis=a["thesis"], invalidation=a["invalidation"],
                data_used={"inputs": out["data"].get(sym, {}),
                           "scores": {"quality_score": a["quality_score"],
                                      "valuation_risk": a["valuation_risk"]}},
            ))
            n += 1
        if n == 0:
            errors = [a["error"] for a in out["analyses"].values() if "error" in a]
            if errors:
                _finish_run(db, run, "", out["pt"], out["ct"], status="failed", error=errors[0])
                return run
        _finish_run(db, run, f"Analyzed {len(symbols)} symbols, produced {n} recommendations",
                    out["pt"], out["ct"])
        audit(db, "agent.research_done", user_id=user_id, actor="agent", entity="agent_run",
              entity_id=run.id, payload={"recommendations": n})
        db.commit()
    except Exception as exc:
        _finish_run(db, run, "", 0, 0, status="failed", error=str(exc))
    return run


# ------------------------------------------------------------------ MacroAgent
# Narrates the DETERMINISTIC MacroSnapshot (it never computes regime flags itself).

def run_macro_brief(db: Session, user_id: int, snapshot) -> "MacroReport":
    from app.models import MacroReport

    run = AgentRun(user_id=user_id, graph="macro", inputs={"snapshot_id": snapshot.id})
    db.add(run)
    db.commit()
    indicators = {k: {kk: vv for kk, vv in v.items() if kk != "sparkline"}
                  for k, v in (snapshot.indicators or {}).items()}
    headlines = (snapshot.war or {}).get("headlines", [])[:10]
    prompt = (
        "You are the MACRO agent of a paper/real portfolio app. Using ONLY the data below, write a "
        "concise macro & geopolitical briefing (max 280 words) with sections: Regime (state each "
        "computed flag and what drives it, citing numbers), Geopolitics (war-risk signal + what the "
        "headlines suggest — attribute claims to headlines, don't assert them as fact), and What it "
        "means for a stock strategy (2-3 concrete, conditional implications, e.g. hedging, sizing, "
        "sectors). No investment advice disclaimer needed; never invent data.\n\n"
        f"Computed regime flags (deterministic): { {k: v for k, v in (snapshot.regimes or {}).items() if k != 'thresholds'} }\n"
        f"Indicators: {indicators}\n"
        f"FRED: {snapshot.fred}\nGPR index: {snapshot.gpr}\n"
        f"War coverage signal: { {k: v for k, v in (snapshot.war or {}).items() if k != 'headlines'} }\n"
        f"Recent headlines: {[h['title'] + ' (' + h['source'] + ')' for h in headlines]}"
    )
    try:
        from app.agents.llm import require_llm
        msg = get_chat_model(require_llm(db, user_id), temperature=0.3).invoke(prompt)
        pt, ct = usage_from(msg)
        narrative = msg.content if isinstance(msg.content, str) else str(msg.content)
        report = MacroReport(user_id=user_id, snapshot_id=snapshot.id, narrative=narrative)
        db.add(report)
        _finish_run(db, run, "Macro briefing generated", pt, ct)
        audit(db, "agent.macro_done", user_id=user_id, actor="agent", entity="macro_report")
        db.commit()
        return report
    except Exception as exc:
        _finish_run(db, run, "", 0, 0, status="failed", error=str(exc))
        raise


# --------------------------------------------------- Bull / Bear / Judge debate
# Three agents per stock: the Bull builds the strongest data-grounded BUY case, the
# Bear the strongest SELL/avoid case, then the JUDGE weighs both arguments against
# the data and issues a consolidated recommendation. Prompts are user-editable
# Markdown files in backend/prompts/ (Bull.md, Bear.md, Judge.md), re-read each run.
# All outputs are stored as Recommendations (perspective in data_used) — signals for
# the human, never orders.

class PillarScores(BaseModel):
    fundamental: float = Field(ge=0, le=10, description="Pillar A score /10")
    technical: float = Field(ge=0, le=10, description="Pillar B score /10")
    industry: float = Field(ge=0, le=10, description="Pillar C score /10")
    sector_macro: float = Field(ge=0, le=10, description="Pillar D score /10")
    composite: float = Field(ge=0, le=10, description="weighted composite (40/20/20/20)")


class AdversarialCase(BaseModel):
    """Structured capture of the Bull/Bear brief (per the prompt's Output Format)."""
    rating: str = Field(description="Bull: STRONG BUY|BUY|ACCUMULATE|NO ACTIONABLE BULL CASE. "
                                    "Bear: STRONG SELL|SELL|AVOID|SHORT|NO ACTIONABLE BEAR CASE")
    conviction: float = Field(ge=0, le=10, description="conviction X/10")
    thesis: str = Field(description="the thesis in 3 sentences, citing numbers with [FACT]/[EST] tags")
    pillar_scores: PillarScores
    pillar_notes: list[str] = Field(description="one line per pillar: Fundamental/Technical/Industry/Sector-Macro justification")
    target_price_12m: str = Field(description="bear / base / bull 12m range with implied % and key assumption")
    catalysts: list[str] = Field(description="3-7 dated catalysts: 'catalyst — window — probability [EST] — expected impact'")
    steelman_rebuttals: list[str] = Field(description="the 3 strongest opposing arguments and your rebuttal/concession for each")
    risks: list[str] = Field(description="honestly acknowledged risks to THIS case, with severity")
    invalidation: str = Field(description="price level or event that kills this thesis")
    data_gaps: str = Field(description="data that was missing and how it affects conviction")


class ScorecardRow(BaseModel):
    pillar: str = Field(description="Fundamental (40%) | Technical (20%) | Industry (20%) | Sector/Macro (20%)")
    bull_score: float = Field(ge=0, le=10)
    bear_score: float = Field(ge=0, le=10)
    edge: str = Field(description="BULL | BEAR | TIE")
    why: str = Field(description="one line")


class JudgeVerdict(BaseModel):
    """Structured arbitration per the Judge prompt's rubric and decision logic."""
    action: str = Field(description="BUY | ACCUMULATE | HOLD | REDUCE | SELL | INSUFFICIENT EVIDENCE")
    conviction: float = Field(ge=0, le=10, description="winner's composite adjusted for omissions/data gaps, X/10")
    verdict_summary: str = Field(description="5 sentences: who won, on what, by how much, what would change the answer")
    scorecard: list[ScorecardRow] = Field(description="evidence-quality scores per pillar + weighted composite row")
    bull_composite: float = Field(ge=0, le=10)
    bear_composite: float = Field(ge=0, le=10)
    horizon: str = Field(description="which horizon the edge applies to (0-6m trade vs 6-18m position)")
    sizing_guidance: str = Field(description="relative band only: e.g. 'half-size starter', 'no position'")
    catalyst_skew_0_6m: str = Field(description="POSITIVE | NEGATIVE | BALANCED + dominant events")
    catalyst_skew_6_18m: str = Field(description="POSITIVE | NEGATIVE | BALANCED + dominant events")
    strongest_bull: list[str] = Field(description="top 3 surviving bull arguments")
    strongest_bear: list[str] = Field(description="top 3 surviving bear arguments")
    material_omissions: list[str] = Field(description="things BOTH agents missed (flag only); empty if none")
    invalidation_triggers: list[str] = Field(description="2-3 concrete events/levels that force a re-run")
    reevaluation_date: str = Field(description="next catalyst or max 90 days")


def _judge_to_action(action: str) -> str:
    a = action.strip().upper()
    if a in ("BUY", "ACCUMULATE", "STRONG BUY"):
        return "buy"
    if a in ("SELL", "REDUCE", "STRONG SELL", "SHORT"):
        return "sell"
    return "hold"


def run_bull_bear(db: Session, user_id: int, twin: StrategyTwin, version_id: int | None,
                  symbols: list[str]) -> AgentRun:
    from app.agents.prompts import load_prompt, render

    symbols = [s.upper() for s in symbols][:8]  # 3 LLM calls per symbol — keep runs cheap
    run = AgentRun(user_id=user_id, graph="bullbear", strategy_version_id=version_id,
                   inputs={"symbols": symbols})
    db.add(run)
    db.commit()
    try:
        data = gather_symbol_data(symbols, twin.benchmark)
        from app.agents.llm import require_llm
        api_key = require_llm(db, user_id)
        model = get_chat_model(api_key, temperature=0.3).with_structured_output(AdversarialCase, include_raw=True)
        judge_model = get_chat_model(api_key, temperature=0.2).with_structured_output(JudgeVerdict, include_raw=True)
        prompts = {name: load_prompt(name) for name in ("Bull", "Bear", "Judge")}
        style, horizon = twin.investment_thesis.style, twin.investment_thesis.horizon

        from datetime import date
        as_of = date.today().isoformat()
        pt = ct = n = judged = 0
        last_error = ""
        for sym, payload in data.items():
            cases: dict[str, AdversarialCase] = {}
            vol = payload.get("indicators", {}).get("volatility_30d")
            for perspective, action in (("bull", "buy"), ("bear", "sell")):
                try:
                    prompt = render(prompts[perspective.capitalize()],
                                    sym=sym, data=payload, style=style, horizon=horizon,
                                    as_of_date=as_of)
                    result = model.invoke(prompt)
                    raw = result.get("raw")
                    if raw is not None:
                        p, c = usage_from(raw)
                        pt, ct = pt + p, ct + c
                    case = result["parsed"]
                    if case is None:
                        continue
                    cases[perspective] = case
                    from app.risk.scoring import score_recommendation
                    try:
                        risk_score = score_recommendation(db, user_id, twin, sym, action, vol).score
                    except Exception:
                        risk_score = None
                    db.add(Recommendation(
                        agent_run_id=run.id, user_id=user_id, symbol=sym, action=action,
                        confidence=case.conviction / 10.0, risk_score=risk_score,
                        thesis=case.thesis, invalidation=case.invalidation,
                        data_used={"perspective": perspective,
                                   "signal_strength": case.conviction * 10,
                                   "rating": case.rating,
                                   "key_points": case.pillar_notes,
                                   "report": case.model_dump()},
                    ))
                    n += 1
                except Exception as exc:
                    last_error = str(exc)
                    continue  # one bad symbol/perspective shouldn't kill the run

            # JUDGE: only when both sides actually argued
            if "bull" in cases and "bear" in cases:
                try:
                    bull, bear = cases["bull"], cases["bear"]

                    def _brief(case: AdversarialCase) -> str:
                        return (f"Thesis: {case.thesis}\n"
                                f"Target 12m: {case.target_price_12m}\n"
                                f"Catalysts: {'; '.join(case.catalysts)}\n"
                                f"Steelman & rebuttals: {'; '.join(case.steelman_rebuttals)}\n"
                                f"Acknowledged risks: {'; '.join(case.risks)}\n"
                                f"Invalidation: {case.invalidation}\n"
                                f"Data gaps: {case.data_gaps}")

                    def _pillars(case: AdversarialCase) -> str:
                        ps = case.pillar_scores
                        return (f"Fundamental {ps.fundamental}/10, Technical {ps.technical}/10, "
                                f"Industry {ps.industry}/10, Sector/Macro {ps.sector_macro}/10, "
                                f"Composite {ps.composite}/10. Notes: {'; '.join(case.pillar_notes)}")

                    prompt = render(prompts["Judge"], sym=sym, data=payload, style=style,
                                    horizon=horizon, as_of_date=as_of,
                                    bull_rating=bull.rating, bull_strength=f"{bull.conviction:.1f}",
                                    bull_case=_brief(bull), bull_points=_pillars(bull),
                                    bear_rating=bear.rating, bear_strength=f"{bear.conviction:.1f}",
                                    bear_case=_brief(bear), bear_points=_pillars(bear))
                    result = judge_model.invoke(prompt)
                    raw = result.get("raw")
                    if raw is not None:
                        p, c = usage_from(raw)
                        pt, ct = pt + p, ct + c
                    verdict = result["parsed"]
                    if verdict is not None:
                        action = _judge_to_action(verdict.action)
                        from app.risk.scoring import score_recommendation
                        try:
                            risk_score = score_recommendation(db, user_id, twin, sym, action, vol).score
                        except Exception:
                            risk_score = None
                        db.add(Recommendation(
                            agent_run_id=run.id, user_id=user_id, symbol=sym, action=action,
                            confidence=verdict.conviction / 10.0, risk_score=risk_score,
                            thesis=verdict.verdict_summary,
                            invalidation="; ".join(verdict.invalidation_triggers),
                            data_used={"perspective": "judge",
                                       "signal_strength": verdict.conviction * 10,
                                       "action": action, "verdict_action": verdict.action,
                                       "key_points": verdict.strongest_bull[:1] + verdict.strongest_bear[:1],
                                       "report": verdict.model_dump()},
                        ))
                        judged += 1
                except Exception as exc:
                    last_error = str(exc)
        if n == 0 and last_error:
            _finish_run(db, run, "", pt, ct, status="failed", error=last_error)
            return run
        missing = [s for s in symbols if s not in data]
        summary = f"Bull & Bear debated {len(data)} stocks → {n} signals, {judged} judge verdicts"
        if missing:
            summary += f" (no market data for {', '.join(missing)})"
        _finish_run(db, run, summary, pt, ct)
        audit(db, "agent.bullbear_done", user_id=user_id, actor="agent", entity="agent_run",
              entity_id=run.id, payload={"signals": n, "symbols": symbols})
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
        from app.agents.llm import require_llm
        msg = get_chat_model(require_llm(db, user_id), temperature=0.3).invoke(prompt)
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
