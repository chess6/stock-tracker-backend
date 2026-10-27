"""Standalone scheduler: enqueues periodic ingestion jobs for worker.py to process.

Run exactly one scheduler per database. Workers can be scaled independently without
duplicating the nightly ETL or RSS poll.
"""

import logging
import sys

from dotenv import load_dotenv

from app.config import Config, default_tickers
from app.db import connect_db, init_db
from app.logging_config import setup_logging
from app.repositories import Repository

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
except ImportError:  # pragma: no cover
    BlockingScheduler = None
    CronTrigger = None
    IntervalTrigger = None


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
        repo.enqueue_job("refresh_catalyst_calendar", {"limit": 500}, priority=21)
        repo.enqueue_job("refresh_prices", {"tickers": tickers, "days": 1825}, priority=30)
        repo.enqueue_job("refresh_company_scores", {"all": True}, priority=32)
        repo.enqueue_job("snapshot_composite_ranks", {"universe": "sp500"}, priority=34)
        repo.enqueue_job("build_research_queue", {"limit": 50, "max_age_days": 30}, priority=35)
        repo.enqueue_job("snapshot_narrative_intelligence", {"universe": "sp500"}, priority=36)
        repo.enqueue_job("enrich_metadata", {"all_missing": True}, priority=33)
        repo.enqueue_job("refresh_macro", {}, priority=35)
        repo.enqueue_job("refresh_insiders", {"tickers": tickers}, priority=40)
        repo.enqueue_job("refresh_edgar_events", {"tickers": tickers}, priority=42)
        repo.enqueue_job("refresh_short_interest", {"tickers": tickers}, priority=41)
        repo.enqueue_job(
            "ingest_default_feeds",
            {"extract_articles": False, "max_articles_per_feed": 10, "force_refresh": True},
            priority=50,
        )
        repo.enqueue_job("enrich_articles", {"limit": 50}, priority=55)

    _with_scheduler_repo(database_path, _enqueue)
    logging.info("Enqueued nightly ETL jobs for %d tickers", len(tickers))


def enqueue_feed_poll(database_path: str) -> None:
    def _enqueue(repo: Repository) -> None:
        repo.enqueue_job(
            "ingest_default_feeds",
            {"extract_articles": False, "max_articles_per_feed": 10, "force_refresh": True},
            priority=5,
        )

    _with_scheduler_repo(database_path, _enqueue)
    logging.info("Enqueued RSS feed poll")


def build_scheduler(database_path: str, tickers: list[str]) -> "BlockingScheduler":
    scheduler = BlockingScheduler()
    scheduler.add_job(
        enqueue_scheduled_jobs,
        CronTrigger(hour=2, minute=0),
        args=[database_path, tickers],
        id="nightly_etl",
        replace_existing=True,
    )
    scheduler.add_job(
        enqueue_feed_poll,
        IntervalTrigger(minutes=45),
        args=[database_path],
        id="rss_poll",
        replace_existing=True,
    )
    return scheduler


def main() -> None:
    load_dotenv()
    setup_logging()
    if BlockingScheduler is None:
        logging.error("apscheduler is not installed; run `pip install -r requirements.txt`")
        sys.exit(1)
    config = Config()
    init_db(config.database_path)
    scheduler = build_scheduler(config.database_path, default_tickers())
    logging.info("Scheduler registered nightly ETL at 02:00 and RSS poll every 45 minutes")
    scheduler.start()


if __name__ == "__main__":
    main()
