from __future__ import annotations

from app.services.article_dedup import (
    canonicalize_url,
    find_semantic_duplicate,
    normalize_published_at,
)


def test_canonicalize_url_strips_tracking_params():
    raw = "https://Example.com/story?utm_source=twitter&id=1#section"
    assert canonicalize_url(raw) == "https://example.com/story?id=1"


def test_normalize_published_at_converts_rfc822_to_iso():
    normalized = normalize_published_at("Fri, 05 Jun 2026 21:38:13 GMT")
    assert normalized is not None
    assert "2026-06-05" in normalized


def test_find_semantic_duplicate_matches_rewritten_headline():
    candidates = [
        {"id": 7, "title": "US stocks slump as Big Tech shakes Wall Street", "summary": "Markets fell."},
    ]
    duplicate_id = find_semantic_duplicate(
        "US stocks slump as Big Tech shakes Wall Street",
        "Markets fell on Friday.",
        candidates,
        threshold=80,
    )
    assert duplicate_id == 7
