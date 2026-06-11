"""Backtest engine: translates Strategy Twin rule AST into vectorbt signal matrices.

Transparency rule: conditions that reference metrics not computable from price/volume
history (fundamental & sentiment scores) are NOT silently dropped — they are recorded
in BacktestRun.skipped_rules and shown to the user."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from app.data.provider import cached_history, compute_indicator_frames
from app.strategy.twin import BACKTESTABLE_METRICS, RuleCondition, StrategyTwin

DEFAULT_FEES = 0.001  # 10 bps per side — honest-ish friction for paper comparison


def _condition_mask(cond: RuleCondition, frames: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    if cond.metric not in frames:
        return None
    series = frames[cond.metric]
    value = cond.value
    match cond.op:
        case ">":
            return series > value
        case ">=":
            return series >= value
        case "<":
            return series < value
        case "<=":
            return series <= value
        case "==":
            return series == value
        case "!=":
            return series != value
    return None


def run_backtest(twin: StrategyTwin, symbols: list[str], start: datetime, end: datetime,
                 initial_cash: float = 100_000.0) -> dict:
    import vectorbt as vbt

    benchmark = twin.benchmark
    all_symbols = sorted(set(s.upper() for s in symbols) | {benchmark})
    history = cached_history(all_symbols, start, end)
    if benchmark not in history:
        raise ValueError(f"no data for benchmark {benchmark}")
    trade_symbols = [s for s in all_symbols if s != benchmark and s in history]
    if not trade_symbols:
        raise ValueError("no market data for any universe symbol")

    closes = pd.DataFrame({s: history[s]["close"] for s in trade_symbols + [benchmark]}).dropna(how="all")
    volumes = pd.DataFrame({s: history[s]["volume"] for s in trade_symbols + [benchmark]})
    volumes = volumes.reindex(closes.index)
    frames = compute_indicator_frames(closes, volumes, benchmark)
    frames = {k: v[trade_symbols] for k, v in frames.items()}
    closes_t = closes[trade_symbols]

    # entries: AND of all backtestable entry conditions
    skipped: list[dict] = []
    entry_mask = pd.DataFrame(True, index=closes_t.index, columns=trade_symbols)
    used_entry = 0
    for cond in twin.entry_rules:
        mask = _condition_mask(cond, frames)
        if mask is None:
            skipped.append({"rule": cond.describe(), "kind": "entry",
                            "reason": "metric not computable from price history (agent-evaluated at proposal time)"})
            continue
        entry_mask &= mask.fillna(False)
        used_entry += 1
    if used_entry == 0:
        # no technical entry rule — fall back to momentum regime so the backtest is meaningful
        entry_mask = frames["price_above_200_day_average"].fillna(False)
        skipped.append({"rule": "(fallback) price_above_200_day_average == true", "kind": "entry",
                        "reason": "no backtestable entry rules; using trend filter fallback"})

    # exits: OR of backtestable exit conditions; drawdown_from_entry maps to a stop-loss
    exit_mask = pd.DataFrame(False, index=closes_t.index, columns=trade_symbols)
    sl_stop: float | None = None
    for cond in twin.exit_rules:
        if cond.metric == "drawdown_from_entry":
            try:
                sl_stop = float(cond.value) / 100.0
            except (TypeError, ValueError):
                skipped.append({"rule": cond.describe(), "kind": "exit", "reason": "non-numeric stop value"})
            continue
        if cond.metric not in BACKTESTABLE_METRICS:
            skipped.append({"rule": cond.describe(), "kind": "exit",
                            "reason": "metric not computable from price history (monitored live instead)"})
            continue
        mask = _condition_mask(cond, frames)
        if mask is not None:
            exit_mask |= mask.fillna(False)

    max_positions = max(1, twin.risk_management.max_number_of_positions)
    size = min(1.0 / max_positions, twin.risk_management.max_position_weight_pct / 100.0)

    pf = vbt.Portfolio.from_signals(
        closes_t,
        entries=entry_mask,
        exits=exit_mask,
        size=size,
        size_type="percent",
        init_cash=initial_cash,
        cash_sharing=True,
        group_by=True,
        call_seq="auto",
        fees=DEFAULT_FEES,
        sl_stop=sl_stop,
        freq="1D",
    )

    equity = pf.value()
    returns = equity.pct_change().fillna(0.0)
    bench_close = closes[benchmark].reindex(equity.index).ffill()
    bench_equity = bench_close / bench_close.iloc[0] * initial_cash
    bench_returns = bench_equity.pct_change().fillna(0.0)

    metrics = {
        "total_return_pct": float(pf.total_return() * 100),
        "benchmark_return_pct": float((bench_equity.iloc[-1] / initial_cash - 1) * 100),
        "max_drawdown_pct": abs(float(pf.max_drawdown() * 100)),
        "sharpe": _safe(pf.sharpe_ratio()),
        "sortino": _safe(pf.sortino_ratio()),
        "volatility_pct": float(returns.std() * np.sqrt(252) * 100),
        "n_trades": int(pf.trades.count()),
        "win_rate_pct": _safe(pf.trades.win_rate() * 100) if pf.trades.count() else 0.0,
        "final_equity": float(equity.iloc[-1]),
        "fees_paid": float(pf.orders.fees.sum()) if pf.orders.count() else 0.0,
    }

    step = max(1, len(equity) // 500)  # cap payload size for the UI chart
    curve = {
        "dates": [d.strftime("%Y-%m-%d") for d in equity.index[::step]],
        "strategy": [round(float(v), 2) for v in equity.iloc[::step]],
        "benchmark": [round(float(v), 2) for v in bench_equity.iloc[::step]],
    }
    return {"metrics": metrics, "equity_curve": curve, "skipped_rules": skipped,
            "returns": returns, "bench_returns": bench_returns}


def quantstats_report(returns: pd.Series, bench_returns: pd.Series, path: str, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")  # backtests run in worker threads; GUI backends crash there
    import quantstats as qs

    returns.index = pd.to_datetime(returns.index)
    bench_returns.index = pd.to_datetime(bench_returns.index)
    qs.reports.html(returns, benchmark=bench_returns, output=path, title=title, download_filename=path)


def _safe(v) -> float:
    try:
        f = float(v)
        return 0.0 if (np.isnan(f) or np.isinf(f)) else f
    except (TypeError, ValueError):
        return 0.0
