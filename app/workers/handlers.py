from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..clients.sec import SecClient
from ..repositories import Repository
from ..services.fundamentals import FundamentalsService
from ..services.insiders import InsidersService
from ..services.news import NewsService
from ..services.prices import PricesService


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

    def ingest_default_feeds(payload: dict) -> dict:
        return ctx.news.ingest_default_feeds(
            extract_articles=payload.get("extract_articles", True),
            max_articles_per_feed=payload.get("max_articles_per_feed"),
            force_refresh=payload.get("force_refresh", True),
        )

    def refresh_prices(payload: dict) -> dict:
        tickers = payload.get("tickers") or ctx.default_tickers
        return ctx.prices.refresh_prices(tickers)

    def refresh_insiders(payload: dict) -> dict:
        tickers = payload.get("tickers") or ctx.default_tickers
        return ctx.insiders.refresh_insiders(tickers)

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
        "ingest_default_feeds": ingest_default_feeds,
        "refresh_prices": refresh_prices,
        "refresh_insiders": refresh_insiders,
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
