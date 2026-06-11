# PortfolioVirtualTwin

**A paper-trading Investment Strategy OS.** Your investment strategy becomes a *Strategy Twin* —
structured, versioned, testable data — that drives AI research agents, backtesting, a
deterministic risk gateway, and simulated execution. You stay in control: agents propose,
deterministic rules validate, **you approve**.

> ⚠️ **No API ever places a real order from this app.** Paper trading uses the simulator or
> Alpaca's paper endpoint (`paper=True` hardcoded). Real-portfolio orders use a
> *manual-execution* flow: armed portfolio + readiness checklist + risk gateway + typed
> CONFIRM, then **you** execute at your broker and record the fill. A live API adapter
> exists but is unreachable unless `LIVE_TRADING_ENABLED` + dedicated live keys are set.
> Nothing here is financial advice.

## What it does

1. **Capture your strategy** — describe it in plain language (AI builder) or write the YAML
   directly. Every save is an immutable version.
2. **Track stocks** — a *My Stocks* watchlist with on-demand refresh of open-source data
   (price, technical indicators, ~19 yfinance fundamentals) per tracked symbol.
3. **Bull & Bear agents** — two adversarial agents argue each tracked stock: the Bull builds
   the strongest data-grounded buy case (0–100 signal), the Bear the strongest sell case.
   Both cite the fetched numbers and state what would invalidate them.
4. **Research** — a LangGraph agent gathers price indicators + fundamentals per ticker and
   produces recommendations with confidence, thesis, and *what would invalidate it*.
5. **Backtest** — rule AST → vectorbt signal matrices, equity vs benchmark, QuantStats
   tearsheet. Rules that can't be tested from price history are listed, not silently dropped.
6. **Paper trade** — internal fill simulator (zero setup) or Alpaca paper API, behind one
   broker interface. Every order passes ~11 deterministic risk gates first, then waits for
   your explicit approval.
7. **Trade for real — with guardrails** — the real portfolio can be armed (readiness
   checklist) to route orders through the same risk gateway, with per-order notional caps,
   daily order caps, typed CONFIRM approval, and a header kill switch. The app never calls
   a broker API for real orders: you execute at your broker and record the fill
   (API brokers plug into the same interface later; Alpaca-live adapter ships flag-gated OFF).
8. **Macro & geopolitics** — VIX, oil, gold, rates, dollar + GPR geopolitical-risk index and
   GDELT war-news intensity → deterministic regime flags (risk-off, oil shock, gold rush,
   war risk) that damp position sizing in the gateway, plus an LLM macro briefing.
9. **Risk everywhere** — deterministic 0-100 risk score (explainable factors) on every
   recommendation and order; sliders for all strategy risk limits.
10. **Monitor** — portfolio dashboard (P&L, drawdown, exposure), risk cockpit (limits,
   concentration, exit-rule alerts), AI daily briefing ("the portfolio gained because…"),
   and an append-only audit log of every state change.

## Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 15 · Tailwind · shadcn/ui · lightweight-charts |
| API | FastAPI (modular monolith) · SQLAlchemy 2 · SQLite (dev) / PostgreSQL (compose) |
| Agents | LangGraph · OpenAI (optional — app fully works without a key) |
| Data | yfinance (default) · Alpaca IEX feed (optional) |
| Quant | vectorbt · QuantStats |
| Brokers | Internal simulator · alpaca-py (paper, hardcoded) |

## Quick start (local, no Docker)

```bash
# backend — needs uv (https://docs.astral.sh/uv/)
cd backend
cp ../.env.example .env          # optional: add OPENAI / ALPACA keys
uv sync
uv run uvicorn app.main:app --port 8000

# frontend
cd ../frontend
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm install
npm run dev                      # http://localhost:3000
```

Register an account, create a strategy (a ready-made template is pre-filled in the YAML
editor), create a portfolio with the **sim** broker, and you're trading on paper.

## Quick start (Docker Compose)

```bash
cp .env.example .env             # set JWT_SECRET, optionally API keys
docker compose up --build        # frontend :3000, API :8000, Postgres
```

## Tests

```bash
cd backend && uv run pytest      # risk-gateway matrix, rule AST, sim broker, full pipeline
```

## Safety design (the part that matters)

- **Agents never touch brokers.** Agent output is rows in `recommendations` /
  `order_proposals`. Only `app/execution/service.py` talks to brokers, and only after the
  risk gateway passed **and** a human approval row exists.
- **Deterministic risk gateway** (`app/risk/gateway.py`): 16 gates — kill switch, arming,
  universe whitelist, cash, position size, sector exposure, max positions, daily loss,
  drawdown, duplicates, order frequency, per-order notional cap, real-order frequency cap,
  macro-regime damping, no shorting. Every check is persisted per proposal — the UI shows
  exactly why an order was allowed or blocked.
- **Real money behind layered consent**: readiness checklist (incl. mandatory stop-loss
  rule) → explicit arming per portfolio → caps → typed `CONFIRM` per order → manual
  execution at your broker → recorded fill. Header **kill switch** cancels all open orders
  and disarms everything. `human_approval_required` is the literal `True` — not configurable.
- **Macro-aware risk**: deterministic regime flags (VIX/oil/gold/GPR/GDELT) damp position
  sizing in hostile regimes; the LLM macro agent only narrates, never computes flags.
- **Append-only audit**: every state transition (proposal, risk run, approval, submit, fill,
  alert, version change) writes to `audit_log`.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture, data model,
agent workflows, and the 3-step roadmap (MVP 1 → risk engine → live-ready governance).

## Limitations (honest list)

- Free market data: yfinance scrapes Yahoo (can break; personal-use ToS), Alpaca's free feed
  is IEX-only. Fine for paper trading; not for live decisions.
- Fundamental/sentiment rules are agent-evaluated at proposal time, not backtested (shown
  transparently per backtest).
- Single-process job model (threads + asyncio monitor) — by design for ≤10 users.
- Database migrations: tables are created on startup (`create_all`); introduce Alembic before
  any schema-breaking change in production.
