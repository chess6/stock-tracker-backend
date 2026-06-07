from __future__ import annotations

from dataclasses import dataclass

SOURCE_QUALITY: dict[str, float] = {
    "reuters.com": 1.0,
    "bloomberg.com": 1.0,
    "wsj.com": 0.95,
    "ft.com": 0.95,
    "sec.gov": 0.9,
    "cnbc.com": 0.85,
    "marketwatch.com": 0.8,
    "finance.yahoo.com": 0.75,
    "seekingalpha.com": 0.7,
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


def source_quality_score(domain: str | None) -> float:
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


def compute_rank_score(inputs: RankInputs) -> float:
    intensity = sentiment_intensity(inputs.sentiment_score, inputs.vader_compound)
    source = source_quality_score(inputs.source_domain)
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


def compute_novelty_score(max_similarity: float | None) -> float:
    """Higher when article is less similar to recent duplicates (similarity in 0..1)."""
    if max_similarity is None:
        return 0.7
    return round(max(0.0, min(1.0, 1.0 - max_similarity)), 4)
