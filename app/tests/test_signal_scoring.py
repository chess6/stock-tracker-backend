"""Tests for canonical research_importance scoring."""

from __future__ import annotations

from datetime import date, timedelta

from app.services.signal_scoring import (
    RESEARCH_IMPORTANCE_WEIGHTS,
    SignalScoreInputs,
    compute_research_importance,
    compute_recency_factor,
    materiality_for_signal_type,
    research_importance_breakdown,
)


def test_materiality_going_concern_exceeds_generic_catalyst():
    assert materiality_for_signal_type("going_concern_8k") > materiality_for_signal_type("new_catalyst")


def test_research_importance_increases_with_portfolio_relevance():
    base = SignalScoreInputs(signal_type="rank_up", event_date=date.today().isoformat())
    held = SignalScoreInputs(
        signal_type="rank_up",
        event_date=date.today().isoformat(),
        in_portfolio=True,
        has_fundamentals=True,
    )
    assert compute_research_importance(held) > compute_research_importance(base)


def test_research_importance_uses_divergence_for_non_consensus():
    low = SignalScoreInputs(
        signal_type="narrative_divergence",
        event_date=date.today().isoformat(),
        divergence_score=0.2,
    )
    high = SignalScoreInputs(
        signal_type="narrative_divergence",
        event_date=date.today().isoformat(),
        divergence_score=0.9,
    )
    assert compute_research_importance(high) > compute_research_importance(low)


def test_recency_decays_with_age():
    today = compute_recency_factor(date.today().isoformat())
    stale = compute_recency_factor((date.today() - timedelta(days=30)).isoformat())
    assert today > stale


def test_breakdown_weights_sum_to_total():
    inputs = SignalScoreInputs(
        signal_type="insider_cluster_buy",
        event_date=date.today().isoformat(),
        magnitude=0.8,
        divergence_score=0.7,
        in_watchlist=True,
        has_fundamentals=True,
        abnormal_return_1d=0.04,
        confidence=0.9,
    )
    breakdown = research_importance_breakdown(inputs)
    factor_sum = round(
        breakdown["materiality"]
        + breakdown["surprise"]
        + breakdown["relevance"]
        + breakdown["nonConsensus"]
        + breakdown["tractability"]
        + breakdown["recency"],
        4,
    )
    assert breakdown["total"] == factor_sum
    assert 0.0 <= breakdown["total"] <= 1.0


# Golden fixtures shared with frontend signalScoringParity.test.js
PARITY_FIXTURES = [
    {
        "id": "going_concern_portfolio",
        "inputs": {
            "signal_type": "going_concern_8k",
            "event_date": "2026-06-20",
            "in_portfolio": True,
            "has_fundamentals": True,
            "as_of": "2026-06-23",
        },
        "expected": 0.6532,
    },
    {
        "id": "insider_cluster_high_intensity",
        "inputs": {
            "signal_type": "insider_cluster_buy",
            "event_date": "2026-06-22",
            "magnitude": 0.85,
            "abnormal_return_1d": 0.03,
            "has_fundamentals": True,
            "as_of": "2026-06-23",
        },
        "expected": 0.6651,
    },
    {
        "id": "stale_rank_move",
        "inputs": {
            "signal_type": "rank_up",
            "event_date": "2026-05-01",
            "magnitude": 0.4,
            "as_of": "2026-06-23",
        },
        "expected": 0.3923,
    },
]


def test_parity_fixtures_match_expected_scores():
    for fixture in PARITY_FIXTURES:
        inputs = SignalScoreInputs(**fixture["inputs"])
        score = compute_research_importance(inputs)
        assert score == fixture["expected"], f"{fixture['id']}: got {score}, expected {fixture['expected']}"


def test_weights_documented_for_frontend_sync():
    assert sum(RESEARCH_IMPORTANCE_WEIGHTS.values()) == 1.0
