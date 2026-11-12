from __future__ import annotations

import pytest

from app.services import event_classification
from app.services.event_classification import classify_events, classify_events_rules


def _events(text: str) -> set[str]:
    return {item.event_type for item in classify_events_rules(text)}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Apple beats estimates as iPhone sales surge", "earnings_beat"),
        ("Intel misses earnings expectations, shares fall", "earnings_miss"),
        ("Microsoft raises guidance on cloud demand", "guidance_increase"),
        ("Fed signals two rate cuts this year", "macroeconomic"),
        ("Treasury yields climb after hot CPI print", "macroeconomic"),
        ("EU hits Google with record fine over ad tech", "regulation_legal_risk"),
        ("Bank fined $200 million by regulators", "regulation_legal_risk"),
        ("SEC opens probe into accounting practices", "regulation_legal_risk"),
        ("TSMC expands chip capacity in Arizona", "semiconductor_supply_chain"),
        ("Retailer announces layoffs of 2,000 workers", "layoffs"),
    ],
)
def test_rules_detect_events(text, expected):
    assert expected in _events(text)


@pytest.mark.parametrize(
    ("text", "not_expected"),
    [
        # Negated statements are not the event.
        ("Nike did not beat estimates this quarter", "earnings_beat"),
        ("Company says there will be no layoffs this year", "layoffs"),
        ("Retailer fails to raise guidance despite strong holidays", "guidance_increase"),
        # Ambiguous words that previously matched.
        ("Analysts say the consumer is doing fine heading into the holidays", "regulation_legal_risk"),
        ("Grass-fed beef prices hit a new high at grocers", "macroeconomic"),
        ("Pension funds fed into the selloff late in the session", "macroeconomic"),
        ("Blue-chip stocks drift as investors await earnings", "semiconductor_supply_chain"),
    ],
)
def test_rules_ignore_negations_and_ambiguous_words(text, not_expected):
    assert not_expected not in _events(text)


def test_negation_only_suppresses_the_negated_match():
    text = "Nike did not beat estimates last year, but this quarter it beats estimates handily"
    assert "earnings_beat" in _events(text)


class _CountingEmbedFn:
    """Deterministic fake embedding: one-hot on a keyword, counting calls."""

    cache_key = "fake-model::cpu"

    def __init__(self):
        self.calls = 0

    def __call__(self, text: str) -> list[float]:
        self.calls += 1
        lowered = text.lower()
        return [
            1.0 if "buyback" in lowered or "repurchase" in lowered else 0.0,
            1.0 if "layoff" in lowered else 0.0,
            0.1,
        ]


def test_anchor_vectors_are_embedded_once_per_model(monkeypatch):
    monkeypatch.setattr(event_classification, "_ANCHOR_VECTOR_CACHE", {})
    embed_fn = _CountingEmbedFn()
    anchors = len(event_classification.EVENT_ANCHORS)

    # No rule matches this text, so classification falls back to embeddings.
    for _ in range(5):
        classify_events("Board approves plan to repurchase stock over two years", embed_fn=embed_fn)

    # Anchors once + one article vector per call (previously anchors were re-embedded every call).
    assert embed_fn.calls == anchors + 5


def test_embedding_fallback_finds_nearest_anchor(monkeypatch):
    monkeypatch.setattr(event_classification, "_ANCHOR_VECTOR_CACHE", {})
    results = classify_events("Board approves plan to repurchase stock over two years", embed_fn=_CountingEmbedFn())
    assert results and results[0].event_type == "stock_buyback"
    assert results[0].method == "embedding"
