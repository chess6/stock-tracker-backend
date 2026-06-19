from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

# Keys are article URL hostnames (not product branding); values are relative source-quality weights.
SOURCE_QUALITY: dict[str, float] = {
    "reuters.com": 1.0,
    "bloomberg.com": 1.0,
    "wsj.com": 0.95,
    "ft.com": 0.95,
    "sec.gov": 0.9,
    "federalreserve.gov": 1.0,
    "bls.gov": 0.9,
    "home.treasury.gov": 1.0,
    "fred.stlouisfed.org": 0.95,
    "cnbc.com": 0.85,
    "marketwatch.com": 0.8,
    "benzinga.com": 0.8,
    "finance.yahoo.com": 0.75,
    "seekingalpha.com": 0.7,
    "npr.org": 0.7,
    "bbc.co.uk": 0.7,
    "reddit.com": 0.55,
}


@dataclass
class RankInputs:
    sentiment_score: float | None
    vader_compound: float | None
    source_domain: str | None
    engagement_score: float | None = None
    novelty_score: float | None = None
    abnormal_return_1d: float | None = None
    event_confidence: float | None = None
    source_weight: float | None = None
    entity_confidence: float | None = None
    published_at: str | None = None


def source_quality_score(domain: str | None, *, feed_source_weight: float | None = None) -> float:
    if feed_source_weight is not None:
        return max(0.0, min(1.0, float(feed_source_weight)))
    if not domain:
        return 0.5
    domain = domain.lower().strip()
    for key, score in SOURCE_QUALITY.items():
        if key in domain:
            return score
    return 0.55


def sentiment_intensity(sentiment_score: float | None, vader_compound: float | None) -> float:
    if sentiment_score is not None:
        return min(1.0, abs(sentiment_score))
    if vader_compound is not None:
        return min(1.0, abs(vader_compound))
    return 0.0


def compute_recency_score(published_at: str | None, *, half_life_hours: float = 48.0) -> float:
    if not published_at:
        return 0.5
    try:
        normalized = published_at.replace("Z", "+00:00")
        published = datetime.fromisoformat(normalized)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_hours = max(0.0, (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).total_seconds() / 3600.0)
        return round(math.exp(-age_hours / half_life_hours), 4)
    except (TypeError, ValueError, OverflowError):
        return 0.5


def compute_rank_score(inputs: RankInputs) -> float:
    intensity = sentiment_intensity(inputs.sentiment_score, inputs.vader_compound)
    source = source_quality_score(inputs.source_domain, feed_source_weight=inputs.source_weight)
    engagement = inputs.engagement_score if inputs.engagement_score is not None else 0.3
    novelty = inputs.novelty_score if inputs.novelty_score is not None else 0.5
    predictive = 0.5
    if inputs.abnormal_return_1d is not None:
        predictive = min(1.0, 0.5 + abs(inputs.abnormal_return_1d) * 5)
    event_boost = (inputs.event_confidence or 0.0) * 0.15
    return round(
        intensity * 0.3
        + source * 0.2
        + engagement * 0.1
        + novelty * 0.2
        + predictive * 0.15
        + event_boost,
        4,
    )


def compute_news_importance_score(inputs: RankInputs) -> float:
    intensity = sentiment_intensity(inputs.sentiment_score, inputs.vader_compound)
    source = source_quality_score(inputs.source_domain, feed_source_weight=inputs.source_weight)
    entity_conf = inputs.entity_confidence if inputs.entity_confidence is not None else 0.3
    novelty = inputs.novelty_score if inputs.novelty_score is not None else 0.5
    recency = compute_recency_score(inputs.published_at)
    predictive = 0.5
    if inputs.abnormal_return_1d is not None:
        predictive = min(1.0, 0.5 + abs(inputs.abnormal_return_1d) * 5)
    event_boost = (inputs.event_confidence or 0.0) * 0.15
    return round(
        intensity * 0.25
        + source * 0.20
        + entity_conf * 0.10
        + novelty * 0.20
        + recency * 0.10
        + predictive * 0.10
        + event_boost,
        4,
    )


def compute_novelty_score(max_similarity: float | None) -> float:
    """Higher when article is less similar to recent duplicates (similarity in 0..1)."""
    if max_similarity is None:
        return 0.7
    return round(max(0.0, min(1.0, 1.0 - max_similarity)), 4)


def score_factor_breakdown(inputs: RankInputs) -> dict[str, float]:
    intensity = sentiment_intensity(inputs.sentiment_score, inputs.vader_compound)
    source = source_quality_score(inputs.source_domain, feed_source_weight=inputs.source_weight)
    entity_conf = inputs.entity_confidence if inputs.entity_confidence is not None else 0.3
    novelty = inputs.novelty_score if inputs.novelty_score is not None else 0.5
    recency = compute_recency_score(inputs.published_at)
    predictive = 0.5
    if inputs.abnormal_return_1d is not None:
        predictive = min(1.0, 0.5 + abs(inputs.abnormal_return_1d) * 5)
    event_boost = (inputs.event_confidence or 0.0) * 0.15
    return {
        "intensity": round(intensity * 0.25, 4),
        "sourceWeight": round(source * 0.20, 4),
        "entityConfidence": round(entity_conf * 0.10, 4),
        "novelty": round(novelty * 0.20, 4),
        "recency": round(recency * 0.10, 4),
        "predictive": round(predictive * 0.10, 4),
        "eventBoost": round(event_boost, 4),
        "total": compute_news_importance_score(inputs),
    }
