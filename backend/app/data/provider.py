"""Market data behind a small provider protocol so OpenBB or paid feeds can slot in
later without touching consumers. yfinance is the default; Alpaca's free IEX feed is
used when keys are configured."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol

import numpy as np
import pandas as pd

from app.core.config import get_settings


class MarketDataProvider(Protocol):
    def history(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
        """Per symbol: DataFrame indexed by date with columns open, high, low, close, volume."""
        ...

    def latest_prices(self, symbols: list[str]) -> dict[str, float]: ...


class YFinanceProvider:
    def history(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
        import yfinance as yf

        out: dict[str, pd.DataFrame] = {}
        raw = yf.download(symbols, start=start, end=end, group_by="ticker",
                          auto_adjust=True, progress=False, threads=True)
        if raw is None or raw.empty:
            return out
        for sym in symbols:
            try:
                df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
            if not df.empty:
                out[sym] = df
        return out

    def latest_prices(self, symbols: list[str]) -> dict[str, float]:
        end = datetime.now(timezone.utc)
        hist = self.history(symbols, end - timedelta(days=7), end)
        return {s: float(df["close"].iloc[-1]) for s, df in hist.items() if not df.empty}


class AlpacaDataProvider:
    """Free IEX feed via alpaca-py. Requires API keys (paper keys work)."""

    def __init__(self, api_key: str, secret_key: str) -> None:
        from alpaca.data.historical import StockHistoricalDataClient

        self._client = StockHistoricalDataClient(api_key, secret_key)

    def history(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        req = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Day, start=start, end=end)
        bars = self._client.get_stock_bars(req).df
        out: dict[str, pd.DataFrame] = {}
        if bars.empty:
            return out
        for sym in symbols:
            try:
                df = bars.xs(sym, level="symbol")[["open", "high", "low", "close", "volume"]]
            except KeyError:
                continue
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            out[sym] = df
        return out

    def latest_prices(self, symbols: list[str]) -> dict[str, float]:
        from alpaca.data.requests import StockLatestTradeRequest

        trades = self._client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbols))
        return {sym: float(t.price) for sym, t in trades.items()}


_provider_lock = threading.Lock()
_provider: MarketDataProvider | None = None


def get_provider() -> MarketDataProvider:
    global _provider
    with _provider_lock:
        if _provider is None:
            s = get_settings()
            if s.market_data_provider == "alpaca" and s.alpaca_api_key:
                _provider = AlpacaDataProvider(s.alpaca_api_key, s.alpaca_secret_key)
            else:
                _provider = YFinanceProvider()
        return _provider


# ---------------------------------------------------------------------------
# In-memory OHLCV cache (per process; persisted snapshots live in MarketDataSnapshot)
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_history_cache: dict[tuple[str, str, str], pd.DataFrame] = {}


def cached_history(symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
    key_start, key_end = start.date().isoformat(), end.date().isoformat()
    missing = [s for s in symbols if (s, key_start, key_end) not in _history_cache]
    if missing:
        fetched = get_provider().history(missing, start, end)
        with _cache_lock:
            for sym, df in fetched.items():
                _history_cache[(sym, key_start, key_end)] = df
    return {
        s: _history_cache[(s, key_start, key_end)]
        for s in symbols
        if (s, key_start, key_end) in _history_cache
    }


# ---------------------------------------------------------------------------
# Indicators — the single implementation used by backtests AND live monitoring,
# so a rule means the same thing in both places.
# ---------------------------------------------------------------------------

def compute_indicator_frames(closes: pd.DataFrame, volumes: pd.DataFrame, benchmark: str) -> dict[str, pd.DataFrame]:
    """Vectorized indicator time series for every symbol (columns) — used by backtests."""
    ret_126 = closes.pct_change(126)
    momentum_score = ret_126.rank(axis=1, pct=True) * 100.0
    sma200 = closes.rolling(200).mean()
    sma50 = closes.rolling(50).mean()
    ret_63 = closes.pct_change(63)
    bench_63 = ret_63[benchmark] if benchmark in ret_63.columns else ret_63.mean(axis=1)
    relative_strength = (ret_63.sub(bench_63, axis=0)) * 100.0

    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_14 = 100 - 100 / (1 + rs)

    volatility_30d = closes.pct_change().rolling(30).std() * np.sqrt(252) * 100.0
    volume_confirmation = volumes > volumes.rolling(20).mean()

    return {
        "momentum_score": momentum_score,
        "price_above_200_day_average": closes > sma200,
        "price_above_50_day_average": closes > sma50,
        "relative_strength": relative_strength,
        "rsi_14": rsi_14,
        "volatility_30d": volatility_30d,
        "volume_confirmation": volume_confirmation,
    }


def latest_indicators(symbol_df: pd.DataFrame, benchmark_df: pd.DataFrame | None) -> dict:
    """Latest scalar indicator values for one symbol — used by live monitoring/research."""
    close, volume = symbol_df["close"], symbol_df["volume"]
    out: dict = {}
    if len(close) >= 200:
        out["price_above_200_day_average"] = bool(close.iloc[-1] > close.rolling(200).mean().iloc[-1])
    if len(close) >= 50:
        out["price_above_50_day_average"] = bool(close.iloc[-1] > close.rolling(50).mean().iloc[-1])
    if len(close) >= 127:
        out["momentum_6m_return_pct"] = float((close.iloc[-1] / close.iloc[-127] - 1) * 100)
    if len(close) >= 64 and benchmark_df is not None and len(benchmark_df) >= 64:
        sym_r = close.iloc[-1] / close.iloc[-64] - 1
        b = benchmark_df["close"]
        out["relative_strength"] = float((sym_r - (b.iloc[-1] / b.iloc[-64] - 1)) * 100)
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
        loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
        out["rsi_14"] = float(100 - 100 / (1 + gain / loss)) if loss else 100.0
    if len(close) >= 31:
        out["volatility_30d"] = float(close.pct_change().rolling(30).std().iloc[-1] * np.sqrt(252) * 100)
    if len(volume) >= 20:
        out["volume_confirmation"] = bool(volume.iloc[-1] > volume.rolling(20).mean().iloc[-1])
    return out
