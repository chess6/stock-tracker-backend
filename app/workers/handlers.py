from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from ..clients.sec import SecClient
from ..repositories import Repository
from ..services.fundamentals import FundamentalsService
from ..services.insiders import InsidersService
from ..services.news import NewsService
from ..services.prices import PricesService
from ..services.ticker_universes import get_universe_tickers

logger = logging.getLogger("stock_tracker.pipeline.extract_articles")


@dataclass
class JobContext:
    repo: Repository
    fundamentals: FundamentalsService
    news: NewsService
    prices: PricesService
    insiders: InsidersService
    sec_company_tickers_url: str
    default_tickers: list[str]


def build_handlers(ctx: JobContext) -> dict[str, Callable[[dict], dict]]:
    def sync_companies(_: dict) -> dict:
        return ctx.fundamentals.refresh_company_tickers(ctx.sec_company_tickers_url)

    def refresh_fundamentals(payload: dict) -> dict:
        tickers = payload.get("tickers") or ctx.default_tickers
        return ctx.fundamentals.refresh_fundamentals(tickers)

    def refresh_company_scores(payload: dict) -> dict:
        if payload.get("all"):
            return ctx.fundamentals.refresh_company_scores_batch(None)
        tickers = payload.get("tickers") or ctx.default_tickers
        return ctx.fundamentals.refresh_company_scores_batch(tickers)

    def ingest_default_feeds(payload: dict) -> dict:
        kwargs = {
            "extract_articles": payload.get("extract_articles", True),
            "force_refresh": payload.get("force_refresh", True),
        }
        if payload.get("max_articles_per_feed") is not None:
            kwargs["max_articles_per_feed"] = payload["max_articles_per_feed"]
        if payload.get("feed_timeout_seconds") is not None:
            kwargs["feed_timeout_seconds"] = payload["feed_timeout_seconds"]
        if payload.get("tickers"):
            kwargs["tickers"] = payload["tickers"]
        result = ctx.news.ingest_default_feeds(**kwargs)
        pending = ctx.repo.conn.execute(
            """
            SELECT COUNT(*) FROM articles
            WHERE duplicate_of_article_id IS NULL
              AND COALESCE(pipeline_status, 'pending') IN ('pending', 'error')
            """
        ).fetchone()[0]
        if pending > 0:
            ctx.repo.enqueue_job("enrich_articles", {"limit": 50}, priority=56)
        return result

    def refresh_prices(payload: dict) -> dict:
        tickers = payload.get("tickers") or ctx.default_tickers
        days = payload.get("days")
        if days is not None and int(days) > 0:
            return ctx.prices.refresh_prices(tickers, days=int(days))
        return ctx.prices.refresh_prices(tickers)

    def refresh_insiders(payload: dict) -> dict:
        tickers = payload.get("tickers")
        if not tickers and payload.get("universe"):
            tickers = get_universe_tickers(str(payload["universe"]))
        if not tickers:
            tickers = ctx.default_tickers
        max_filings = payload.get("max_filings_per_company") or payload.get("maxFilingsPerCompany")
        if max_filings is not None and int(max_filings) > 0:
            return ctx.insiders.refresh_insiders(tickers, max_filings_per_company=int(max_filings))
        return ctx.insiders.refresh_insiders(tickers)

    def enrich_metadata(payload: dict) -> dict:
        if payload.get("all_missing"):
            return ctx.fundamentals.enrich_company_metadata(None, all_missing=True)
        tickers = payload.get("tickers") or ctx.default_tickers
        return ctx.fundamentals.enrich_company_metadata(tickers)

    def refresh_macro(_: dict) -> dict:
        from ..services.macro import MACRO_TICKERS

        return ctx.prices.refresh_prices(MACRO_TICKERS)

    def enrich_articles(payload: dict) -> dict:
        from ..services.article_pipeline import ArticlePipeline

        limit = int(payload.get("limit", 50))
        enable_embeddings = payload.get("enable_embeddings", True)
        enable_finbert = payload.get("enable_finbert", True)
        pipeline = ArticlePipeline(
            ctx.repo,
            enable_embeddings=bool(enable_embeddings),
            enable_finbert=bool(enable_finbert),
        )
        result = pipeline.process_batch(limit=limit)
        logger.info("Enriched %d articles", result.get("processed", 0))
        return result

    def extract_articles(payload: dict) -> dict:
        """Backward-compatible alias for the full enrichment pipeline."""
        return enrich_articles(payload)

    def bootstrap(payload: dict) -> dict:
        tickers = payload.get("tickers") or ctx.default_tickers
        companies = sync_companies({})
        fundamentals = refresh_fundamentals({"tickers": tickers})
        feeds = ingest_default_feeds({"extract_articles": False, "max_articles_per_feed": 25})
        prices = refresh_prices({"tickers": tickers})
        insiders = refresh_insiders({"tickers": tickers})
        return {
            "companies": companies,
            "fundamentals": fundamentals,
            "feeds": feeds,
            "prices": prices,
            "insiders": insiders,
            "tickers": tickers,
        }

    return {
        "sync_companies": sync_companies,
        "refresh_fundamentals": refresh_fundamentals,
        "refresh_company_scores": refresh_company_scores,
        "enrich_metadata": enrich_metadata,
        "ingest_default_feeds": ingest_default_feeds,
        "refresh_prices": refresh_prices,
        "refresh_macro": refresh_macro,
        "refresh_insiders": refresh_insiders,
        "extract_articles": extract_articles,
        "enrich_articles": enrich_articles,
        "bootstrap": bootstrap,
    }


def build_context(config: dict) -> JobContext:
    repo = Repository(config["conn"])
    sec_client = SecClient(
        user_agent=config["sec_user_agent"],
        base_url=config["sec_base_url"],
        timeout=config["request_timeout"],
    )
    return JobContext(
        repo=repo,
        fundamentals=FundamentalsService(repo, sec_client),
        news=NewsService(
            repo=repo,
            timeout=config["request_timeout"],
            cache_ttl_seconds=config["news_http_ttl_seconds"],
        ),
        prices=PricesService(repo),
        insiders=InsidersService(repo, sec_client),
        sec_company_tickers_url=config["sec_company_tickers_url"],
        default_tickers=config["default_tickers"],
    )
