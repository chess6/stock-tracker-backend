from __future__ import annotations

from app.services.article_ranking import (
    RankInputs,
    compute_news_importance_score,
    compute_rank_score,
    compute_recency_score,
    source_quality_score,
)


def test_source_quality_prefers_feed_weight():
    assert source_quality_score("reddit.com", feed_source_weight=0.1) == 0.1
    assert source_quality_score("benzinga.com") == 0.8


def test_compute_rank_score_uses_feed_source_weight():
    inputs = RankInputs(
        sentiment_score=0.8,
        vader_compound=0.8,
        source_domain="reddit.com",
        source_weight=0.1,
        novelty_score=0.7,
    )
    score = compute_rank_score(inputs)
    assert score > 0
    assert score < compute_rank_score(
        RankInputs(
            sentiment_score=0.8,
            vader_compound=0.8,
            source_domain="sec.gov",
            source_weight=1.0,
            novelty_score=0.7,
        )
    )


def test_compute_news_importance_score_includes_recency_and_entity_confidence():
    fresh = RankInputs(
        sentiment_score=0.6,
        vader_compound=0.6,
        source_domain="sec.gov",
        source_weight=1.0,
        novelty_score=0.8,
        entity_confidence=0.9,
        published_at="2099-01-01T12:00:00Z",
        event_confidence=0.9,
    )
    stale = RankInputs(
        sentiment_score=0.6,
        vader_compound=0.6,
        source_domain="sec.gov",
        source_weight=1.0,
        novelty_score=0.8,
        entity_confidence=0.9,
        published_at="2020-01-01T12:00:00Z",
        event_confidence=0.9,
    )
    assert compute_news_importance_score(fresh) > compute_news_importance_score(stale)


def test_recency_decay_curve():
    assert compute_recency_score("2099-01-01T12:00:00Z") == 1.0
    assert compute_recency_score(None) == 0.5
