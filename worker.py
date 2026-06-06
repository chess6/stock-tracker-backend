import logging
import os

from dotenv import load_dotenv

from app.config import Config
from app.db import connect_db, init_db
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


def enqueue_scheduled_jobs(repo, tickers: list[str]) -> None:
    repo.enqueue_job("sync_companies", {}, priority=10)
    repo.enqueue_job("refresh_fundamentals", {"tickers": tickers}, priority=20)
    repo.enqueue_job("refresh_prices", {"tickers": tickers}, priority=30)
    repo.enqueue_job("refresh_insiders", {"tickers": tickers}, priority=40)
    repo.enqueue_job(
        "ingest_default_feeds",
        {"extract_articles": False, "max_articles_per_feed": 25, "force_refresh": True},
        priority=50,
    )


def enqueue_feed_poll(repo) -> None:
    repo.enqueue_job(
        "ingest_default_feeds",
        {"extract_articles": False, "max_articles_per_feed": 25, "force_refresh": True},
        priority=5,
    )


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
            lambda: enqueue_scheduled_jobs(ctx.repo, tickers),
            CronTrigger(hour=2, minute=0),
            id="nightly_etl",
            replace_existing=True,
        )
        if IntervalTrigger is not None:
            scheduler.add_job(
                lambda: enqueue_feed_poll(ctx.repo),
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
