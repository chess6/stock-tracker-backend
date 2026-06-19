"""Orchestrate admin/worker pipeline refresh by mode (Phase G3)."""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import TYPE_CHECKING

from .pipeline_modes import (
    UnknownPipelineModeError,
    mode_requires_tickers,
    normalize_mode,
    resolve_fundamentals_tickers,
    resolve_prices_tickers,
    resolve_scores_tickers,
)

if TYPE_CHECKING:
    from ..repositories import Repository
    from .fundamentals import FundamentalsService
    from .news import NewsService
    from .prices import PricesService

logger = logging.getLogger("stock_tracker.pipeline.refresh")


def should_skip_thesis_recompute(
    repo: Repository,
    company_id: int,
    scoring_version: int,
    thesis_version: int,
) -> bool:
    """Skip thesis recompute when a fresh snapshot exists for current versions."""
    latest = repo.fetch_latest_thesis_snapshot(company_id)
    if not latest:
        return False
    freshness_cutoff = (date.today() - timedelta(days=7)).isoformat()
    return (
        latest["thesis_version"] >= thesis_version
        and latest["scoring_version"] >= scoring_version
        and latest["computed_at"] >= freshness_cutoff
    )


class PipelineRefreshService:
    def __init__(
        self,
        repo: Repository,
        fundamentals: FundamentalsService,
        prices: PricesService,
        news: NewsService,
    ) -> None:
        self.repo = repo
        self.fundamentals = fundamentals
        self.prices = prices
        self.news = news

    def run(
        self,
        mode: str | None,
        *,
        tickers: list[str] | None = None,
        article_limit: int = 50,
        ingest_feeds: bool | None = None,
        force_refresh: bool = False,
        dry_run: bool = False,
    ) -> dict:
        try:
            normalized = normalize_mode(mode)
        except UnknownPipelineModeError as exc:
            return {"error": str(exc), "mode": mode}

        if mode_requires_tickers(normalized) and not tickers:
            return {
                "error": f"mode={normalized} requires tickers or universe",
                "mode": normalized,
            }

        t0 = time.monotonic()
        fundamentals_tickers = resolve_fundamentals_tickers(self.repo, normalized, tickers)
        prices_tickers = resolve_prices_tickers(self.repo, normalized, tickers)
        scores_tickers = resolve_scores_tickers(self.repo, normalized, tickers)

        selection = {
            "fundamentalsTickers": len(fundamentals_tickers),
            "pricesTickers": len(prices_tickers),
            "scoresTickers": len(scores_tickers),
        }
        stages: dict = {}

        if fundamentals_tickers:
            stages["fundamentals"] = self.fundamentals.refresh_fundamentals(
                fundamentals_tickers,
                force_refresh=force_refresh,
                dry_run=dry_run,
            )
        if prices_tickers and not dry_run:
            stages["prices"] = self.prices.refresh_prices(prices_tickers)
        elif prices_tickers and dry_run:
            stages["prices"] = {
                "dryRun": True,
                "tickers": prices_tickers,
                "plannedTickers": len(prices_tickers),
            }
        if scores_tickers and not dry_run:
            stages["scores"] = self.fundamentals.refresh_company_scores_batch(scores_tickers)
        elif scores_tickers and dry_run:
            stages["scores"] = {
                "dryRun": True,
                "plannedTickers": len(scores_tickers),
            }

        if dry_run:
            elapsed = time.monotonic() - t0
            return {
                "mode": normalized,
                "dryRun": True,
                "forceRefresh": force_refresh,
                "selection": selection,
                "stages": stages,
                "elapsedSec": round(elapsed, 3),
            }

        if normalized == "lightweight_daily_refresh":
            stages["articles"] = self._enrich_pending_articles(article_limit)
        elif normalized == "full_rebuild":
            stages["feeds"] = self.news.ingest_default_feeds(
                extract_articles=False,
                max_articles_per_feed=10,
                force_refresh=True,
            )
            stages["articles"] = self._enrich_pending_articles(article_limit)
            if ingest_feeds is False:
                stages.pop("feeds", None)

        elapsed = time.monotonic() - t0
        payload = {
            "mode": normalized,
            "selection": selection,
            "stages": stages,
            "elapsedSec": round(elapsed, 3),
        }
        logger.info(
            "pipeline_refresh mode=%s fundamentals=%d prices=%d scores=%d elapsed=%.2fs",
            normalized,
            selection["fundamentalsTickers"],
            selection["pricesTickers"],
            selection["scoresTickers"],
            elapsed,
        )
        return payload

    def _enrich_pending_articles(self, limit: int) -> dict:
        from .article_pipeline import ArticlePipeline
        from .feature_flags import embeddings_default_enabled

        pipeline = ArticlePipeline(
            self.repo,
            enable_embeddings=embeddings_default_enabled(self.repo),
            enable_finbert=False,
        )
        return pipeline.process_batch(limit=max(int(limit), 1))
