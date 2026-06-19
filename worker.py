import logging
import os

from dotenv import load_dotenv

from app.config import Config
from app.db import connect_db, init_db
from app.logging_config import setup_logging
from app.repositories import Repository
from app.workers.handlers import build_context
from app.workers.runner import WorkerRunner

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
except ImportError:  # pragma: no cover
    BackgroundScheduler = None
    CronTrigger = None
    IntervalTrigger = None


DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "AMZN", "META", "TSLA"]


def _with_scheduler_repo(database_path: str, callback) -> None:
    """APScheduler jobs run in a threadpool; use a thread-local SQLite connection."""
    conn = connect_db(database_path)
    try:
        callback(Repository(conn))
    finally:
        conn.close()


def enqueue_scheduled_jobs(database_path: str, tickers: list[str]) -> None:
    def _enqueue(repo: Repository) -> None:
        repo.enqueue_job("sync_companies", {}, priority=10)
        repo.enqueue_job("refresh_fundamentals", {"tickers": tickers}, priority=20)
        repo.enqueue_job("refresh_prices", {"tickers": tickers}, priority=30)
        repo.enqueue_job("refresh_company_scores", {"all": True}, priority=32)
        repo.enqueue_job("snapshot_composite_ranks", {"universe": "sp500"}, priority=34)
        repo.enqueue_job("build_research_queue", {"limit": 50, "max_age_days": 30}, priority=35)
        repo.enqueue_job("snapshot_narrative_intelligence", {"universe": "sp500"}, priority=36)
        repo.enqueue_job("enrich_metadata", {"all_missing": True}, priority=33)
        repo.enqueue_job("refresh_macro", {}, priority=35)
        repo.enqueue_job("refresh_insiders", {"tickers": tickers}, priority=40)
        repo.enqueue_job(
            "ingest_default_feeds",
            {"extract_articles": False, "max_articles_per_feed": 10, "force_refresh": True},
            priority=50,
        )
        repo.enqueue_job("enrich_articles", {"limit": 50}, priority=55)

    _with_scheduler_repo(database_path, _enqueue)


def enqueue_feed_poll(database_path: str) -> None:
    def _enqueue(repo: Repository) -> None:
        repo.enqueue_job(
            "ingest_default_feeds",
            {"extract_articles": False, "max_articles_per_feed": 10, "force_refresh": True},
            priority=5,
        )

    _with_scheduler_repo(database_path, _enqueue)


def main() -> None:
    load_dotenv()
    setup_logging()
    config = Config()
    init_db(config.database_path)
    conn = connect_db(config.database_path)
    tickers = [
        item.strip().upper()
        for item in os.getenv("STOCK_TRACKER_DEFAULT_TICKERS", ",".join(DEFAULT_TICKERS)).split(",")
        if item.strip()
    ]
    ctx = build_context(
        {
            "conn": conn,
            "sec_user_agent": config.sec_user_agent,
            "sec_base_url": config.sec_base_url,
            "sec_company_tickers_url": config.sec_company_tickers_url,
            "request_timeout": config.request_timeout,
            "news_http_ttl_seconds": config.news_http_ttl_seconds,
            "default_tickers": tickers,
        }
    )
    runner = WorkerRunner(ctx)

    if BackgroundScheduler is not None:
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            lambda: enqueue_scheduled_jobs(config.database_path, tickers),
            CronTrigger(hour=2, minute=0),
            id="nightly_etl",
            replace_existing=True,
        )
        if IntervalTrigger is not None:
            scheduler.add_job(
                lambda: enqueue_feed_poll(config.database_path),
                IntervalTrigger(minutes=45),
                id="rss_poll",
                replace_existing=True,
            )
            logging.info("Scheduler registered RSS poll every 45 minutes")
        scheduler.start()
        logging.info("Scheduler registered nightly ETL at 02:00")

    runner.run_forever()


if __name__ == "__main__":
    main()
