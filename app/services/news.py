from __future__ import annotations

import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree

import requests

from ..repositories import Repository, utc_now_iso

logger = logging.getLogger("stock_tracker.pipeline.news")
from .article_dedup import canonicalize_url, normalize_published_at
from .article_enrichment import infer_topic_cluster, simple_sentiment, simhash_fingerprint
from .entity_linker_factory import create_entity_linker
from .entity_linking import build_entity_link_text

try:
    import feedparser  # type: ignore
except ImportError:  # pragma: no cover
    feedparser = None

try:
    import trafilatura  # type: ignore
except ImportError:  # pragma: no cover
    trafilatura = None


def build_google_news_rss_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


DEFAULT_FEEDS = [
    {"name": "BBC Business", "feed_url": "https://feeds.bbci.co.uk/news/business/rss.xml", "category": "finance"},
    {"name": "NPR Business", "feed_url": "https://feeds.npr.org/1007/rss.xml", "category": "finance"},
    {"name": "CNBC Top News", "feed_url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "category": "finance"},
    {"name": "MarketWatch Top Stories", "feed_url": "https://feeds.marketwatch.com/marketwatch/topstories/", "category": "finance"},
    {"name": "Techmeme", "feed_url": "https://www.techmeme.com/feed.xml", "category": "tech"},
    {"name": "Hacker News Front Page", "feed_url": "https://hnrss.org/frontpage", "category": "tech"},
    {"name": "Lobsters", "feed_url": "https://lobste.rs/rss", "category": "tech"},
    {"name": "Semiconductor Engineering", "feed_url": "https://semiengineering.com/feed/", "category": "semis"},
    {"name": "BleepingComputer", "feed_url": "https://www.bleepingcomputer.com/feed/", "category": "security"},
    {"name": "AWS Blog", "feed_url": "https://aws.amazon.com/blogs/aws/feed/", "category": "cloud"},
    {"name": "Cloudflare Blog", "feed_url": "https://blog.cloudflare.com/rss/", "category": "cloud"},
    {"name": "SEC Press Releases", "feed_url": "https://www.sec.gov/news/pressreleases.rss", "category": "regulatory"},
    {"name": "Reddit r/stocks", "feed_url": "https://www.reddit.com/r/stocks/.rss", "category": "community"},
    {"name": "Reddit r/investing", "feed_url": "https://www.reddit.com/r/investing/.rss", "category": "community"},
    {"name": "Reddit r/SecurityAnalysis", "feed_url": "https://www.reddit.com/r/SecurityAnalysis/.rss", "category": "community"},
    {"name": "Google News: Stock Market", "feed_url": build_google_news_rss_url("stock market"), "category": "finance"},
    {"name": "Google News: Semiconductors", "feed_url": build_google_news_rss_url("semiconductor industry"), "category": "semis"},
    {"name": "Google News: Cloud Computing", "feed_url": build_google_news_rss_url("cloud computing"), "category": "cloud"},
    {"name": "Google News: Cybersecurity", "feed_url": build_google_news_rss_url("cybersecurity"), "category": "security"},
    {"name": "Reddit r/options", "feed_url": "https://www.reddit.com/r/options/.rss", "category": "community"},
    {"name": "Seeking Alpha", "feed_url": "https://seekingalpha.com/feed.xml", "category": "finance"},
    {"name": "Seeking Alpha Market Currents", "feed_url": "https://seekingalpha.com/market_currents.xml", "category": "finance"},
    {"name": "CNBC Finance", "feed_url": "https://www.cnbc.com/id/10000664/device/rss/rss.html", "category": "finance"},
    {"name": "CNBC Investing", "feed_url": "https://www.cnbc.com/id/15839069/device/rss/rss.html", "category": "finance"},
    {"name": "CNBC Tech", "feed_url": "https://www.cnbc.com/id/19854910/device/rss/rss.html", "category": "tech"},
    {"name": "Reddit r/wallstreetbets", "feed_url": "https://www.reddit.com/r/wallstreetbets/.rss", "category": "community"},
    {"name": "Federal Reserve Press Releases", "feed_url": "https://www.federalreserve.gov/feeds/press_all.xml", "category": "regulatory"},
    {"name": "Federal Reserve Speeches", "feed_url": "https://www.federalreserve.gov/feeds/speeches.xml", "category": "regulatory"},
    {"name": "Yahoo Finance", "feed_url": "https://finance.yahoo.com/news/rssindex", "category": "finance"},
    {"name": "MarketWatch MarketPulse", "feed_url": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse", "category": "finance"},
    {"name": "Benzinga", "feed_url": "https://www.benzinga.com/feed", "category": "finance"},
    {"name": "BLS Economic Indicators", "feed_url": "https://www.bls.gov/feed/bls_latest.rss", "category": "finance"},
    {"name": "Ars Technica", "feed_url": "https://feeds.arstechnica.com/arstechnica/index", "category": "tech"},
    {"name": "TechCrunch", "feed_url": "https://techcrunch.com/feed/", "category": "tech"},
    {"name": "The Verge", "feed_url": "https://www.theverge.com/rss/index.xml", "category": "tech"},
    {"name": "EE Times", "feed_url": "https://www.eetimes.com/feed/", "category": "semis"},
    {"name": "Computer Weekly", "feed_url": "https://www.computerweekly.com/rss/All-Computer-Weekly-content.xml", "category": "tech"},
    {"name": "Pragmatic Engineer", "feed_url": "https://blog.pragmaticengineer.com/rss/", "category": "tech"},
    {"name": "Hacker Noon", "feed_url": "https://hackernoon.com/feed", "category": "tech"},
    {"name": "Hacker News Newest", "feed_url": "https://hnrss.org/newest", "category": "tech"},
    {"name": "Slashdot", "feed_url": "https://rss.slashdot.org/Slashdot/slashdotMain", "category": "tech"},
    {"name": "Product Hunt", "feed_url": "https://www.producthunt.com/feed", "category": "tech"},
    {"name": "ServeTheHome", "feed_url": "https://www.servethehome.com/feed/", "category": "tech"},
    {"name": "Tom's Hardware", "feed_url": "https://www.tomshardware.com/feeds.xml", "category": "tech"},
    {"name": "Y Combinator Blog", "feed_url": "https://www.ycombinator.com/blog/rss/", "category": "tech"},
    {"name": "CoinDesk", "feed_url": "https://www.coindesk.com/arc/outboundfeeds/rss", "category": "crypto"},
    {"name": "Krebs on Security", "feed_url": "https://krebsonsecurity.com/feed/", "category": "security"},
    {"name": "Dark Reading", "feed_url": "https://www.darkreading.com/rss.xml", "category": "security"},
    {"name": "Ben's Bites", "feed_url": "https://www.bensbites.com/feed", "category": "ai"},
    {"name": "Google AI Blog", "feed_url": "https://blog.google/innovation-and-ai/technology/ai/rss/", "category": "ai"},
    {"name": "Import AI", "feed_url": "https://importai.substack.com/feed", "category": "ai"},
    {"name": "AI Wire", "feed_url": "https://www.hpcwire.com/aiwire/feed/", "category": "ai"},
    {"name": "TLDR AI", "feed_url": "https://tldr.tech/api/rss/ai", "category": "ai"},
]

# Cap wall-clock time per feed so one slow source cannot block the full ingest run.
DEFAULT_FEED_TIMEOUT_SECONDS = 180
# Cap articles per feed so high-volume RSS sources (Reddit, Google News) do not dominate ingest.
DEFAULT_MAX_ARTICLES_PER_FEED = 25


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self.parts.append(stripped)

    def get_text(self) -> str:
        return " ".join(self.parts)


def strip_html(html: str) -> str:
    parser = _HTMLStripper()
    parser.feed(html)
    return parser.get_text()


def url_hash(url: str) -> str:
    normalized = canonicalize_url(url) or url.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_feed(xml_text: str) -> list[dict]:
    if feedparser is not None:
        parsed = feedparser.parse(xml_text)
        return [
            {
                "title": entry.get("title"),
                "link": entry.get("link"),
                "summary": entry.get("summary") or entry.get("description"),
                "published": entry.get("published") or entry.get("updated"),
            }
            for entry in parsed.entries
            if entry.get("link") and entry.get("title")
        ]

    root = ElementTree.fromstring(xml_text)
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title")
        link = item.findtext("link")
        summary = item.findtext("description")
        published = item.findtext("pubDate")
        if title and link:
            items.append({"title": title, "link": link, "summary": summary, "published": published})
    return items


def extract_article_text(html_text: str) -> str:
    if trafilatura is not None:
        extracted = trafilatura.extract(html_text)
        if extracted:
            return extracted
    return strip_html(html_text)


class NewsService:
    def __init__(self, repo: Repository, session: requests.Session | None = None, timeout: int = 20, cache_ttl_seconds: int = 3600) -> None:
        self.repo = repo
        self.session = session or requests.Session()
        self.timeout = timeout
        self.cache_ttl_seconds = cache_ttl_seconds

    @staticmethod
    def _emit_news_ingested(article_id: int, tickers: list[str], sentiment_label: str | None) -> None:
        try:
            from orchestration.services.bridge import emit_news_ingested

            emit_news_ingested(
                article_id,
                tickers=tickers,
                sentiment_label=sentiment_label,
            )
        except Exception:
            pass

    def _fetch_cached_text(self, url: str, cache_namespace: str, *, force_refresh: bool = False) -> str:
        cache_key = f"{cache_namespace}:{url}"
        cached = None if force_refresh else self.repo.get_cached_http_response(cache_key)
        if cached and cached.get("expires_at"):
            expires_at = datetime.fromisoformat(cached["expires_at"].replace("Z", "+00:00"))
            if expires_at > datetime.now(timezone.utc):
                return cached["response_body"] or ""
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        body = response.text
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=self.cache_ttl_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.repo.put_cached_http_response(cache_key, url, response.status_code, body, expires_at)
        return body

    def _link_article_entities(
        self,
        article_id: int,
        text: str,
        *,
        companies: list[dict] | None = None,
        stage: str = "ingest",
        defer_commit: bool = False,
    ) -> list[str]:
        linker = create_entity_linker(self.repo, companies=companies, enable_embedding_profiles=False)
        matches = linker.link_entities(text, stage=stage, enable_embeddings=False)
        self.repo.save_entity_matches(article_id, matches, defer_commit=defer_commit)
        return [match.ticker for match in matches]

    def ingest_feed(
        self,
        feed_url: str,
        name: str,
        category: str = "general",
        *,
        extract_articles: bool = True,
        max_articles: int | None = None,
        force_refresh: bool = False,
        feed_timeout_seconds: float | None = None,
        skip_dedup: bool = False,
        skip_events: bool = False,
        _companies_cache: list[dict] | None = None,
        _prefetched_body: str | None = None,
    ) -> dict:
        deadline = None
        if feed_timeout_seconds is not None and feed_timeout_seconds > 0:
            deadline = time.monotonic() + feed_timeout_seconds

        def _timed_out() -> bool:
            return deadline is not None and time.monotonic() >= deadline

        logger.info("ingest_feed start name=%r category=%s extract=%s max_articles=%s timeout=%s",
                    name, category, extract_articles, max_articles, feed_timeout_seconds)
        t0 = time.monotonic()
        if _prefetched_body is not None:
            feed_body = _prefetched_body
        else:
            feed_body = self._fetch_cached_text(feed_url, "feed", force_refresh=force_refresh)
        if _timed_out():
            logger.warning("ingest_feed timeout before parsing name=%r", name)
            feed_id = self.repo.upsert_feed(
                {
                    "name": name,
                    "feed_url": feed_url,
                    "domain": urlparse(feed_url).netloc,
                    "category": category,
                    "last_polled_at": utc_now_iso(),
                }
            )
            return {"feedId": feed_id, "articlesProcessed": 0, "timedOut": True}
        entries = parse_feed(feed_body)
        if max_articles is not None and max_articles > 0:
            entries = entries[:max_articles]
        feed_id = self.repo.upsert_feed(
            {
                "name": name,
                "feed_url": feed_url,
                "domain": urlparse(feed_url).netloc,
                "category": category,
                "last_polled_at": utc_now_iso(),
            }
        )
        companies = _companies_cache or self.repo.list_companies_for_matching()
        batch_mode = skip_dedup and skip_events
        article_count = 0
        for entry in entries:
            if _timed_out():
                break
            article_url = canonicalize_url(entry["link"]) or entry["link"]
            body_text = strip_html(entry.get("summary") or "")
            if extract_articles:
                try:
                    html = self._fetch_cached_text(article_url, "article", force_refresh=force_refresh)
                    body_text = extract_article_text(html)
                except requests.RequestException:
                    pass
            summary_text = strip_html(entry.get("summary") or "")
            enrichment_text = " ".join(filter(None, [entry.get("title"), summary_text, body_text]))
            sentiment_label, sentiment_score = simple_sentiment(enrichment_text)
            topic_cluster_id = infer_topic_cluster(enrichment_text)
            article_id = self.repo.upsert_article(
                {
                    "canonical_url": article_url,
                    "url_hash": url_hash(article_url),
                    "title": entry["title"],
                    "summary": summary_text,
                    "body_text": body_text,
                    "source_domain": urlparse(article_url).netloc,
                    "published_at": normalize_published_at(entry.get("published")),
                    "fetched_at": utc_now_iso(),
                    "content_hash": text_hash(body_text or entry["title"]),
                    "sentiment_label": sentiment_label,
                    "sentiment_score": sentiment_score,
                    "topic_cluster_id": topic_cluster_id,
                    "raw_source": f"feed:{feed_id}",
                },
                skip_dedup=skip_dedup,
                defer_commit=batch_mode,
            )
            fingerprint = simhash_fingerprint(enrichment_text)
            self.repo.upsert_embedding_metadata(
                article_id,
                model="simhash",
                content_hash=fingerprint,
                storage_key=fingerprint,
                defer_commit=batch_mode,
            )
            match_text = build_entity_link_text(
                entry.get("title") or "",
                entry.get("summary"),
                body_text,
            )
            linked_tickers = self._link_article_entities(
                article_id,
                match_text,
                companies=companies,
                stage="ingest",
                defer_commit=batch_mode,
            )
            if not skip_events:
                self._emit_news_ingested(article_id, linked_tickers, sentiment_label)
            article_count += 1
        if batch_mode:
            self.repo.conn.commit()
        elapsed = time.monotonic() - t0
        timed_out = _timed_out()
        logger.info("ingest_feed done name=%r articles=%d timedOut=%s elapsed=%.1fs",
                    name, article_count, timed_out, elapsed)
        return {"feedId": feed_id, "articlesProcessed": article_count, "timedOut": timed_out}

    def default_feeds(self) -> list[dict]:
        return list(DEFAULT_FEEDS)

    def _prefetch_feeds(
        self,
        feed_list: list[dict],
        *,
        force_refresh: bool = False,
        max_workers: int = 10,
        per_feed_timeout: float = 30,
    ) -> dict[str, str]:
        """Fetch all RSS feed XMLs concurrently. Returns {feed_url: xml_text}."""
        prefetched: dict[str, str] = {}

        def _fetch_one(feed_url: str) -> tuple[str, str | None]:
            try:
                response = self.session.get(feed_url, timeout=per_feed_timeout)
                response.raise_for_status()
                return feed_url, response.text
            except Exception as exc:
                logger.warning("prefetch failed url=%s: %s", feed_url, exc)
                return feed_url, None

        urls = [f["feed_url"] for f in feed_list]
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one, url): url for url in urls}
            for future in as_completed(futures):
                url, body = future.result()
                if body is not None:
                    prefetched[url] = body

        logger.info("prefetch done feeds=%d/%d", len(prefetched), len(urls))
        return prefetched

    def ingest_default_feeds(
        self,
        *,
        extract_articles: bool = True,
        max_articles_per_feed: int | None = DEFAULT_MAX_ARTICLES_PER_FEED,
        force_refresh: bool = False,
        feed_timeout_seconds: float = DEFAULT_FEED_TIMEOUT_SECONDS,
        tickers: list[str] | None = None,
    ) -> dict:
        feed_list = self.default_feeds()
        logger.info("ingest_default_feeds start feeds=%d extract=%s max_per_feed=%s timeout=%gs tickers=%s",
                    len(feed_list), extract_articles, max_articles_per_feed, feed_timeout_seconds,
                    len(tickers) if tickers else "all")
        t0 = time.monotonic()

        all_companies = self.repo.list_companies_for_matching()
        if tickers:
            ticker_set = {t.upper() for t in tickers}
            companies = [c for c in all_companies if (c.get("ticker") or "").upper() in ticker_set]
            logger.info("ticker filter: %d/%d companies", len(companies), len(all_companies))
        else:
            companies = all_companies

        logger.info("prefetching %d RSS feeds in parallel...", len(feed_list))
        prefetched = self._prefetch_feeds(feed_list, force_refresh=force_refresh)

        results = []
        total_articles = 0
        failed_feeds = 0
        timed_out_feeds = 0
        for feed in feed_list:
            feed_result = {
                "name": feed["name"],
                "category": feed["category"],
                "feed_url": feed["feed_url"],
                "articlesProcessed": 0,
                "status": "ok",
            }
            try:
                result = self.ingest_feed(
                    feed["feed_url"],
                    feed["name"],
                    feed["category"],
                    extract_articles=extract_articles,
                    max_articles=max_articles_per_feed,
                    force_refresh=force_refresh,
                    feed_timeout_seconds=feed_timeout_seconds,
                    skip_dedup=True,
                    skip_events=True,
                    _companies_cache=companies,
                    _prefetched_body=prefetched.get(feed["feed_url"]),
                )
                feed_result["articlesProcessed"] = result["articlesProcessed"]
                feed_result["feedId"] = result["feedId"]
                total_articles += result["articlesProcessed"]
                if result.get("timedOut"):
                    feed_result["status"] = "timeout"
                    feed_result["error"] = f"Exceeded {feed_timeout_seconds:g}s per-feed timeout"
                    timed_out_feeds += 1
            except requests.RequestException as exc:
                logger.warning("ingest_default_feeds feed error name=%r: %s", feed["name"], exc)
                feed_result["status"] = "error"
                feed_result["error"] = str(exc)
                failed_feeds += 1
            results.append(feed_result)
        dates_normalized = self.repo.normalize_published_dates()
        deduped = self.repo.deduplicate_articles()
        elapsed = time.monotonic() - t0
        logger.info(
            "ingest_default_feeds done feeds=%d articles=%d failed=%d timedOut=%d elapsed=%.1fs",
            len(results), total_articles, failed_feeds, timed_out_feeds, elapsed,
        )
        return {
            "feedsProcessed": len(results),
            "articlesProcessed": total_articles,
            "failedFeeds": failed_feeds,
            "timedOutFeeds": timed_out_feeds,
            "feedTimeoutSeconds": feed_timeout_seconds,
            "maxArticlesPerFeed": max_articles_per_feed,
            "datesNormalized": dates_normalized,
            "deduplication": deduped,
            "results": results,
        }
