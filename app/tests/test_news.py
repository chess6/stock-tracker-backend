from __future__ import annotations

from app.db import get_db
from app.repositories import Repository
from app.services.news import NewsService


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
