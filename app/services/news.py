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
from .article_dedup import compute_simhash
from .article_enrichment import infer_topic_cluster, simple_sentiment
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


DEFAULT_SOURCE_WEIGHT = 0.55

FEED_PACKS = (
    "deep_value",
    "technology",
    "ai",
    "semiconductors",
    "macro",
    "security",
    "crypto",
)


def _feed(
    name: str,
    feed_url: str,
    category: str,
    *,
    source_weight: float = DEFAULT_SOURCE_WEIGHT,
    enabled_by_default: bool = True,
    pack_tags: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "feed_url": feed_url,
        "category": category,
        "source_weight": source_weight,
        "enabled_by_default": enabled_by_default,
        "pack_tags": pack_tags or [],
    }


DEFAULT_FEEDS = [
    _feed("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "finance", source_weight=0.70, pack_tags=["macro"]),
    _feed("NPR Business", "https://feeds.npr.org/1007/rss.xml", "finance", source_weight=0.70, pack_tags=["macro"]),
    _feed(
        "CNBC Top News",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "finance",
        source_weight=0.80,
        pack_tags=["deep_value"],
    ),
    _feed(
        "MarketWatch Top Stories",
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        "finance",
        source_weight=0.80,
        pack_tags=["deep_value"],
    ),
    _feed("Techmeme", "https://www.techmeme.com/feed.xml", "tech", source_weight=0.65, pack_tags=["technology"]),
    _feed("Hacker News Front Page", "https://hnrss.org/frontpage", "tech", source_weight=0.55, pack_tags=["technology"]),
    _feed(
        "Semiconductor Engineering",
        "https://semiengineering.com/feed/",
        "semis",
        source_weight=0.75,
        pack_tags=["semiconductors", "technology"],
    ),
    _feed("BleepingComputer", "https://www.bleepingcomputer.com/feed/", "security", source_weight=0.65, pack_tags=["security"]),
    _feed("AWS Blog", "https://aws.amazon.com/blogs/aws/feed/", "cloud", source_weight=0.60, pack_tags=["technology"]),
    _feed("Cloudflare Blog", "https://blog.cloudflare.com/rss/", "cloud", source_weight=0.60, pack_tags=["technology"]),
    _feed(
        "SEC Press Releases",
        "https://www.sec.gov/news/pressreleases.rss",
        "regulatory",
        source_weight=1.0,
        pack_tags=["deep_value", "macro"],
    ),
    _feed(
        "SEC 8-K Filings",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-k&owner=include&count=40&output=atom",
        "regulatory",
        source_weight=1.0,
        pack_tags=["deep_value"],
    ),
    _feed(
        "SEC 13D Activist Filings",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=sc%2013d&owner=include&count=40&output=atom",
        "regulatory",
        source_weight=1.0,
        pack_tags=["deep_value"],
    ),
    _feed("Reddit r/stocks", "https://www.reddit.com/r/stocks/.rss", "community", source_weight=0.45, pack_tags=["deep_value"]),
    _feed("Reddit r/investing", "https://www.reddit.com/r/investing/.rss", "community", source_weight=0.45, pack_tags=["deep_value"]),
    _feed(
        "Reddit r/SecurityAnalysis",
        "https://www.reddit.com/r/SecurityAnalysis/.rss",
        "community",
        source_weight=0.60,
        pack_tags=["deep_value"],
    ),
    _feed(
        "Google News: Stock Market",
        build_google_news_rss_url("stock market"),
        "finance",
        source_weight=0.65,
        pack_tags=["deep_value"],
    ),
    _feed(
        "Google News: Semiconductors",
        build_google_news_rss_url("semiconductor industry"),
        "semis",
        source_weight=0.65,
        pack_tags=["semiconductors"],
    ),
    _feed(
        "Google News: Cloud Computing",
        build_google_news_rss_url("cloud computing"),
        "cloud",
        source_weight=0.60,
        pack_tags=["technology"],
    ),
    _feed(
        "Google News: Cybersecurity",
        build_google_news_rss_url("cybersecurity"),
        "security",
        source_weight=0.65,
        pack_tags=["security"],
    ),
    _feed(
        "Reddit r/options",
        "https://www.reddit.com/r/options/.rss",
        "community",
        source_weight=0.35,
        enabled_by_default=False,
        pack_tags=["deep_value"],
    ),
    _feed("Seeking Alpha", "https://seekingalpha.com/feed.xml", "finance", source_weight=0.70, pack_tags=["deep_value"]),
    _feed(
        "Seeking Alpha Market Currents",
        "https://seekingalpha.com/market_currents.xml",
        "finance",
        source_weight=0.85,
        pack_tags=["deep_value"],
    ),
    _feed(
        "CNBC Tech",
        "https://www.cnbc.com/id/19854910/device/rss/rss.html",
        "tech",
        source_weight=0.75,
        pack_tags=["technology"],
    ),
    _feed(
        "Reddit r/wallstreetbets",
        "https://www.reddit.com/r/wallstreetbets/.rss",
        "community",
        source_weight=0.10,
        pack_tags=["deep_value"],
    ),
    _feed(
        "Federal Reserve Press Releases",
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "regulatory",
        source_weight=1.0,
        pack_tags=["macro"],
    ),
    _feed(
        "Federal Reserve Speeches",
        "https://www.federalreserve.gov/feeds/speeches.xml",
        "regulatory",
        source_weight=1.0,
        pack_tags=["macro"],
    ),
    _feed("Yahoo Finance", "https://finance.yahoo.com/news/rssindex", "finance", source_weight=0.75, pack_tags=["deep_value"]),
    _feed(
        "MarketWatch MarketPulse",
        "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
        "finance",
        source_weight=0.75,
        enabled_by_default=False,
        pack_tags=["deep_value"],
    ),
    _feed("Benzinga", "https://www.benzinga.com/feed", "finance", source_weight=0.80, pack_tags=["deep_value"]),
    _feed(
        "BLS Economic Indicators",
        "https://www.bls.gov/feed/bls_latest.rss",
        "finance",
        source_weight=0.95,
        pack_tags=["macro"],
    ),
    _feed(
        "Treasury Press Releases",
        "https://home.treasury.gov/system/files/136/TreasuryPressReleases.xml",
        "regulatory",
        source_weight=1.0,
        pack_tags=["macro"],
    ),
    _feed(
        "FRED Economic Releases",
        "https://fred.stlouisfed.org/feeds/releases.xml",
        "regulatory",
        source_weight=0.95,
        pack_tags=["macro"],
    ),
    _feed("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "tech", source_weight=0.70, pack_tags=["technology"]),
    _feed("TechCrunch", "https://techcrunch.com/feed/", "tech", source_weight=0.65, pack_tags=["technology"]),
    _feed("The Verge", "https://www.theverge.com/rss/index.xml", "tech", source_weight=0.60, pack_tags=["technology"]),
    _feed("EE Times", "https://www.eetimes.com/feed/", "semis", source_weight=0.70, pack_tags=["semiconductors"]),
    _feed(
        "Pragmatic Engineer",
        "https://blog.pragmaticengineer.com/rss/",
        "tech",
        source_weight=0.55,
        enabled_by_default=False,
        pack_tags=["technology"],
    ),
    _feed("ServeTheHome", "https://www.servethehome.com/feed/", "tech", source_weight=0.60, pack_tags=["technology", "semiconductors"]),
    _feed("Tom's Hardware", "https://www.tomshardware.com/feeds.xml", "tech", source_weight=0.60, pack_tags=["technology", "semiconductors"]),
    _feed(
        "Y Combinator Blog",
        "https://www.ycombinator.com/blog/rss/",
        "tech",
        source_weight=0.50,
        enabled_by_default=False,
        pack_tags=["technology"],
    ),
    _feed(
        "CoinDesk",
        "https://www.coindesk.com/arc/outboundfeeds/rss",
        "crypto",
        source_weight=0.55,
        enabled_by_default=False,
        pack_tags=["crypto"],
    ),
    _feed("Krebs on Security", "https://krebsonsecurity.com/feed/", "security", source_weight=0.70, pack_tags=["security"]),
    _feed("Dark Reading", "https://www.darkreading.com/rss.xml", "security", source_weight=0.65, pack_tags=["security"]),
    _feed("Ben's Bites", "https://www.bensbites.com/feed", "ai", source_weight=0.60, pack_tags=["ai"]),
    _feed(
        "Google AI Blog",
        "https://blog.google/innovation-and-ai/technology/ai/rss/",
        "ai",
        source_weight=0.65,
        pack_tags=["ai"],
    ),
    _feed("Import AI", "https://importai.substack.com/feed", "ai", source_weight=0.60, pack_tags=["ai"]),
    _feed("AI Wire", "https://www.hpcwire.com/aiwire/feed/", "ai", source_weight=0.55, pack_tags=["ai"]),
    _feed("TLDR AI", "https://tldr.tech/api/rss/ai", "ai", source_weight=0.55, pack_tags=["ai"]),
]

REMOVED_FEED_NAMES = frozenset({
    "Lobsters",
    "Hacker Noon",
    "Product Hunt",
    "Slashdot",
    "Computer Weekly",
    "CNBC Finance",
    "CNBC Investing",
    "Hacker News Newest",
})

DEFAULT_ACTIVE_FEED_COUNT = sum(1 for feed in DEFAULT_FEEDS if feed.get("enabled_by_default", True))

# Cap wall-clock time per feed so one slow source cannot block the full ingest run.
DEFAULT_FEED_TIMEOUT_SECONDS = 180
# Cap articles per feed so high-volume RSS sources (Reddit, Google News) do not dominate ingest.
DEFAULT_MAX_ARTICLES_PER_FEED = 10


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
    from .article_extraction import extract_article_text as _extract

    return _extract(html_text)


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
        source_weight: float | None = None,
        enabled_by_default: bool | None = None,
        pack_tags: list[str] | None = None,
        _companies_cache: list[dict] | None = None,
        _prefetched_body: str | None = None,
    ) -> dict:
        feed_meta = {
            "source_weight": source_weight if source_weight is not None else DEFAULT_SOURCE_WEIGHT,
            "enabled_by_default": True if enabled_by_default is None else enabled_by_default,
            "pack_tags": pack_tags or [],
        }

        def _feed_payload() -> dict:
            return {
                "name": name,
                "feed_url": feed_url,
                "domain": urlparse(feed_url).netloc,
                "category": category,
                "last_polled_at": utc_now_iso(),
                "source_weight": feed_meta["source_weight"],
                "enabled_by_default": feed_meta["enabled_by_default"],
                "pack_tags": feed_meta["pack_tags"],
            }
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
            feed_id = self.repo.upsert_feed(_feed_payload())
            return {"feedId": feed_id, "articlesProcessed": 0, "timedOut": True}
        entries = parse_feed(feed_body)
        if max_articles is not None and max_articles > 0:
            entries = entries[:max_articles]
        feed_id = self.repo.upsert_feed(_feed_payload())
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
            fingerprint = compute_simhash(entry.get("title") or "", summary_text)
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
        if timed_out:
            self.repo.record_feed_poll(feed_id, success=False, error_message=f"Exceeded {feed_timeout_seconds:g}s per-feed timeout")
        else:
            self.repo.record_feed_poll(feed_id, success=True)
        logger.info("ingest_feed done name=%r articles=%d timedOut=%s elapsed=%.1fs",
                    name, article_count, timed_out, elapsed)
        return {"feedId": feed_id, "articlesProcessed": article_count, "timedOut": timed_out}

    def default_feeds(self) -> list[dict]:
        return list(DEFAULT_FEEDS)

    def feeds_for_ingest(self) -> list[dict]:
        enabled_packs = set(self.repo.get_enabled_feed_packs())
        selected: list[dict] = []
        for feed in DEFAULT_FEEDS:
            if feed.get("enabled_by_default", True):
                selected.append(feed)
                continue
            pack_tags = feed.get("pack_tags") or []
            if enabled_packs and any(tag in enabled_packs for tag in pack_tags):
                selected.append(feed)
        inactive_urls = self.repo.get_inactive_feed_urls()
        if inactive_urls:
            selected = [feed for feed in selected if feed["feed_url"] not in inactive_urls]
        return selected

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
        extract_articles: bool = False,
        max_articles_per_feed: int | None = DEFAULT_MAX_ARTICLES_PER_FEED,
        force_refresh: bool = False,
        feed_timeout_seconds: float = DEFAULT_FEED_TIMEOUT_SECONDS,
        tickers: list[str] | None = None,
    ) -> dict:
        feed_list = self.feeds_for_ingest()
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
                    source_weight=feed.get("source_weight"),
                    enabled_by_default=feed.get("enabled_by_default", True),
                    pack_tags=feed.get("pack_tags"),
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
                elif feed_result.get("feedId") and feed_result.get("status") == "ok":
                    self.repo.record_feed_poll(feed_result["feedId"], success=True)
            except requests.RequestException as exc:
                logger.warning("ingest_default_feeds feed error name=%r: %s", feed["name"], exc)
                feed_result["status"] = "error"
                feed_result["error"] = str(exc)
                failed_feeds += 1
                if feed_result.get("feedId"):
                    self.repo.record_feed_poll(feed_result["feedId"], success=False, error_message=str(exc))
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
