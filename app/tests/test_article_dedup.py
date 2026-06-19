from __future__ import annotations

from app.services.article_dedup import canonicalize_url


def test_canonicalize_url_strips_tracking_and_normalizes_seeking_alpha():
    assert canonicalize_url(
        "https://seekingalpha.com/article/123456-some-slug?utm_source=rss&ref=abc"
    ) == "https://seekingalpha.com/article/123456"
    assert canonicalize_url(
        "https://www.marketwatch.com/story/foo?partner=mw&cmpid=abc"
    ) == "https://www.marketwatch.com/story/foo"
