from __future__ import annotations

import hashlib
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

from ..repositories import Repository, utc_now_iso

logger = logging.getLogger("stock_tracker.pipeline.article_extraction")

try:
    import trafilatura  # type: ignore
except ImportError:  # pragma: no cover
    trafilatura = None


def needs_extraction(body_text: str | None) -> bool:
    return not body_text or not str(body_text).strip()


def extract_article_text(html_text: str) -> str:
    if trafilatura is not None:
        extracted = trafilatura.extract(html_text)
        if extracted:
            return extracted.strip()
    try:
        from newspaper import Article  # type: ignore

        article = Article("")
        article.set_html(html_text)
        article.parse()
        if article.text and article.text.strip():
            return article.text.strip()
    except Exception:  # pragma: no cover - optional dependency
        pass
    from .news import strip_html

    return strip_html(html_text)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DomainFetcher:
    """Per-domain rate limiting with exponential backoff and jitter."""

    def __init__(
        self,
        repo: Repository,
        *,
        session: requests.Session | None = None,
        timeout: int = 20,
        min_delay_seconds: float = 1.0,
        max_delay_seconds: float = 30.0,
        max_failures: int = 5,
    ) -> None:
        self.repo = repo
        self.session = session or requests.Session()
        self.timeout = timeout
        self.min_delay_seconds = min_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.max_failures = max_failures
        self._last_fetch_by_domain: dict[str, float] = {}

    def _domain(self, url: str) -> str:
        return urlparse(url).netloc.lower() or "unknown"

    def _parse_iso(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _in_backoff(self, domain: str) -> bool:
        row = self.repo.get_domain_fetch_state(domain)
        if not row:
            return False
        until = self._parse_iso(row.get("backoff_until"))
        return until is not None and until > datetime.now(timezone.utc)

    def _wait_for_domain(self, domain: str) -> None:
        if self._in_backoff(domain):
            row = self.repo.get_domain_fetch_state(domain)
            until = self._parse_iso((row or {}).get("backoff_until"))
            if until:
                sleep_for = (until - datetime.now(timezone.utc)).total_seconds()
                if sleep_for > 0:
                    time.sleep(min(sleep_for, self.max_delay_seconds))
        last = self._last_fetch_by_domain.get(domain, 0.0)
        elapsed = time.monotonic() - last
        base_delay = self.min_delay_seconds + random.uniform(0, 0.35)
        if elapsed < base_delay:
            time.sleep(base_delay - elapsed)

    def fetch_html(self, url: str, *, max_retries: int = 3) -> str | None:
        if not url:
            return None
        domain = self._domain(url)
        if self._in_backoff(domain):
            logger.debug("domain %s in backoff, skipping %s", domain, url)
            return None

        for attempt in range(max_retries):
            self._wait_for_domain(domain)
            self._last_fetch_by_domain[domain] = time.monotonic()
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                self.repo.upsert_domain_fetch_state(domain, success=True)
                return response.text
            except requests.RequestException as exc:
                failures = self.repo.upsert_domain_fetch_state(domain, success=False)
                backoff_seconds = min(
                    self.max_delay_seconds,
                    self.min_delay_seconds * (2 ** min(failures, self.max_failures)),
                )
                jitter = random.uniform(0, backoff_seconds * 0.25)
                logger.debug(
                    "fetch failed domain=%s attempt=%d backoff=%.1fs url=%s err=%s",
                    domain,
                    attempt + 1,
                    backoff_seconds + jitter,
                    url,
                    exc,
                )
                if attempt + 1 >= max_retries:
                    return None
                time.sleep(backoff_seconds + jitter)
        return None

    def fetch_and_extract(self, url: str) -> tuple[str | None, str | None]:
        html = self.fetch_html(url)
        if not html:
            return None, None
        text = extract_article_text(html)
        if not text:
            return None, None
        return text, text_hash(text)
