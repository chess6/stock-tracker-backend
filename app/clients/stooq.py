from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

import requests


class StooqClient:
    BASE_URL = "https://stooq.com/q/d/l/"

    def __init__(self, timeout: int = 20, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()

    @staticmethod
    def symbol_for_ticker(ticker: str) -> str:
        return f"{ticker.lower().replace('.', '-')}.us"

    def fetch_daily_csv(self, ticker: str, days: int = 3650) -> list[dict]:
        symbol = self.symbol_for_ticker(ticker)
        response = self.session.get(
            self.BASE_URL,
            params={"s": symbol, "i": "d"},
            timeout=self.timeout,
            headers={"User-Agent": "StockTracker/1.0"},
        )
        response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.text))
        cutoff = datetime.utcnow().date() - timedelta(days=days)
        rows = []
        for row in reader:
            date_str = (row.get("Date") or "").strip()
            if not date_str:
                continue
            try:
                row_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if row_date < cutoff:
                continue
            rows.append(
                {
                    "date": date_str,
                    "open": _float(row.get("Open")),
                    "high": _float(row.get("High")),
                    "low": _float(row.get("Low")),
                    "close": _float(row.get("Close")),
                    "volume": _float(row.get("Volume")),
                }
            )
        return sorted(rows, key=lambda item: item["date"])


def _float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
