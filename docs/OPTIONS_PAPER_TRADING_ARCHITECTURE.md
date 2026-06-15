# Options Paper Trading Architecture Plan

Status: proposal for the options extension of PortfolioVirtualTwin. This is for paper trading only. It is designed for a six-month competition where the objective is strong risk-adjusted performance with transparent risk, drawdown, and strategy behavior.

Non-goals:

- No real-money order placement.
- No hidden leverage or undefined-risk short option exposure by default.
- No agent is allowed to bypass deterministic risk checks.
- No strategy is optimized for opaque tail risk or manipulative behavior.

Core design principle:

> Agents may research, debate, rank, and explain. Deterministic services decide what is allowed. The user approves every trade.

---

## 1. Target architecture

```text
External Data Sources
  |-- underlying OHLCV / corporate actions
  |-- option chains / quotes / trades / IV / Greeks / OI / volume
  |-- earnings calendar / macro calendar / rates / VIX / sector data
  |-- news and sentiment feeds
  v
Raw Immutable Data Store
  |-- source payloads
  |-- source timestamp
  |-- ingest timestamp
  |-- payload hash
  v
Normalization + Feature Pipeline
  |-- clean symbols and contracts
  |-- point-in-time option surface
  |-- IV rank / percentile / skew / term structure
  |-- realized volatility / trend / regime features
  |-- event and news features
  v
Agentic Decision Engine
  |-- Market Regime Agent
  |-- Volatility Agent
  |-- Directional Agent
  |-- Event Agent
  |-- Options Structure Agent
  |-- Risk Agent, deterministic hard gate
  |-- Portfolio Manager Agent
  |-- Execution Agent
  |-- Performance Analyst Agent
  |-- Strategy Evolution Agent
  v
Trade Candidate Queue
  |-- candidate score
  |-- payoff diagram data
  |-- stress test results
  |-- structured decision memo
  |-- approve / reject / block state
  v
Paper Execution + Internal Ledger
  |-- broker paper endpoint, optional
  |-- internal execution simulator, mandatory
  |-- realistic fills, slippage, spread, liquidity constraints
  |-- partial fills and order lifecycle
  v
Monitoring + Review
  |-- open risk
  |-- Greek exposure
  |-- drawdown
  |-- strategy attribution
  |-- replayable audit log
  |-- six-month leaderboard
```

The internal paper ledger remains the source of truth for the competition. A broker paper account can be integrated for order routing and UI familiarity, but the application must still run its own execution model because broker paper environments often simplify liquidity, queue priority, slippage, fees, and partial fills.

---

## 2. Data layer

### Required data sources

| Dataset | Required fields | Notes |
|---|---|---|
| Underlying price history | OHLCV, adjusted close, splits, dividends, market hours | Use both adjusted and raw prices. Options require raw price alignment around corporate actions. |
| Option chains | underlying, OCC symbol, expiry, strike, call/put, multiplier, exercise style | Store full chain snapshots, not just selected contracts. |
| Option quotes/trades | bid, ask, bid size, ask size, last, volume, timestamp, quote condition | Needed for realistic execution and spread filters. |
| Implied volatility | contract IV, surface IV, IV rank, IV percentile | Prefer vendor IV plus internal recalculation. |
| Greeks | delta, gamma, vega, theta, rho | Store vendor values and model version for recalculated Greeks. |
| Open interest and volume | OI, day volume, rolling volume, volume/OI | Liquidity and crowding inputs. |
| Bid/ask spread | absolute spread, percent of mid, quote age | Used for hard liquidity filters. |
| Earnings calendar | date, time, confirmed/estimated, expected move | Event risk and IV crush modeling. |
| Macro events | Fed, CPI, PCE, jobs, Treasury auctions, GDP | Used to suppress short-gamma and oversizing around event risk. |
| News/sentiment | headline, source, timestamp, entity mapping, sentiment, novelty | Must be point-in-time and replayable. |
| Sector/regime data | sector ETF returns, correlations, VIX, rates, breadth | Used for portfolio correlation and regime detection. |

### Data normalization

- Canonicalize underlyings by ticker, exchange, asset class, sector, and primary ETF mapping.
- Parse OCC option symbols into `underlying`, `expiry`, `right`, `strike`, and `multiplier`.
- Normalize all timestamps to UTC and add `market_session_date` in US/Eastern.
- Preserve vendor timestamps separately from ingest timestamps.
- Remove or flag stale quotes, crossed markets, zero bids, extreme spreads, and impossible Greeks.
- Adjust underlying history for splits, but keep unadjusted prices for contract payoff alignment.
- Create point-in-time chain snapshots keyed by `snapshot_at` and `source_id`.
- Never overwrite raw vendor payloads. Corrections create a new transform version.

### Versioning model

Every feature used in a decision must be reproducible:

- `source_id`: market data vendor or feed.
- `vendor_timestamp`: time stated by the source.
- `ingested_at`: time the app received the data.
- `transform_version`: code version for normalization.
- `feature_set_id`: feature generation configuration.
- `model_version`: IV/Greek/pricing model version.
- `backtest_snapshot_id`: frozen dataset used by a backtest.
- `decision_snapshot_id`: frozen dataset used by an agent decision.

Use bronze/silver/gold storage:

- Bronze: immutable raw payloads.
- Silver: normalized chain, quote, trade, event, and price tables.
- Gold: analytics features, regime labels, candidate scores, and stress outputs.

---

## 3. Strategy library

Strategies are typed templates, not free-form agent inventions. Each template defines allowed legs, expiration rules, strike selection rules, payoff formulas, liquidity requirements, risk limits, and exit policies.

| Strategy | Best market regime | Entry conditions | Exit conditions | Max profit | Max loss | Greeks profile | Time decay | Liquidity requirement | Failure modes |
|---|---|---|---|---|---|---|---|---|---|
| Long call | Bull trend, low or normal IV | Directional score high, IV not expensive, liquid ATM/ITM calls | Profit target, thesis invalidated, DTE floor | Uncapped | Premium paid | +delta, +gamma, +vega, -theta | Negative | Tight spread, active volume | Sideways chop, IV crush |
| Long put | Bear trend or risk-off | Bearish thesis, downside momentum, event hedge need | Profit target, reversal, DTE floor | Large, capped by underlying to zero | Premium paid | -delta, +gamma, +vega, -theta | Negative | Tight spread, active volume | Rebound, vol crush |
| Debit spread | Directional, moderate IV | Directional edge, target move inside sold strike | 50-75% max profit, stop at 40-50% debit loss | Width - debit | Debit paid | Directional, lower vega than long option | Mild negative | Both legs liquid | Move too small or too slow |
| Credit spread | High IV, defined support/resistance | IV rich, short strike beyond invalidation, adequate credit | 50-70% credit captured, breach of short strike, DTE floor | Credit received | Width - credit | Short gamma, short vega, +theta | Positive | Strong OI and narrow spread | Gap through short strike |
| Iron condor | Range, high IV | High IV rank, low directional conviction, clear range | 50% credit capture, delta breach, event risk rises | Credit received | Wing width - credit | Near-neutral delta, short gamma/vega, +theta | Positive | All legs liquid | Breakout, vol spike |
| Calendar | Range near anchor strike | Front IV lower than back IV or term edge, expected pin | Profit target, strike drift, front DTE exit | Path-dependent | Debit paid | +vega, localized delta/gamma | Often positive near strike | Both expiries liquid | Large move away from strike |
| Diagonal | Directional plus term edge | Directional thesis, term structure favorable | Roll short leg, close if thesis breaks | Path-dependent | Usually net debit | Directional, often +vega/+theta | Mixed | Both expiries liquid | Fast adverse move, assignment risk |
| Straddle | Low IV, expected large move | Compression, catalyst, realized move expected > implied | Pre-event or post-breakout target, time stop | Large both directions | Debit paid | Long gamma, long vega, near-neutral delta | Strong negative | ATM liquidity excellent | No move, IV crush |
| Strangle | Low IV, wider breakout | Compression, asymmetric tails, cheaper convexity | Profit target, time stop, delta imbalance | Large both directions | Debit paid | Long gamma/vega, lower theta than straddle | Negative | Liquid OTM wings | Drift without breakout |
| Covered call | Mild bull or range | Long stock held, IV attractive, upside target known | Buy back at profit, roll, or let assignment in paper | Premium plus capped stock upside | Stock downside less premium | Long stock, short call | Positive from short call | Stock and call liquid | Big rally capped, stock selloff |
| Protective put | Hedge or event risk | Long stock needs downside floor | Hedge expires, event passes, thesis restored | Stock upside less premium | Floor defined by put | Long stock plus long put | Negative | Put liquid | Persistent hedge drag |
| Vol breakout | Vol compression before move | Low IV percentile, compression, momentum trigger | Close into expansion or failed breakout | Depends on structure | Defined by debit/spread | Long gamma/vega | Negative | Liquid near-money options | False breakout |
| Earnings vol | Pre/post earnings dislocation | Expected move mispriced, IV rank and history support thesis | Usually before event unless explicitly modeled | Defined by structure | Defined by structure | Long or short vol | Event-dependent | Very liquid options only | Surprise gap, IV crush, wide spreads |
| Mean reversion | Range or overextended move | RSI/stretch signal, high IV, support/resistance | Reversion target, invalidation level | Usually defined | Defined by spread | Often short gamma/vega | Positive if credit | High liquidity | Trend continuation |
| Trend-following | Strong trend, sector confirmation | Momentum, breakout, regime risk-on/off aligned | Trend invalidation, trailing stop, DTE floor | Structure-dependent | Defined by debit/spread | Directional, convex if long option | Negative to mixed | Liquid front/mid curve | Whipsaw |

Each strategy record should include:

- `allowed_regimes`
- `entry_rules`
- `exit_rules`
- `max_profit_formula`
- `max_loss_formula`
- `breakeven_formula`
- `greek_profile_expected`
- `time_decay_profile`
- `liquidity_requirements`
- `risk_level`
- `known_failure_modes`

---

## 4. Agentic workflow

### Market Regime Agent

Responsibilities:

- Classify market as trending, ranging, volatile, calm, risk-on, risk-off, pre-event, or post-event.
- Use market breadth, index trends, VIX level/change, realized volatility, correlations, sector rotation, and macro calendar.
- Output a structured regime label with confidence and evidence.

Hard rule: regime labels are features, not trade approvals.

### Volatility Agent

Responsibilities:

- Analyze IV, HV, IV rank, IV percentile, skew, term structure, and vol compression/expansion.
- Compare implied move to historical realized moves and event-specific realized moves.
- Flag contracts where IV is stale, surface is distorted, or spreads make IV unreliable.

### Directional Agent

Responsibilities:

- Build bullish, bearish, or neutral thesis using momentum, mean reversion, trend, sector behavior, macro context, and support/resistance.
- Produce invalidation conditions.
- Separate evidence from opinion.

### Event Agent

Responsibilities:

- Detect earnings, Fed decisions, CPI/jobs releases, product launches, litigation, regulatory actions, abnormal news, and sector shocks.
- Estimate event risk window and whether a position should be opened, avoided, hedged, or closed before the event.

### Options Structure Agent

Responsibilities:

- Select structure, expiration, strikes, target debit/credit, and order type based on thesis, vol regime, time horizon, liquidity, and risk budget.
- Generate payoff diagram, breakevens, Greeks, max profit/loss, and scenario table.
- Reject structures that do not match the thesis.

### Risk Agent

Responsibilities:

- Deterministic hard gate over every candidate.
- Enforce capital, Greek, drawdown, sector, correlation, liquidity, and event-risk limits.
- Produce a clear block reason when a trade is rejected.

### Portfolio Manager Agent

Responsibilities:

- Allocate risk across tickers, sectors, regimes, expiries, and strategies.
- Avoid hidden concentration from correlated positions.
- Prefer fewer high-conviction trades over many weak trades.

### Execution Agent

Responsibilities:

- Simulate limit order placement, bid/ask spread, slippage, queue priority, partial fills, quote staleness, liquidity limits, and commissions.
- Support order tactics: midpoint attempt, step toward natural, cancel/reprice, no-fill timeout.

### Performance Analyst Agent

Responsibilities:

- Review every closed trade.
- Attribute outcome to thesis quality, structure selection, entry timing, exit timing, liquidity, volatility behavior, and regime shift.
- Store lessons for future retrieval.

### Strategy Evolution Agent

Responsibilities:

- Update strategy weights from walk-forward evidence.
- Penalize overfit strategies, high drawdown, poor liquidity, and regime-specific underperformance.
- Never modify hard risk limits without explicit user approval.

---

## 5. Decision pipeline

```text
1. Scan market universe
2. Build latest point-in-time feature snapshot
3. Detect market, sector, volatility, and event regimes
4. Identify opportunities
5. Generate strategy candidates
6. Estimate payoff, probability, EV, liquidity, and execution cost
7. Run stress tests
8. Run deterministic risk validation
9. Select portfolio allocation
10. Send approved candidates to manual approval queue
11. Simulate paper execution
12. Monitor open positions and exit triggers
13. Adjust, roll, close, or expire positions
14. Post-trade review
15. Update strategy weights using walk-forward results
```

### Candidate scoring

Candidate score should be a weighted, auditable calculation:

```text
score =
  thesis_quality * 0.20
+ regime_fit * 0.15
+ volatility_edge * 0.15
+ liquidity_quality * 0.15
+ reward_to_risk * 0.10
+ stress_survivability * 0.10
+ portfolio_fit * 0.10
+ execution_quality * 0.05
```

Scores do not override risk blocks.

### Decision memo schema

```json
{
  "decision_id": "uuid",
  "created_at": "2026-06-15T14:30:00Z",
  "ticker": "SPY",
  "strategy": "bull_call_spread",
  "legs": [
    {"action": "buy_to_open", "right": "call", "strike": 520, "expiry": "2026-07-17", "qty": 1},
    {"action": "sell_to_open", "right": "call", "strike": 530, "expiry": "2026-07-17", "qty": 1}
  ],
  "thesis": "Trend continuation with defined debit risk.",
  "market_regime": "risk_on_trending",
  "volatility_regime": "normal_iv_mild_call_skew",
  "entry_price": 3.20,
  "entry_order_type": "limit_mid_then_step",
  "exit_plan": "Take profit at 60% of max gain or exit at 12 DTE.",
  "stop_loss_or_invalidation": "Close if spread loses 45% of debit or thesis score falls below 0.45.",
  "max_loss": 320,
  "max_gain": 680,
  "breakevens": [523.20],
  "probability_estimate": 0.44,
  "expected_value_after_costs": 42,
  "key_risks": ["trend reversal", "IV drop", "gap down", "wide spread on exit"],
  "stress_results": {
    "underlying_plus_5pct": 410,
    "underlying_minus_5pct": -250,
    "iv_crush": -80,
    "iv_expansion": 55,
    "liquidity_disappears": -130
  },
  "agent_votes": {
    "market_regime": "approve",
    "volatility": "neutral",
    "directional": "approve",
    "event": "approve",
    "risk": "approve"
  },
  "risk_agent_status": "approved",
  "risk_rejection_reason": null
}
```

---

## 6. Risk management rules

Risk settings should support three profiles: conservative, balanced, and aggressive. The defaults below are the balanced profile for a six-month paper competition.

| Rule | Balanced default |
|---|---:|
| Max loss per trade | 2.0% of NAV |
| Max capital at risk per underlying | 6.0% of NAV |
| Max capital at risk per sector/theme | 15.0% of NAV |
| Max daily loss | 2.0% of NAV |
| Max weekly loss | 5.0% of NAV |
| Drawdown risk reduction | Reduce new risk 50% at 8% drawdown |
| New trade block | Block new risk at 12% drawdown |
| Review-only mode | 15% drawdown |
| Max correlated positions | 3 to 4 active positions |
| Undefined-risk short options | Disabled by default |
| Minimum open interest | 500 contracts preferred |
| Minimum daily option volume | 100 contracts preferred |
| Max spread percent of mid | 10% preferred, 15% hard block |
| Quote staleness | Block stale quotes |
| Minimum expected reward/risk | Strategy-specific, usually > 1.2 after costs |

### Greek limits

Track exposures by portfolio, sector, underlying, expiry bucket, and strategy:

- Delta: cap directional exposure by NAV and beta-adjust to SPY.
- Gamma: cap short gamma exposure, especially into events and market stress.
- Vega: cap net vega loss under IV crush and IV spike scenarios.
- Theta: cap daily theta decay for long-premium positions.
- Rho: monitor for longer-dated positions and rate-sensitive underlyings.

### Stress tests before entry

Every candidate must be repriced under:

- Underlying +5%, -5%, +10%, -10%.
- IV crush.
- IV expansion.
- Gap up and gap down.
- Liquidity disappears: spread widens 2x to 5x and exit at unfavorable side.
- Earnings surprise.
- Market crash.
- Volatility spike.
- Correlation spike across portfolio.
- Early assignment and dividend risk where relevant.

A trade is blocked if stressed loss violates the per-trade or portfolio stress budget.

---

## 7. Backtesting and validation

Options backtesting must be event-driven and point-in-time. Daily close-only backtests are useful for rough strategy research, but not sufficient for execution-quality options strategy evaluation.

Validation framework:

- Historical backtesting with point-in-time option chains and quotes.
- Walk-forward testing with rolling train/validation/test windows.
- Out-of-sample testing by date, ticker, sector, and regime.
- Monte Carlo simulation over trade sequencing and slippage assumptions.
- Regime-based performance analysis.
- Transaction-cost and commission simulation.
- Bid/ask and partial-fill simulation.
- Overfitting detection using parameter sensitivity, performance decay, and deflated Sharpe checks.

Metrics:

- Total return.
- Annualized return.
- Sharpe ratio.
- Sortino ratio.
- Max drawdown.
- Calmar ratio.
- Win rate.
- Profit factor.
- Expected value per trade.
- Average win / average loss.
- Median win / median loss.
- Time in trade.
- Return by strategy.
- Return by regime.
- Greek exposure over time.
- Sector and ticker exposure.
- Slippage cost.
- Missed-fill rate.
- Strategy contribution to P&L.
- Tail loss contribution.

---

## 8. Dashboard and user experience

Required screens:

| Screen | Purpose |
|---|---|
| Portfolio overview | NAV, daily P&L, total P&L, drawdown, cash, exposure, active risk. |
| Open positions | Legs, Greeks, P&L, thesis status, exit triggers, stress loss. |
| Candidate trades | Ranked candidates, expected payoff, score, risk status, agent votes. |
| Trade approval | Payoff diagram, stress table, decision memo, approve/reject controls. |
| Risk dashboard | Limit usage, blocked trades, drawdown, liquidity, concentration. |
| Greeks dashboard | Delta/gamma/vega/theta/rho by ticker, sector, expiry, strategy. |
| Strategy performance | P&L attribution, win rate, EV, drawdown, regime performance. |
| Agent debate | Bull, bear, vol, event, and risk arguments side by side. |
| Post-trade review | What worked, what failed, what to adjust. |
| Replay | Reconstruct historical decisions from their frozen data snapshots. |
| Leaderboard | Six-month competition return, Sharpe, max drawdown, Calmar, rule violations. |

User controls:

- Approve or reject trades manually.
- Choose risk level: conservative, balanced, aggressive.
- Override candidate rejection only for non-hard informational warnings.
- See every risk block and why it happened.
- Compare strategies by regime and ticker.
- Replay past decisions.
- Export performance and audit reports.

Hard blocks should not be manually overridden without editing the risk profile and creating an audit entry.

---

## 9. Technical architecture

Pragmatic stack for one user now and up to about 10 parallel users:

| Layer | Recommended choice |
|---|---|
| Backend | FastAPI, Pydantic, SQLAlchemy, modular monolith |
| Frontend | Next.js, TypeScript, Tailwind, shadcn/ui, lightweight-charts or ECharts |
| Database | PostgreSQL plus TimescaleDB extension for time series |
| Research store | Parquet plus DuckDB for historical research snapshots |
| Vector search | pgvector for news, memos, research documents, and post-trade lessons |
| Workflow orchestration | Prefect for ingestion, feature jobs, backtests, and scheduled reviews |
| Agent orchestration | LangGraph for stateful, auditable multi-agent workflows |
| Market data | OPRA-capable provider for options; yfinance only as fallback for non-critical stock data |
| Broker/paper | Internal simulator mandatory; Alpaca/Tradier paper optional through BrokerProtocol |
| Backtesting | Start custom event-driven options simulator; evaluate LEAN when historical chains are available |
| Pricing/Greeks | QuantLib or internal Black-Scholes/binomial engine with model versioning |
| Monitoring | Structured logs, OpenTelemetry, Sentry, Prometheus/Grafana when deployed |
| Deployment | Docker Compose first; managed Postgres and one container service later |

Integration notes:

- Keep `BrokerProtocol` separate from `ExecutionSimulator`.
- Make paper fills stricter than broker paper fills.
- Store every proposed order even if rejected.
- Store every agent state transition for replay.
- Keep model prompts versioned with the code.

---

## 10. Data model extension

Core tables to add or extend:

```text
option_contracts
  id, underlying_symbol, occ_symbol, expiry, right, strike, multiplier,
  exercise_style, exchange, first_seen_at, last_seen_at

option_chain_snapshots
  id, underlying_symbol, snapshot_at, source_id, raw_payload_hash,
  underlying_price, transform_version

option_quotes
  id, contract_id, quote_at, bid, ask, bid_size, ask_size,
  mid, spread, spread_pct_mid, source_id, quote_condition

option_trades
  id, contract_id, trade_at, price, size, exchange, condition, source_id

option_greeks
  id, contract_id, measured_at, iv, delta, gamma, vega, theta, rho,
  source_id, model_version

option_open_interest
  id, contract_id, oi_date, open_interest, volume, source_id

market_regime_snapshots
  id, as_of, regime, confidence, features_json, model_version

volatility_surface_snapshots
  id, underlying_symbol, as_of, expiries_json, skew_json, term_structure_json,
  iv_rank, iv_percentile, model_version

option_strategy_templates
  id, name, version, allowed_regimes_json, leg_schema_json,
  risk_schema_json, payoff_formula_json

trade_candidates
  id, portfolio_id, strategy_template_id, ticker, created_at,
  status, score, thesis_json, structure_json, pricing_json,
  stress_json, memo_json, decision_snapshot_id

option_positions
  id, portfolio_id, candidate_id, status, opened_at, closed_at,
  realized_pnl, unrealized_pnl, current_greeks_json

option_position_legs
  id, position_id, contract_id, side, open_close_intent, qty,
  entry_price, exit_price, current_mark

strategy_weight_snapshots
  id, as_of, strategy_name, regime, weight, evidence_json, walk_forward_window

post_trade_reviews
  id, position_id, closed_at, review_json, lessons_embedding_id
```

Append-only audit tables:

```text
agent_runs
risk_checks
execution_events
decision_memos
audit_log
```

---

## 11. MVP roadmap

### Step 1 - MVP paper trading foundation

- Basic option data ingestion for a small universe of liquid ETFs and large-cap stocks.
- Option chain display with expiry, strike, bid/ask, volume, OI, IV, Greeks.
- Manual paper trade entry for single-leg options and vertical spreads.
- Internal option paper ledger.
- Basic execution simulator with bid/ask, commissions, spread slippage, no-fill and partial-fill states.
- Risk dashboard: max loss, exposure, drawdown, liquidity, and open Greeks.
- Strategy templates for long calls/puts, debit spreads, credit spreads, and iron condors.
- Basic backtesting on frozen chain snapshots.

### Step 2 - Agentic decision engine

- Multi-agent workflow: regime, volatility, directional, event, options structure, risk.
- Trade candidate generation.
- Strategy scoring.
- Structured decision memo.
- Deterministic risk agent with block reasons.
- Manual approval queue.
- Agent disagreement view.
- Post-trade review form.

### Step 3 - Competitive optimization

- Walk-forward learning.
- Regime-specific strategy allocation.
- Full stress testing.
- Automated post-trade analysis.
- Advanced dashboard and replay.
- Leaderboard for the six-month competition.
- Portfolio optimization with risk, correlation, liquidity, and drawdown constraints.

---

## 12. Competitive strategy for six months

The best competitive posture is not maximum leverage. It is controlled convexity, selective trade frequency, and fast learning.

Recommendations:

- Start with the most liquid underlyings: SPY, QQQ, IWM, sector ETFs, and mega-cap names with tight chains.
- Use defined-risk structures as the default.
- Avoid short-gamma exposure into earnings or macro events unless the edge is explicit and stress-tested.
- Keep a capital reserve for dislocations.
- Avoid overtrading. Prefer a small number of high-conviction trades with strong regime fit.
- Track performance by regime. A strategy that works in calm range-bound markets may fail during trend or crash regimes.
- Penalize strategies that win often but produce severe tail losses.
- Track missed fills and slippage as strategy costs, not operational noise.
- Reduce risk automatically during drawdowns.
- Review every closed trade and update strategy weights only from walk-forward evidence.

Winning scorecard:

```text
primary: six-month total return
secondary: max drawdown, Sharpe, Sortino, Calmar, profit factor
internal: EV per trade, stress loss, slippage, regime fit, rule violations
```

---

## 13. Blind spots and failure modes

| Failure mode | Mitigation |
|---|---|
| Bad or stale option data | Quote age filters, raw payload audit, alternate vendor checks. |
| Unrealistic paper fills | Internal simulator stricter than broker paper fills. |
| Lookahead bias | Point-in-time snapshots and decision replay. |
| Survivorship bias | Predefined universe snapshots, include delisted where possible. |
| IV surface errors | Vendor IV plus internal recalculation and sanity checks. |
| Liquidity disappears | Stress with spread widening and unfavorable exits. |
| Correlation spike | Portfolio-level correlation and crash stress tests. |
| Event gap risk | Event Agent blocks or resizes near earnings/macro. |
| Agent hallucination | Structured outputs, deterministic calculations, source citations, risk hard gate. |
| Overfitting | Walk-forward validation, parameter stability checks, strategy weight decay. |
| Hidden short-vol tail risk | Undefined-risk shorts disabled; short gamma tightly capped. |
| Early assignment/dividends | Assignment-risk model for American options and ex-div dates. |

---

## 14. Implementation notes for this repo

Recommended integration path with the current PortfolioVirtualTwin architecture:

1. Add an `options/` backend module with contracts, chains, Greeks, strategy templates, payoff math, and stress tests.
2. Extend `BrokerProtocol` with option-leg order objects, but keep the internal simulator as the default paper venue.
3. Extend the risk gateway with option-specific gates: max loss, liquidity, Greeks, undefined-risk short options, event proximity, and stress loss.
4. Add `OptionStrategyTwin` or extend `StrategyTwin` with an `asset_class = options` branch.
5. Add dashboard routes for option chain, candidate queue, payoff diagram, Greeks, and stress table.
6. Add backtest snapshots before adding automated strategy evolution.
7. Treat all LLM outputs as advisory metadata. All pricing, sizing, Greeks, stress, and risk decisions must be deterministic and persisted.
