from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import audit
from app.core.db import get_db
from app.core.security import get_current_user
from app.models import OptionContract, OptionQuote, OptionTradeCandidate, Portfolio, User
from app.options.payoff import summarize_payoff
from app.options.risk import evaluate_candidate
from app.options.schemas import CandidateCreate, ContractUpsert, PayoffRequest, QuoteCreate, RiskCheckRequest
from app.options.templates import STRATEGY_TEMPLATES

router = APIRouter(prefix="/api/options", tags=["options"])


@router.get("/strategy-templates")
def strategy_templates(user: User = Depends(get_current_user)):
    return STRATEGY_TEMPLATES


@router.post("/payoff")
def payoff(payload: PayoffRequest, user: User = Depends(get_current_user)):
    summary = summarize_payoff(payload.legs, payload.underlying_price)
    return {
        "net_debit": summary.net_debit,
        "max_loss": summary.max_loss,
        "max_gain": summary.max_gain,
        "breakevens": summary.breakevens,
        "unlimited_loss": summary.unlimited_loss,
        "unlimited_gain": summary.unlimited_gain,
        "pnl_points": summary.pnl_points,
    }


@router.post("/risk-check")
def risk_check(payload: RiskCheckRequest, user: User = Depends(get_current_user)):
    if payload.account_nav <= 0:
        raise HTTPException(422, "account_nav must be positive")
    return evaluate_candidate(payload.legs, payload.underlying_price, payload.account_nav, payload.risk_profile)


@router.post("/contracts")
def upsert_contract(payload: ContractUpsert, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    underlying = payload.underlying_symbol.strip().upper()
    occ = payload.occ_symbol.strip().upper()
    if not underlying or not occ:
        raise HTTPException(422, "underlying_symbol and occ_symbol are required")
    contract = db.scalar(select(OptionContract).where(OptionContract.occ_symbol == occ))
    if contract is None:
        contract = OptionContract(underlying_symbol=underlying, occ_symbol=occ)
        db.add(contract)
    contract.underlying_symbol = underlying
    contract.expiry = payload.expiry
    contract.right = payload.right
    contract.strike = payload.strike
    contract.multiplier = payload.multiplier
    contract.exercise_style = payload.exercise_style
    contract.exchange = payload.exchange
    contract.last_seen_at = datetime.now(timezone.utc)
    audit(db, "option.contract_upserted", user_id=user.id, entity="option_contract", entity_id=occ)
    db.commit()
    return _contract_out(contract)


@router.post("/contracts/{occ_symbol}/quotes")
def add_quote(occ_symbol: str, payload: QuoteCreate, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    contract = db.scalar(select(OptionContract).where(OptionContract.occ_symbol == occ_symbol.upper()))
    if contract is None:
        raise HTTPException(404, "Option contract not found")
    mid = (payload.bid + payload.ask) / 2
    spread = payload.ask - payload.bid
    quote = OptionQuote(
        contract_id=contract.id,
        bid=payload.bid,
        ask=payload.ask,
        bid_size=payload.bid_size,
        ask_size=payload.ask_size,
        mid=mid,
        spread=spread,
        spread_pct_mid=(spread / mid) if mid > 0 else 1.0,
        volume=payload.volume,
        open_interest=payload.open_interest,
        implied_volatility=payload.implied_volatility,
        delta=payload.delta,
        gamma=payload.gamma,
        vega=payload.vega,
        theta=payload.theta,
        rho=payload.rho,
        source_id=payload.source_id,
    )
    db.add(quote)
    audit(db, "option.quote_added", user_id=user.id, entity="option_contract", entity_id=contract.occ_symbol,
          payload={"bid": payload.bid, "ask": payload.ask, "volume": payload.volume, "open_interest": payload.open_interest})
    db.commit()
    return _quote_out(quote)


@router.get("/contracts")
def list_contracts(underlying: str | None = None, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    q = select(OptionContract)
    if underlying:
        q = q.where(OptionContract.underlying_symbol == underlying.upper())
    rows = db.scalars(q.order_by(OptionContract.underlying_symbol, OptionContract.expiry, OptionContract.strike).limit(500)).all()
    return [_contract_out(c) for c in rows]


@router.post("/candidates")
def create_candidate(payload: CandidateCreate, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    if payload.account_nav <= 0:
        raise HTTPException(422, "account_nav must be positive")
    if payload.portfolio_id is not None:
        portfolio = db.get(Portfolio, payload.portfolio_id)
        if portfolio is None or portfolio.user_id != user.id:
            raise HTTPException(404, "Portfolio not found")
    evaluation = evaluate_candidate(payload.legs, payload.underlying_price, payload.account_nav, payload.risk_profile)
    memo = _decision_memo(payload, evaluation)
    candidate = OptionTradeCandidate(
        user_id=user.id,
        portfolio_id=payload.portfolio_id,
        ticker=(payload.ticker or memo["ticker"]).upper(),
        strategy=payload.strategy,
        status="risk_passed" if evaluation["risk_passed"] else "risk_blocked",
        risk_passed=evaluation["risk_passed"],
        score=evaluation["score"],
        thesis=payload.thesis,
        market_regime=payload.market_regime,
        volatility_regime=payload.volatility_regime,
        legs=[leg.model_dump() for leg in payload.legs],
        payoff=evaluation["payoff"],
        stress_results=evaluation["stress_results"],
        risk_checks=evaluation["risk_checks"],
        memo=memo,
    )
    db.add(candidate)
    db.flush()
    audit(db, "option.candidate_created", user_id=user.id, entity="option_trade_candidate",
          entity_id=str(candidate.id), payload={"ticker": candidate.ticker, "strategy": candidate.strategy,
                                                "risk_passed": candidate.risk_passed})
    db.commit()
    return _candidate_out(candidate)


@router.get("/candidates")
def list_candidates(portfolio_id: int | None = None, status: str | None = None,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(OptionTradeCandidate).where(OptionTradeCandidate.user_id == user.id)
    if portfolio_id is not None:
        q = q.where(OptionTradeCandidate.portfolio_id == portfolio_id)
    if status:
        q = q.where(OptionTradeCandidate.status == status)
    rows = db.scalars(q.order_by(OptionTradeCandidate.created_at.desc()).limit(100)).all()
    return [_candidate_out(c) for c in rows]


def _decision_memo(payload: CandidateCreate, evaluation: dict) -> dict:
    failed = [check for check in evaluation["risk_checks"] if not check["passed"]]
    payoff = evaluation["payoff"]
    max_loss = payoff["max_loss"]
    max_gain = payoff["max_gain"]
    ev = None
    if payload.probability_estimate is not None and max_loss is not None:
        gain_for_ev = max_gain if max_gain is not None else max_loss * 3
        ev = payload.probability_estimate * gain_for_ev - (1 - payload.probability_estimate) * max_loss
    return {
        "ticker": payload.ticker.upper() if payload.ticker else "",
        "strategy": payload.strategy,
        "thesis": payload.thesis,
        "market_regime": payload.market_regime,
        "volatility_regime": payload.volatility_regime,
        "entry_price": payoff["net_debit"],
        "exit_plan": payload.exit_plan,
        "stop_loss_or_invalidation": payload.invalidation,
        "max_loss": max_loss,
        "max_gain": max_gain,
        "breakevens": payoff["breakevens"],
        "probability_estimate": payload.probability_estimate,
        "expected_value_estimate": round(ev, 2) if ev is not None else None,
        "key_risks": [check["detail"] for check in failed],
        "risk_agent_status": "approved" if evaluation["risk_passed"] else "blocked",
        "reason_for_rejection": "; ".join(check["name"] for check in failed) if failed else None,
    }


def _contract_out(contract: OptionContract) -> dict:
    return {
        "id": contract.id,
        "underlying_symbol": contract.underlying_symbol,
        "occ_symbol": contract.occ_symbol,
        "expiry": contract.expiry,
        "right": contract.right,
        "strike": contract.strike,
        "multiplier": contract.multiplier,
        "exercise_style": contract.exercise_style,
        "exchange": contract.exchange,
        "first_seen_at": contract.first_seen_at.isoformat(),
        "last_seen_at": contract.last_seen_at.isoformat(),
    }


def _quote_out(quote: OptionQuote) -> dict:
    return {
        "id": quote.id,
        "contract_id": quote.contract_id,
        "quote_at": quote.quote_at.isoformat(),
        "bid": quote.bid,
        "ask": quote.ask,
        "bid_size": quote.bid_size,
        "ask_size": quote.ask_size,
        "mid": quote.mid,
        "spread": quote.spread,
        "spread_pct_mid": quote.spread_pct_mid,
        "volume": quote.volume,
        "open_interest": quote.open_interest,
        "implied_volatility": quote.implied_volatility,
        "delta": quote.delta,
        "gamma": quote.gamma,
        "vega": quote.vega,
        "theta": quote.theta,
        "rho": quote.rho,
        "source_id": quote.source_id,
    }


def _candidate_out(candidate: OptionTradeCandidate) -> dict:
    return {
        "id": candidate.id,
        "portfolio_id": candidate.portfolio_id,
        "ticker": candidate.ticker,
        "strategy": candidate.strategy,
        "status": candidate.status,
        "risk_passed": candidate.risk_passed,
        "score": candidate.score,
        "thesis": candidate.thesis,
        "market_regime": candidate.market_regime,
        "volatility_regime": candidate.volatility_regime,
        "legs": candidate.legs,
        "payoff": candidate.payoff,
        "stress_results": candidate.stress_results,
        "risk_checks": candidate.risk_checks,
        "memo": candidate.memo,
        "created_at": candidate.created_at.isoformat(),
    }
