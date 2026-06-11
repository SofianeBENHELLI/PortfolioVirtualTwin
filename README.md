# PortfolioVirtualTwin

**A paper-trading Investment Strategy OS.** Your investment strategy becomes a *Strategy Twin* —
structured, versioned, testable data — that drives AI research agents, backtesting, a
deterministic risk gateway, and simulated execution. You stay in control: agents propose,
deterministic rules validate, **you approve**.

> ⚠️ **MVP 1 is paper trading only.** There is no live-trading code path in this repository.
> The Alpaca integration hardcodes `paper=True`; the strategy schema rejects any execution
> mode other than `paper_trading_only`. Nothing here is financial advice.

## What it does

1. **Capture your strategy** — describe it in plain language (AI builder) or write the YAML
   directly. Every save is an immutable version.
2. **Research** — a LangGraph agent gathers price indicators + fundamentals per ticker and
   produces recommendations with confidence, thesis, and *what would invalidate it*.
3. **Backtest** — rule AST → vectorbt signal matrices, equity vs benchmark, QuantStats
   tearsheet. Rules that can't be tested from price history are listed, not silently dropped.
4. **Paper trade** — internal fill simulator (zero setup) or Alpaca paper API, behind one
   broker interface. Every order passes ~11 deterministic risk gates first, then waits for
   your explicit approval.
5. **Monitor** — portfolio dashboard (P&L, drawdown, exposure), risk cockpit (limits,
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
- **Deterministic risk gateway** (`app/risk/gateway.py`): paper-mode, universe whitelist,
  cash, position size, sector exposure, max positions, daily loss, drawdown, duplicates,
  order frequency, no shorting. Every check is persisted per proposal — the UI shows exactly
  why an order was allowed or blocked.
- **Paper-only by construction**: `ExecutionPolicy.mode` is the literal type
  `"paper_trading_only"`; `human_approval_required` is the literal `True`. Other values fail
  validation. The Alpaca client is constructed with `paper=True`, not configuration.
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
