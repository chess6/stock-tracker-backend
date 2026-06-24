from __future__ import annotations

from app.db import get_db
from app.repositories import Repository
from app.services.article_dedup import canonicalize_url


def _seed_article(repo: Repository, key: str, title: str, summary: str) -> int:
    return repo.upsert_article(
        {
            "canonical_url": f"https://example.com/{key}",
            "url_hash": f"hash-{key}",
            "title": title,
            "summary": summary,
            "published_at": "2026-06-22T03:30:00Z",
            "fetched_at": "2026-06-22T03:35:00Z",
            "source_domain": "news.google.com",
            "raw_source": "test",
        },
        skip_dedup=True,
    )


def test_deduplicate_scans_most_recent_articles(app):
    """Near-duplicate Google News items (same headline, different publisher
    suffix) must be deduped even when older canonical articles exist beyond the
    lookback window — the fuzzy pass must scan the newest articles, not oldest."""
    with app.app_context():
        repo = Repository(get_db())
        for i in range(5):
            _seed_article(repo, f"filler-{i}", f"Unrelated market headline number {i}", "Filler body")
        morningstar_id = _seed_article(
            repo,
            "valens-morningstar",
            "Valens Semiconductor Appoints Karine Pinto-Flomenboim as Chief Financial Officer - Morningstar",
            "Valens Semiconductor Appoints Karine Pinto-Flomenboim as Chief Financial Officer Morningstar",
        )
        pr_id = _seed_article(
            repo,
            "valens-prnewswire",
            "Valens Semiconductor Appoints Karine Pinto-Flomenboim as Chief Financial Officer - PR Newswire",
            "Valens Semiconductor Appoints Karine Pinto-Flomenboim as Chief Financial Officer PR Newswire",
        )

        repo.deduplicate_articles(lookback=3)

        pr_row = repo.conn.execute(
            "SELECT duplicate_of_article_id FROM articles WHERE id = ?",
            (pr_id,),
        ).fetchone()
        assert pr_row["duplicate_of_article_id"] == morningstar_id


def test_deduplicate_matches_across_large_id_gap(app):
    """Same story from two feeds can land far apart in id order (e.g. a direct
    publisher RSS item vs the Google News copy polled much later). The fuzzy
    pass must compare against all canonical articles in the lookback window, not
    just a small rolling window of recent ids."""
    with app.app_context():
        repo = Repository(get_db())
        original_id = _seed_article(
            repo,
            "oracle-bbc",
            "Tech giant Oracle cuts 21,000 jobs as it embraces AI",
            "The cuts are part of a wider trend among tech firms as they spend billions on AI.",
        )
        for i in range(100):
            _seed_article(repo, f"gap-{i}", f"Completely unrelated story {i}", "Body")
        google_id = _seed_article(
            repo,
            "oracle-google",
            "Tech giant Oracle cuts 21,000 jobs as it embraces AI - BBC",
            "Tech giant Oracle cuts 21,000 jobs as it embraces AI BBC",
        )

        repo.deduplicate_articles()

        google_row = repo.conn.execute(
            "SELECT duplicate_of_article_id FROM articles WHERE id = ?",
            (google_id,),
        ).fetchone()
        assert google_row["duplicate_of_article_id"] == original_id


def test_canonicalize_url_strips_tracking_and_normalizes_seeking_alpha():
    assert canonicalize_url(
        "https://seekingalpha.com/article/123456-some-slug?utm_source=rss&ref=abc"
    ) == "https://seekingalpha.com/article/123456"
    assert canonicalize_url(
        "https://www.marketwatch.com/story/foo?partner=mw&cmpid=abc"
    ) == "https://www.marketwatch.com/story/foo"
