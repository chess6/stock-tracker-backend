from dotenv import load_dotenv

from app.config import Config, default_tickers
from app.db import connect_db, init_db
from app.logging_config import setup_logging
from app.workers.handlers import build_context
from app.workers.runner import WorkerRunner


def main() -> None:
    load_dotenv()
    setup_logging()
    config = Config()
    init_db(config.database_path)
    conn = connect_db(config.database_path)
    tickers = default_tickers()
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
    WorkerRunner(ctx).run_forever()


if __name__ == "__main__":
    main()
