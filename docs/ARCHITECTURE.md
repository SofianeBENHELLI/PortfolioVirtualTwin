# PortfolioVirtualTwin — Architecture & Roadmap

Status: **MVP 1 implemented** (paper trading only). This document is the technical proposal
and the record of what was built, including repository selection rationale and the 3-step
plan toward a live-ready system.

---

## 1. Architecture overview

A **modular monolith** — one FastAPI deployable with strict internal module boundaries —
chosen deliberately for the ≤10-user constraint. Microservices, Kubernetes, Celery/Temporal
were rejected as overengineering at this scale.

```
Next.js (App Router, shadcn/ui, lightweight-charts)
        │  REST + SSE
FastAPI backend (single process)
  ├── strategy/      Strategy Twin: Pydantic models, rule AST, YAML, immutable versions
  ├── data/          MarketDataProvider protocol → yfinance | Alpaca IEX (OpenBB slot reserved)
  ├── agents/        LangGraph graphs (capture, research, explain) + deterministic proposal step
  ├── backtest/      rule AST → vectorbt signals · QuantStats tearsheets (worker thread)
  ├── risk/          Deterministic Risk Gateway — pure functions, no LLM, every check persisted
  ├── execution/     BrokerProtocol → SimBroker | AlpacaPaperBroker (paper=True hardcoded)
  ├── portfolio/     valuation, P&L, drawdown, concentration analytics
  ├── monitor/       asyncio loop: price refresh, order polling, snapshots, risk alerts
  └── audit/         append-only AuditLog writer used by every module
SQLite (dev) / PostgreSQL (compose) · SQLAlchemy 2
```

### Safety invariants (architectural, not policy)

1. **No live-trading code path exists.** `AlpacaPaperBroker` constructs
   `TradingClient(..., paper=True)` as a literal; there is no configuration that reaches a
   live endpoint. `ExecutionPolicy.mode` is the Pydantic literal `"paper_trading_only"` and
   `human_approval_required` the literal `True` — any other value is a validation error.
2. **Agents cannot execute.** Agent graphs end at database rows (`Recommendation`,
   `OrderProposal`). Only `execution/service.py` holds broker handles, and `decide()` refuses
   any proposal without `risk_passed=True` plus a human `Approval` row.
3. **The risk gateway is deterministic.** Pure functions over a `PortfolioState` dataclass —
   unit-tested pass/fail matrix, one persisted `RiskCheck` row per gate per proposal.

## 2. The Strategy Twin

The user's strategy as structured, versioned data (`app/strategy/twin.py`): universe,
thesis, signals, entry/exit rules, risk limits, hedging policy, execution policy, benchmark.

Rules are a **constrained expression AST** — `{metric, op, value}` — never free-text code.
The same rule objects drive:

- **backtests** (metrics computable from OHLCV: momentum, moving-average regime, relative
  strength, RSI, volatility, volume confirmation, stop-from-entry),
- **the risk gateway** (limits),
- **live monitoring** (exit-rule alerts),
- **the UI** (human-readable rendering + "rule coverage" panel).

Metrics that need fundamentals/sentiment (`quality_score`, `valuation_risk`, …) are
evaluated by the research agent at proposal time, and each backtest lists them under
"rules not included" — transparency over silent omission.

Every save creates an immutable `StrategyVersion`; the strategy points at the active one.

## 3. Agent workflow (LangGraph + OpenAI)

Four small graphs instead of one mega-graph:

| Graph | Nodes | Output |
|---|---|---|
| **Capture** | LLM with structured output, ≤3 validation retries | draft Strategy Twin YAML (user reviews & saves) |
| **Research** | gather (deterministic data) → analyze (LLM per ticker) | `Recommendation` rows: action, confidence, thesis, invalidation, data used |
| **Proposal** | *fully deterministic* — sizing from risk limits | `OrderProposal` rows → risk gateway → approval queue |
| **Explain** | gather stats (deterministic) → narrate (LLM) | `PerformanceReport` ("gained because…", contributors, risk, watch items) |

Token usage is recorded per `AgentRun` (`prompt_tokens`, `completion_tokens`); research is
capped at 10 symbols per run, proposals at 5 per run. Without `OPENAI_API_KEY` the agent
endpoints return 503 and everything else works.

### Order pipeline (no LLM from here on)

```
OrderProposal → Risk Gateway (11 gates, persisted) → risk_passed | risk_blocked
   → human Approve/Reject (UI) → broker submit → fill → position/cash update
   → PortfolioSnapshot → SSE event → audit row
```

Gates: paper_mode, instrument_whitelist, sell_inventory (no shorting), cash_available,
max_position_size, max_sector_exposure, max_positions, max_daily_loss (buys blocked after
breach), max_drawdown (same), duplicate_order, order_frequency.

## 4. Data model

`users, strategies, strategy_versions, assets, market_data_snapshots, research_documents,
agent_runs, recommendations, backtest_runs, portfolios, positions, portfolio_snapshots,
order_proposals, risk_checks, approvals, paper_orders, execution_events, alerts,
performance_reports, audit_log`

Append-only: `risk_checks`, `execution_events`, `audit_log` (every state transition).

## 5. Repository selection (validated June 2026)

**Used in MVP 1**

| Repo | Role | Notes |
|---|---|---|
| langchain-ai/langgraph | agent orchestration | pin ≥1.2.4 (1.2.3 yanked), MIT |
| polakowo/vectorbt | backtesting | OSS revived (v1.0 Apr 2026); Apache+Commons Clause — fine unless sold |
| ranaroussi/quantstats | tearsheets | pin exact (pandas-breakage history) |
| alpacahq/alpaca-py | paper broker + IEX data | paper API free, not residency-gated |
| ranaroussi/yfinance | default/fallback data | unofficial scraper; OK for ≤10-user paper app |
| skfolio/skfolio | (reserved) risk-aware allocation | wired as dependency for Step 2 |

**Deferred** — OpenBB (now AGPL "Open Data Platform"; add behind the provider interface if
Alpaca+yfinance data proves insufficient), llama_index (RAG over filings, Step 2),
TradingAgents (mine for debate prompts, not a dependency), Riskfolio-Lib/PyPortfolioOpt
(Step 2), LEAN + ib_async (Step 3).

**Avoided** — backtrader (abandoned 2023), backtesting.py (slow single-maintainer, AGPL),
qlib/FinRL/FinGPT (research-grade), freqtrade (crypto-first), crewAI (redundant with
LangGraph), lumibot (framework lock-in around our own orchestration).

## 6. Roadmap

### Step 1 — MVP 1 (this repo) ✅
Strategy capture/versioning, research agent, backtests, sim + Alpaca paper brokers, risk
gateway, approval workflow, dashboards, alerts, briefings, audit log.

### Step 2 — Robust strategy & risk engine
- Risk-aware allocation (skfolio) + hedge proposal engine (Riskfolio-Lib): suggested hedges
  with simulated impact, same proposal→gateway→approval pipeline.
- Stronger backtesting: transaction-cost models, scenario/stress testing, walk-forward;
  evaluate LEAN if event-driven realism is needed.
- Strategy version comparison, performance attribution, factor/sector exposure.
- Bull/bear agent debate before recommendations (TradingAgents patterns); red-team agent
  that argues against the strategy.
- RAG over filings/notes (llama_index or pgvector) feeding `research_documents`.

### Step 3 — Controlled live-ready architecture
- Broker abstraction hardened: ib_async / Alpaca live behind a **separate, default-off**
  capability that requires explicit user activation + per-order approval.
- Kill switch (instant cancel-all + halt), live-readiness checklist, fail-safe defaults.
- Real-time order monitoring, notifications, compliance-style export of the audit trail.
- Still: no LLM ever executes; deterministic gates on every order; human approval mandatory.

## 7. Known limitations / blind spots

- Free data quality (yfinance ToS + breakage; IEX-only depth on Alpaca's free feed).
- Backtests ignore fundamental/sentiment rules (disclosed per run); survivorship bias if the
  user picks today's winners as the universe.
- Sentiment signal is shallow (no FinGPT/news pipeline yet).
- Single-process jobs; a crash mid-backtest leaves the run "running" (visible, re-runnable).
- SQLite under concurrent writes is fine at this scale but Postgres is recommended in compose.
- Paper fills are optimistic (no slippage in SimBroker; market orders fill at last trade).
