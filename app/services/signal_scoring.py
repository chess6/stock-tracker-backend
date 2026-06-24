"""Canonical research_importance scoring for unified Signal objects."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# Materiality by signal_type — higher = more research-worthy (0..1).
SIGNAL_TYPE_MATERIALITY: dict[str, float] = {
    "going_concern_8k": 0.95,
    "bankruptcy": 0.95,
    "restatement": 0.88,
    "auditor_change": 0.82,
    "activist_13d": 0.90,
    "insider_cluster_buy": 0.85,
    "new_insider_cluster": 0.85,
    "narrative_divergence": 0.75,
    "rerating_candidate": 0.78,
    "high_conviction": 0.72,
    "risk_flag": 0.70,
    "earnings_miss": 0.68,
    "guidance_cut": 0.72,
    "earnings_beat": 0.62,
    "guidance_increase": 0.60,
    "mergers_acquisitions": 0.75,
    "regulation_legal_risk": 0.70,
    "management_change": 0.65,
    "restructuring": 0.68,
    "thesis_catalyst": 0.65,
    "new_catalyst": 0.58,
    "rank_up": 0.55,
    "rank_down": 0.52,
    "score_improvement": 0.58,
    "asset_sale": 0.55,
    "stock_buyback": 0.50,
    "insider_buying": 0.55,
    "debt_reduction": 0.52,
    "capital_raise": 0.54,
    "fcf_inflection": 0.62,
    "margin_recovery_burst": 0.60,
    "unusual_volume": 0.68,
    "earnings": 0.62,
    "earnings_upcoming": 0.58,
    "earnings_today": 0.78,
    "short_interest_spike": 0.55,
}
DEFAULT_MATERIALITY = 0.45

# Factor weights — must stay in sync with frontend signalScoring.js
RESEARCH_IMPORTANCE_WEIGHTS = {
    "materiality": 0.30,
    "surprise": 0.20,
    "relevance": 0.15,
    "nonConsensus": 0.15,
    "tractability": 0.10,
    "recency": 0.10,
}

PORTFOLIO_RELEVANCE_BOOST = 0.25
WATCHLIST_RELEVANCE_BOOST = 0.12
RECENCY_HALF_LIFE_DAYS = 14.0


@dataclass(frozen=True)
class SignalScoreInputs:
    signal_type: str
    event_date: str | None = None
    detected_at: str | None = None
    magnitude: float | None = None
    abnormal_return_1d: float | None = None
    divergence_score: float | None = None
    in_portfolio: bool = False
    in_watchlist: bool = False
    has_fundamentals: bool = False
    confidence: float | None = None
    as_of: str | None = None


def materiality_for_signal_type(signal_type: str) -> float:
    key = (signal_type or "").strip().lower()
    return SIGNAL_TYPE_MATERIALITY.get(key, DEFAULT_MATERIALITY)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10]).date()
    except ValueError:
        return None


def compute_recency_factor(
    event_date: str | None,
    *,
    half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    as_of: date | None = None,
) -> float:
    """Exponential decay from event_date; 1.0 today → ~0.5 at half-life."""
    parsed = _parse_date(event_date)
    if parsed is None:
        return 0.5
    anchor = as_of or date.today()
    age_days = max(0.0, (anchor - parsed).days)
    return round(math.exp(-age_days / half_life_days), 4)


def compute_surprise_factor(
    abnormal_return_1d: float | None,
    magnitude: float | None,
    *,
    confidence: float | None = None,
) -> float:
    components: list[float] = []
    if abnormal_return_1d is not None:
        components.append(min(1.0, 0.4 + abs(float(abnormal_return_1d)) * 4.0))
    if magnitude is not None:
        components.append(min(1.0, max(0.0, float(magnitude))))
    if confidence is not None:
        components.append(min(1.0, max(0.0, float(confidence))))
    if not components:
        return 0.35
    return round(sum(components) / len(components), 4)


def compute_relevance_factor(*, in_portfolio: bool, in_watchlist: bool) -> float:
    score = 0.35
    if in_portfolio:
        score += PORTFOLIO_RELEVANCE_BOOST
    elif in_watchlist:
        score += WATCHLIST_RELEVANCE_BOOST
    return round(min(1.0, score), 4)


def compute_non_consensus_factor(divergence_score: float | None) -> float:
    if divergence_score is None:
        return 0.35
    return round(min(1.0, max(0.0, float(divergence_score))), 4)


def compute_tractability_factor(has_fundamentals: bool) -> float:
    return 0.75 if has_fundamentals else 0.40


def research_importance_breakdown(inputs: SignalScoreInputs) -> dict[str, float]:
    materiality = materiality_for_signal_type(inputs.signal_type)
    surprise = compute_surprise_factor(
        inputs.abnormal_return_1d,
        inputs.magnitude,
        confidence=inputs.confidence,
    )
    relevance = compute_relevance_factor(
        in_portfolio=inputs.in_portfolio,
        in_watchlist=inputs.in_watchlist,
    )
    non_consensus = compute_non_consensus_factor(inputs.divergence_score)
    tractability = compute_tractability_factor(inputs.has_fundamentals)
    as_of = _parse_date(inputs.as_of) if inputs.as_of else None
    recency = compute_recency_factor(inputs.event_date or inputs.detected_at, as_of=as_of)
    weights = RESEARCH_IMPORTANCE_WEIGHTS
    total = (
        materiality * weights["materiality"]
        + surprise * weights["surprise"]
        + relevance * weights["relevance"]
        + non_consensus * weights["nonConsensus"]
        + tractability * weights["tractability"]
        + recency * weights["recency"]
    )
    return {
        "materiality": round(materiality * weights["materiality"], 4),
        "surprise": round(surprise * weights["surprise"], 4),
        "relevance": round(relevance * weights["relevance"], 4),
        "nonConsensus": round(non_consensus * weights["nonConsensus"], 4),
        "tractability": round(tractability * weights["tractability"], 4),
        "recency": round(recency * weights["recency"], 4),
        "total": round(min(1.0, max(0.0, total)), 4),
    }


def compute_research_importance(inputs: SignalScoreInputs) -> float:
    """Rank research time (0..1), distinct from news_importance on articles."""
    return research_importance_breakdown(inputs)["total"]
