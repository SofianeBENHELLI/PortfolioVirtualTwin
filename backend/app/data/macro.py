"""Macroeconomic & geopolitical data layer.

Free sources (verified June 2026):
- yfinance batch download for market indicators (VIX, oil, gold, rates, dollar, equities, FX)
- FRED (optional, free API key) for curve spread / CPI / unemployment / recession signals
- GDELT DOC 2.0 (keyless) TimelineVol = share of global news coverage matching a
  war/sanctions query — fast geopolitical intensity signal; ArtList = headlines
- GPR daily index (Caldara-Iacoviello) XLS — authoritative geopolitical risk, 1 fetch/day

All regime flags are DETERMINISTIC with documented thresholds; the LLM macro agent only
narrates, never computes the flags.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pandas as pd

from app.core.config import get_settings

log = logging.getLogger("pvt.macro")

MACRO_TICKERS: dict[str, tuple[str, str]] = {
    # key: (yfinance symbol, display label)
    "vix": ("^VIX", "VIX (fear index)"),
    "sp500": ("^GSPC", "S&P 500"),
    "wti": ("CL=F", "Oil — WTI"),
    "brent": ("BZ=F", "Oil — Brent"),
    "gold": ("GC=F", "Gold"),
    "us10y": ("^TNX", "US 10Y yield"),
    "dxy": ("DX-Y.NYB", "Dollar index"),
    "eurusd": ("EURUSD=X", "EUR/USD"),
}

WAR_QUERY = '(war OR invasion OR sanctions OR "military strike" OR "oil embargo")'

# ---------------------------------------------------------------- indicators


def fetch_indicators() -> dict[str, dict]:
    """One batched yfinance download → per-indicator value, changes, z-score, trend."""
    import yfinance as yf

    symbols = [v[0] for v in MACRO_TICKERS.values()]
    end = datetime.now(timezone.utc)
    raw = yf.download(symbols, start=end - timedelta(days=420), end=end,
                      group_by="ticker", auto_adjust=True, progress=False, threads=True)
    out: dict[str, dict] = {}
    for key, (symbol, label) in MACRO_TICKERS.items():
        try:
            close = (raw[symbol] if isinstance(raw.columns, pd.MultiIndex) else raw)["Close"].dropna()
        except KeyError:
            continue
        if len(close) < 60:
            continue
        value = float(close.iloc[-1])
        ret5 = close.pct_change(5).dropna()
        entry = {
            "label": label,
            "value": value,
            "chg_1d_pct": float((close.iloc[-1] / close.iloc[-2] - 1) * 100) if len(close) >= 2 else 0.0,
            "chg_5d_pct": float((close.iloc[-1] / close.iloc[-6] - 1) * 100) if len(close) >= 6 else 0.0,
            "chg_30d_pct": float((close.iloc[-1] / close.iloc[-22] - 1) * 100) if len(close) >= 22 else 0.0,
            "z_5d": float((ret5.iloc[-1] - ret5.mean()) / ret5.std()) if len(ret5) > 20 and ret5.std() else 0.0,
            "above_200d": bool(value > close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None,
            "sparkline": [round(float(v), 2) for v in close.iloc[-60:].tolist()],
        }
        out[key] = entry
    return out


# ---------------------------------------------------------------- FRED (optional)

FRED_SERIES = {"t10y2y": "T10Y2Y", "t10y3m": "T10Y3M", "cpi_yoy_proxy": "CPIAUCSL",
               "fed_funds": "FEDFUNDS", "unemployment": "UNRATE"}


def fetch_fred() -> dict[str, float]:
    key = get_settings().fred_api_key
    if not key:
        return {}
    out: dict[str, float] = {}
    with httpx.Client(timeout=15) as client:
        for name, series in FRED_SERIES.items():
            try:
                r = client.get("https://api.stlouisfed.org/fred/series/observations",
                               params={"series_id": series, "api_key": key, "file_type": "json",
                                       "sort_order": "desc", "limit": 1})
                obs = r.json()["observations"][0]
                if obs["value"] not in (".", ""):
                    out[name] = float(obs["value"])
            except Exception:
                continue
    return out


# ------------------------------------------------------- GDELT war intensity + news


GDELT_MIN_INTERVAL = 5.5  # GDELT allows one request every 5 seconds
_last_gdelt_call = 0.0


def _gdelt_get(params: dict) -> dict:
    """Rate-limit-aware GDELT call (≥5s spacing, one retry)."""
    import time as _time
    global _last_gdelt_call
    last_text = ""
    for attempt in range(4):
        wait = GDELT_MIN_INTERVAL - (_time.monotonic() - _last_gdelt_call)
        if wait > 0:
            _time.sleep(wait)
        _last_gdelt_call = _time.monotonic()
        r = httpx.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params,
                      timeout=20, headers={"User-Agent": "PortfolioVirtualTwin/1.0"})
        try:
            return r.json()
        except ValueError:
            last_text = r.text[:120]
            if "limit requests" in r.text:
                _time.sleep(GDELT_MIN_INTERVAL * (attempt + 1))
                continue
            break
    raise RuntimeError(f"GDELT non-JSON response: {last_text}")


def _gdelt_timeline(timespan: str) -> list[float]:
    data = _gdelt_get({"query": f"{WAR_QUERY} sourcelang:eng", "mode": "TimelineVol",
                       "timespan": timespan, "format": "json"})
    series = data.get("timeline", [{}])[0].get("data", [])
    return [float(p["value"]) for p in series if p.get("value") is not None]


def fetch_war_signal() -> dict:
    """Conflict coverage intensity: current 7d mean vs 3-month baseline (z-score),
    plus headlines for the agent."""
    out: dict = {"available": False}
    try:
        recent = _gdelt_timeline("7d")
        baseline = _gdelt_timeline("3m")
        if recent and len(baseline) > 20:
            cur = float(np.mean(recent))
            mu, sd = float(np.mean(baseline)), float(np.std(baseline))
            out.update({"available": True, "coverage_pct": cur,
                        "z_score": (cur - mu) / sd if sd else 0.0})
    except Exception as exc:
        log.warning("GDELT timeline failed: %s", exc)
    try:
        arts = _gdelt_get({"query": f"{WAR_QUERY} sourcelang:eng", "mode": "ArtList",
                           "maxrecords": 25, "timespan": "2d", "sort": "hybridrel",
                           "format": "json"}).get("articles", [])
        seen: set[str] = set()
        headlines = []
        for a in arts:
            title = a.get("title", "").strip()
            if title and title not in seen:
                seen.add(title)
                headlines.append({"title": title, "source": a.get("domain", ""), "url": a.get("url", "")})
            if len(headlines) >= 12:
                break
        out["headlines"] = headlines
    except Exception as exc:
        log.warning("GDELT artlist failed: %s", exc)
        out.setdefault("headlines", [])
    return out


def fetch_gpr() -> dict:
    """Caldara-Iacoviello daily Geopolitical Risk index (authoritative, slow-moving)."""
    try:
        import io
        r = httpx.get("https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls",
                      timeout=30, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (research)"})
        df = pd.read_excel(io.BytesIO(r.content))
        col = next((c for c in df.columns if str(c).upper() in ("GPRD", "GPR")), None)
        if col is None:
            return {"available": False}
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(series) < 100:
            return {"available": False}
        value = float(series.iloc[-1])
        pct = float((series.tail(365) < value).mean() * 100)
        return {"available": True, "value": value, "percentile_1y": pct}
    except Exception as exc:
        log.warning("GPR fetch failed: %s", exc)
        return {"available": False}


# ---------------------------------------------------------------- regimes

REGIME_THRESHOLDS = {
    "vix_calm_below": 16.0,
    "vix_high_at": 24.0,
    "oil_shock_5d_pct": 10.0,
    "gold_rush_30d_pct": 5.0,
    "risk_off_equity_5d_pct": -3.0,
    "war_gdelt_z": 1.5,
    "war_gpr_percentile": 85.0,
}


def compute_regimes(ind: dict, fred: dict, war: dict, gpr: dict) -> dict:
    """Deterministic regime flags. Thresholds in REGIME_THRESHOLDS (documented)."""
    t = REGIME_THRESHOLDS
    vix = ind.get("vix", {}).get("value")
    vol_regime = None
    if vix is not None:
        vol_regime = "calm" if vix < t["vix_calm_below"] else ("high" if vix >= t["vix_high_at"] else "elevated")

    sp5 = ind.get("sp500", {}).get("chg_5d_pct", 0.0)
    gold5 = ind.get("gold", {}).get("chg_5d_pct", 0.0)
    risk_off = bool((vix is not None and vix >= t["vix_high_at"])
                    or (sp5 <= t["risk_off_equity_5d_pct"] and gold5 > 0))

    wti5 = ind.get("wti", {}).get("chg_5d_pct", 0.0)
    oil_shock = bool(abs(wti5) >= t["oil_shock_5d_pct"])

    gold = ind.get("gold", {})
    gold_rush = bool(gold.get("above_200d") and gold.get("chg_30d_pct", 0.0) >= t["gold_rush_30d_pct"])

    war_level = "low"
    gdelt_z = war.get("z_score", 0.0) if war.get("available") else 0.0
    gpr_pct = gpr.get("percentile_1y", 0.0) if gpr.get("available") else 0.0
    if gdelt_z >= t["war_gdelt_z"] or gpr_pct >= t["war_gpr_percentile"]:
        war_level = "high"
    elif gdelt_z >= t["war_gdelt_z"] / 2 or gpr_pct >= 70.0:
        war_level = "elevated"

    curve_inverted = None
    if "t10y2y" in fred:
        curve_inverted = bool(fred["t10y2y"] < 0)

    return {
        "volatility_regime": vol_regime,
        "risk_off": risk_off,
        "oil_shock": oil_shock,
        "oil_shock_direction": "crash" if wti5 <= -t["oil_shock_5d_pct"] else ("spike" if wti5 >= t["oil_shock_5d_pct"] else None),
        "gold_rush": gold_rush,
        "war_risk": war_level,
        "curve_inverted": curve_inverted,
        "thresholds": t,
    }


# ---------------------------------------------------------------- orchestration

_refresh_lock = threading.Lock()


def build_snapshot() -> dict:
    """Fetch everything and compute regimes. Pure data, no DB."""
    with _refresh_lock:
        ind = fetch_indicators()
        fred = fetch_fred()
        war = fetch_war_signal()
        gpr = fetch_gpr()
        regimes = compute_regimes(ind, fred, war, gpr)
        return {"indicators": ind, "fred": fred, "war": war, "gpr": gpr, "regimes": regimes}
