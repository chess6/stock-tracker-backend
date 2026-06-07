from __future__ import annotations

import requests

from app.db import get_db
from app.repositories import Repository
from app.services.news import (
    DEFAULT_FEED_TIMEOUT_SECONDS,
    DEFAULT_MAX_ARTICLES_PER_FEED,
    NewsService,
)


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("bad response")


class FakeSession:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def get(self, url: str, timeout: int = 20):
        if url not in self.mapping:
            raise requests.ConnectionError(f"Failed to reach {url}")
        return FakeResponse(self.mapping[url])


def test_news_ingestion_dedupes_by_url_and_links_ticker(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "AAPL", "name": "Apple Inc", "cik": "0000320193"}])

        feed_xml = """
        <rss><channel>
          <item>
            <title>AAPL launches new device</title>
            <link>https://example.com/apple-story</link>
            <description>Apple Inc ships something new.</description>
            <pubDate>2025-01-01</pubDate>
          </item>
        </channel></rss>
        """
        html = "<html><body><article>AAPL launches a new device for Apple Inc customers.</article></body></html>"
        session = FakeSession(
            {
                "https://example.com/feed.xml": feed_xml,
                "https://example.com/apple-story": html,
            }
        )
        service = NewsService(repo=repo, session=session, cache_ttl_seconds=10)

        first = service.ingest_feed("https://example.com/feed.xml", "Example Feed", "tech")
        second = service.ingest_feed("https://example.com/feed.xml", "Example Feed", "tech")

        assert first["articlesProcessed"] == 1
        assert second["articlesProcessed"] == 1
        news = repo.get_company_news("AAPL")
        assert len(news) == 1
        assert news[0]["title"] == "AAPL launches new device"


def test_semantic_dedup_marks_near_duplicate_titles(app):
    with app.app_context():
        repo = Repository(get_db())
        primary_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/original",
                "url_hash": "hash-original",
                "title": "Apple launches new device in California",
                "summary": "Apple Inc announced a launch event.",
                "published_at": "2026-06-05T12:00:00Z",
                "fetched_at": "2026-06-05T12:05:00Z",
                "content_hash": "content-original",
                "raw_source": "test",
            }
        )
        duplicate_id = repo.upsert_article(
            {
                "canonical_url": "https://publisher.example/apple-launch",
                "url_hash": "hash-duplicate",
                "title": "Apple launches new device in Calif.",
                "summary": "Apple announced a launch event today.",
                "published_at": "2026-06-05T13:00:00Z",
                "fetched_at": "2026-06-05T13:05:00Z",
                "content_hash": "content-duplicate",
                "raw_source": "test",
            }
        )
        row = repo.conn.execute(
            "SELECT duplicate_of_article_id FROM articles WHERE id = ?",
            (duplicate_id,),
        ).fetchone()
        assert row["duplicate_of_article_id"] == primary_id


def test_ingest_default_feeds_continues_after_feed_failure(app, monkeypatch):
    with app.app_context():
        repo = Repository(get_db())
        service = NewsService(repo=repo, session=FakeSession({}), cache_ttl_seconds=10)

        def fake_default_feeds():
            return [
                {"name": "Broken Feed", "feed_url": "https://broken.example/feed.xml", "category": "test"},
                {"name": "Good Feed", "feed_url": "https://good.example/feed.xml", "category": "test"},
            ]

        feed_xml = """
        <rss><channel>
          <item>
            <title>Working story</title>
            <link>https://good.example/story</link>
            <description>Summary text</description>
            <pubDate>2025-01-01</pubDate>
          </item>
        </channel></rss>
        """
        session = FakeSession(
            {
                "https://good.example/feed.xml": feed_xml,
                "https://good.example/story": "<html><body>Story body</body></html>",
            }
        )
        service.session = session
        monkeypatch.setattr(service, "default_feeds", fake_default_feeds)

        payload = service.ingest_default_feeds()

        assert payload["feedsProcessed"] == 2
        assert payload["failedFeeds"] == 1
        assert payload["articlesProcessed"] == 1
        assert payload["results"][0]["status"] == "error"
        assert payload["results"][1]["status"] == "ok"


def test_ingest_feed_caps_max_articles_per_feed(app):
    with app.app_context():
        repo = Repository(get_db())
        feed_xml = """
        <rss><channel>
          <item>
            <title>Story one</title>
            <link>https://example.com/story-1</link>
            <description>First summary</description>
            <pubDate>2025-01-01</pubDate>
          </item>
          <item>
            <title>Story two</title>
            <link>https://example.com/story-2</link>
            <description>Second summary</description>
            <pubDate>2025-01-02</pubDate>
          </item>
        </channel></rss>
        """
        session = FakeSession(
            {
                "https://example.com/feed.xml": feed_xml,
                "https://example.com/story-1": "<html><body>Story one body</body></html>",
                "https://example.com/story-2": "<html><body>Story two body</body></html>",
            }
        )
        service = NewsService(repo=repo, session=session, cache_ttl_seconds=10)

        result = service.ingest_feed(
            "https://example.com/feed.xml",
            "Example Feed",
            "tech",
            max_articles=1,
            extract_articles=False,
        )

        assert result["articlesProcessed"] == 1


def test_ingest_feed_max_articles_zero_processes_all_entries(app):
    with app.app_context():
        repo = Repository(get_db())
        feed_xml = """
        <rss><channel>
          <item>
            <title>Story one</title>
            <link>https://example.com/story-1</link>
            <description>First summary</description>
            <pubDate>2025-01-01</pubDate>
          </item>
          <item>
            <title>Story two</title>
            <link>https://example.com/story-2</link>
            <description>Second summary</description>
            <pubDate>2025-01-02</pubDate>
          </item>
        </channel></rss>
        """
        session = FakeSession(
            {
                "https://example.com/feed.xml": feed_xml,
                "https://example.com/story-1": "<html><body>Story one body</body></html>",
                "https://example.com/story-2": "<html><body>Story two body</body></html>",
            }
        )
        service = NewsService(repo=repo, session=session, cache_ttl_seconds=10)

        result = service.ingest_feed(
            "https://example.com/feed.xml",
            "Example Feed",
            "tech",
            max_articles=0,
            extract_articles=False,
        )

        assert result["articlesProcessed"] == 2


def test_ingest_default_feeds_reports_default_max_articles_per_feed(app, monkeypatch):
    with app.app_context():
        repo = Repository(get_db())
        service = NewsService(repo=repo, session=FakeSession({}), cache_ttl_seconds=10)

        def fake_default_feeds():
            return [
                {"name": "Good Feed", "feed_url": "https://good.example/feed.xml", "category": "test"},
            ]

        feed_xml = """
        <rss><channel>
          <item>
            <title>Working story</title>
            <link>https://good.example/story</link>
            <description>Summary text</description>
            <pubDate>2025-01-01</pubDate>
          </item>
        </channel></rss>
        """
        session = FakeSession(
            {
                "https://good.example/feed.xml": feed_xml,
                "https://good.example/story": "<html><body>Story body</body></html>",
            }
        )
        service.session = session
        monkeypatch.setattr(service, "default_feeds", fake_default_feeds)

        payload = service.ingest_default_feeds(extract_articles=False)

        assert payload["maxArticlesPerFeed"] == DEFAULT_MAX_ARTICLES_PER_FEED
        assert DEFAULT_MAX_ARTICLES_PER_FEED == 25


def test_ingest_feed_stops_when_per_feed_timeout_elapses(app, monkeypatch):
    with app.app_context():
        repo = Repository(get_db())
        feed_xml = """
        <rss><channel>
          <item>
            <title>Story one</title>
            <link>https://example.com/story-1</link>
            <description>First summary</description>
            <pubDate>2025-01-01</pubDate>
          </item>
          <item>
            <title>Story two</title>
            <link>https://example.com/story-2</link>
            <description>Second summary</description>
            <pubDate>2025-01-02</pubDate>
          </item>
        </channel></rss>
        """
        session = FakeSession(
            {
                "https://example.com/feed.xml": feed_xml,
                "https://example.com/story-1": "<html><body>Story one body</body></html>",
                "https://example.com/story-2": "<html><body>Story two body</body></html>",
            }
        )
        service = NewsService(repo=repo, session=session, cache_ttl_seconds=10)
        # Extra 100.0 values account for t0 logging calls in ingest_feed
        times = iter([100.0, 100.0, 100.0, 100.0, 250.0])
        monkeypatch.setattr("app.services.news.time.monotonic", lambda: next(times, 250.0))

        result = service.ingest_feed(
            "https://example.com/feed.xml",
            "Example Feed",
            "tech",
            feed_timeout_seconds=60,
        )

        assert result["articlesProcessed"] == 1
        assert result["timedOut"] is True


def test_ingest_default_feeds_continues_after_feed_timeout(app, monkeypatch):
    with app.app_context():
        repo = Repository(get_db())
        service = NewsService(repo=repo, session=FakeSession({}), cache_ttl_seconds=10)

        def fake_default_feeds():
            return [
                {"name": "Slow Feed", "feed_url": "https://slow.example/feed.xml", "category": "test"},
                {"name": "Good Feed", "feed_url": "https://good.example/feed.xml", "category": "test"},
            ]

        slow_feed_xml = """
        <rss><channel>
          <item>
            <title>Slow story one</title>
            <link>https://slow.example/story-1</link>
            <description>Slow summary</description>
            <pubDate>2025-01-01</pubDate>
          </item>
          <item>
            <title>Slow story two</title>
            <link>https://slow.example/story-2</link>
            <description>Another slow summary</description>
            <pubDate>2025-01-02</pubDate>
          </item>
        </channel></rss>
        """
        good_feed_xml = """
        <rss><channel>
          <item>
            <title>Working story</title>
            <link>https://good.example/story</link>
            <description>Summary text</description>
            <pubDate>2025-01-01</pubDate>
          </item>
        </channel></rss>
        """
        session = FakeSession(
            {
                "https://slow.example/feed.xml": slow_feed_xml,
                "https://slow.example/story-1": "<html><body>Slow one</body></html>",
                "https://slow.example/story-2": "<html><body>Slow two</body></html>",
                "https://good.example/feed.xml": good_feed_xml,
                "https://good.example/story": "<html><body>Story body</body></html>",
            }
        )
        service.session = session
        monkeypatch.setattr(service, "default_feeds", fake_default_feeds)
        # Extra values account for t0 logging calls in ingest_default_feeds + ingest_feed
        times = iter([100.0, 100.0, 100.0, 100.0, 100.0, 250.0, 250.0])
        monkeypatch.setattr("app.services.news.time.monotonic", lambda: next(times, 300.0))

        payload = service.ingest_default_feeds(feed_timeout_seconds=60)

        assert payload["feedsProcessed"] == 2
        assert payload["timedOutFeeds"] == 1
        assert payload["feedTimeoutSeconds"] == 60
        assert payload["articlesProcessed"] == 2
        assert payload["results"][0]["status"] == "timeout"
        assert payload["results"][1]["status"] == "ok"
        assert DEFAULT_FEED_TIMEOUT_SECONDS == 180
