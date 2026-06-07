from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2])
    database_path: str | None = None
    nasdaq_api_key: str | None = None
    sec_user_agent: str | None = None
    sec_base_url: str = "https://data.sec.gov"
    sec_company_tickers_url: str = "https://www.sec.gov/files/company_tickers.json"
    request_timeout: int = 20
    news_http_ttl_seconds: int = 60 * 60
    admin_api_key: str | None = None

    def __post_init__(self) -> None:
        if self.database_path is None:
            default_path = self.base_dir / "data" / "stock_tracker.sqlite3"
            self.database_path = os.getenv("STOCK_TRACKER_DB_PATH", str(default_path))
        if self.nasdaq_api_key is None:
            self.nasdaq_api_key = os.getenv("NASDAQ_API_KEY")
        if self.sec_user_agent is None:
            self.sec_user_agent = os.getenv("SEC_USER_AGENT", "MyStockApp admin@example.com")
        timeout_value = os.getenv("STOCK_TRACKER_REQUEST_TIMEOUT")
        if timeout_value:
            self.request_timeout = int(timeout_value)
        if self.admin_api_key is None:
            self.admin_api_key = os.getenv("ADMIN_API_KEY")

    def to_flask_config(self) -> dict:
        return {
            "DATABASE_PATH": self.database_path,
            "NASDAQ_API_KEY": self.nasdaq_api_key,
            "SEC_USER_AGENT": self.sec_user_agent,
            "SEC_BASE_URL": self.sec_base_url,
            "SEC_COMPANY_TICKERS_URL": self.sec_company_tickers_url,
            "REQUEST_TIMEOUT": self.request_timeout,
            "NEWS_HTTP_TTL_SECONDS": self.news_http_ttl_seconds,
            "ADMIN_API_KEY": self.admin_api_key,
        }
