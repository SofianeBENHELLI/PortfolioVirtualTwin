"""Tracked stocks: open-source data snapshots (refreshed on demand) + latest
Bull/Bear agent signals per symbol."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.graphs import gather_symbol_data, run_bull_bear
from app.agents.llm import require_llm
from app.audit.service import audit
from app.core.db import get_db
from app.core.security import get_current_user
from app.models import MarketDataSnapshot, Recommendation, User, WatchedStock
from app.strategy import service as strategy_service

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class AddSymbol(BaseModel):
    symbol: str


class BullBearRequest(BaseModel):
    strategy_id: int | None = None  # optional: only adds style/horizon context
    symbols: list[str] = []         # explicit subset (e.g. a single stock)
    portfolio_id: int | None = None  # analyze the holdings of this portfolio
    # precedence: symbols > portfolio_id > whole watchlist


def _latest_signals(db: Session, user_id: int, symbols: list[str]) -> dict[str, dict]:
    """Newest bull / bear / judge recommendation per symbol."""
    if not symbols:
        return {}
    rows = db.scalars(
        select(Recommendation)
        .where(Recommendation.user_id == user_id, Recommendation.symbol.in_(symbols))
        .order_by(Recommendation.created_at.desc()).limit(600)
    ).all()
    out: dict[str, dict] = {s: {} for s in symbols}
    for r in rows:
        perspective = (r.data_used or {}).get("perspective")
        if perspective not in ("bull", "bear", "judge") or perspective in out[r.symbol]:
            continue
        out[r.symbol][perspective] = {
            "signal_strength": (r.data_used or {}).get("signal_strength", r.confidence * 100),
            "action": r.action,
            "rating": (r.data_used or {}).get("rating") or (r.data_used or {}).get("verdict_action"),
            "thesis": r.thesis,
            "key_points": (r.data_used or {}).get("key_points", []),
            "invalidation": r.invalidation,
            "report": (r.data_used or {}).get("report"),
            "created_at": r.created_at.isoformat(),
        }
    return out


def _payload(db: Session, user_id: int, watched: list[WatchedStock]) -> list[dict]:
    symbols = [w.symbol for w in watched]
    snaps = {
        s.symbol: s for s in
        db.scalars(select(MarketDataSnapshot).where(MarketDataSnapshot.symbol.in_(symbols))).all()
    } if symbols else {}
    signals = _latest_signals(db, user_id, symbols)
    out = []
    for w in watched:
        snap = snaps.get(w.symbol)
        ind = dict(snap.indicators) if snap and snap.indicators else {}
        fundamentals = ind.pop("fundamentals", {})
        out.append({
            "symbol": w.symbol,
            "added_at": w.added_at.isoformat(),
            "price": snap.price if snap else None,
            "data_as_of": snap.as_of.isoformat() if snap else None,
            "indicators": ind,
            "fundamentals": fundamentals,
            "bull": signals.get(w.symbol, {}).get("bull"),
            "bear": signals.get(w.symbol, {}).get("bear"),
            "judge": signals.get(w.symbol, {}).get("judge"),
        })
    return out


@router.get("/signals")
def signals(symbols: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Latest bull/bear signal per symbol — works for any symbols (e.g. portfolio
    holdings), not just tracked ones."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:50]
    if not syms:
        raise HTTPException(422, "symbols query param required (comma-separated)")
    return _latest_signals(db, user.id, syms)


@router.get("")
def list_watchlist(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    watched = db.scalars(select(WatchedStock).where(WatchedStock.user_id == user.id)
                         .order_by(WatchedStock.added_at)).all()
    return _payload(db, user.id, watched)


@router.post("")
def add_symbol(payload: AddSymbol, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    symbol = payload.symbol.strip().upper()
    if not symbol or len(symbol) > 10 or not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(422, "Invalid symbol")
    exists = db.scalar(select(WatchedStock).where(WatchedStock.user_id == user.id,
                                                  WatchedStock.symbol == symbol))
    if exists:
        raise HTTPException(409, f"{symbol} is already tracked")
    db.add(WatchedStock(user_id=user.id, symbol=symbol))
    audit(db, "watchlist.added", user_id=user.id, entity="watched_stock", entity_id=symbol)
    db.commit()
    return {"ok": True, "symbol": symbol}


@router.delete("/{symbol}")
def remove_symbol(symbol: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(WatchedStock).where(WatchedStock.user_id == user.id,
                                               WatchedStock.symbol == symbol.upper()))
    if row is None:
        raise HTTPException(404, "Not tracked")
    db.delete(row)
    audit(db, "watchlist.removed", user_id=user.id, entity="watched_stock", entity_id=symbol.upper())
    db.commit()
    return {"ok": True}


@router.post("/refresh")
def refresh_data(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Re-fetch open-source data (prices, technicals, fundamentals) for every tracked stock."""
    watched = db.scalars(select(WatchedStock).where(WatchedStock.user_id == user.id)).all()
    if not watched:
        raise HTTPException(409, "Watchlist is empty — add symbols first")
    symbols = [w.symbol for w in watched]
    data = gather_symbol_data(symbols, "SPY")
    now = datetime.now(timezone.utc)
    from app.models import Asset
    for sym, entry in data.items():
        snap = db.scalar(select(MarketDataSnapshot).where(MarketDataSnapshot.symbol == sym))
        indicators = {**entry["indicators"], "fundamentals": entry["fundamentals"]}
        if snap is None:
            db.add(MarketDataSnapshot(symbol=sym, price=entry["price"], indicators=indicators, as_of=now))
        else:
            snap.price = entry["price"]
            snap.indicators = indicators
            snap.as_of = now
        # keep the Asset registry (sector data for the risk gateway) up to date
        sector = entry["fundamentals"].get("sector")
        if sector:
            asset = db.scalar(select(Asset).where(Asset.symbol == sym))
            if asset is None:
                db.add(Asset(symbol=sym, name=str(entry["fundamentals"].get("longName", "")),
                             sector=str(sector)))
            else:
                asset.sector = str(sector)
    audit(db, "watchlist.data_refreshed", user_id=user.id,
          payload={"symbols": symbols, "fetched": list(data.keys())})
    db.commit()
    missing = [s for s in symbols if s not in data]
    return {"refreshed": list(data.keys()), "no_data": missing}


@router.post("/bullbear")
def bull_bear(payload: BullBearRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Run the Bull and Bear agents on tracked stocks (or an explicit subset).
    A strategy is optional — it only adds style/horizon context to the prompts."""
    require_llm(db, user.id)
    if payload.strategy_id is not None:
        version, twin = strategy_service.active_twin(db, user.id, payload.strategy_id)
        version_id = version.id
    else:
        from app.strategy.twin import StrategyTwin
        twin = StrategyTwin(strategy_name="(no strategy — general analysis)")
        version_id = None
    symbols = [s.upper() for s in payload.symbols]
    if not symbols and payload.portfolio_id is not None:
        from app.execution import service as exec_service
        from app.models import Position
        portfolio = exec_service.get_portfolio(db, user.id, payload.portfolio_id)
        symbols = [p.symbol for p in db.scalars(
            select(Position).where(Position.portfolio_id == portfolio.id, Position.qty > 0)).all()]
        if not symbols:
            raise HTTPException(409, f"'{portfolio.name}' has no holdings to analyze")
    if not symbols:
        symbols = [w.symbol for w in db.scalars(
            select(WatchedStock).where(WatchedStock.user_id == user.id)).all()]
    if not symbols:
        raise HTTPException(409, "Watchlist is empty — add symbols first")
    run = run_bull_bear(db, user.id, twin, version_id, symbols)
    if run.status == "failed":
        from app.agents.llm import friendly_llm_error
        raise HTTPException(502, f"Bull/Bear run failed: {friendly_llm_error(run.error)}")
    return {"agent_run_id": run.id, "summary": run.summary,
            "tokens": run.prompt_tokens + run.completion_tokens}
