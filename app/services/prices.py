from __future__ import annotations

from datetime import datetime, timedelta

from ..clients.stooq import StooqClient
from ..repositories import Repository

try:
    import yfinance as yf  # type: ignore
except ImportError:  # pragma: no cover
    yf = None


class PricesService:
    def __init__(self, repo: Repository, stooq: StooqClient | None = None) -> None:
        self.repo = repo
        self.stooq = stooq or StooqClient()

    def refresh_prices(self, tickers: list[str], days: int = 400) -> dict:
        refreshed = []
        records_written = 0
        for ticker in [item.upper() for item in tickers if item]:
            rows = self._fetch_ticker_prices(ticker, days=days)
            if rows:
                records_written += self.repo.upsert_prices(ticker, rows, source=rows[0].get("source", "stooq"))
                refreshed.append(ticker)
        return {"tickers": refreshed, "recordsWritten": records_written}

    def _fetch_ticker_prices(self, ticker: str, days: int) -> list[dict]:
        try:
            rows = self.stooq.fetch_daily_csv(ticker, days=days)
            if rows:
                for row in rows:
                    row["source"] = "stooq"
                return rows
        except Exception:
            pass
        if yf is None:
            return []
        try:
            history = yf.Ticker(ticker).history(period=f"{days}d", auto_adjust=False)
        except Exception:
            return []
        if history.empty:
            return []
        rows = []
        for index, row in history.iterrows():
            rows.append(
                {
                    "date": index.strftime("%Y-%m-%d"),
                    "open": float(row["Open"]) if row.get("Open") == row["Open"] else None,
                    "high": float(row["High"]) if row.get("High") == row["High"] else None,
                    "low": float(row["Low"]) if row.get("Low") == row["Low"] else None,
                    "close": float(row["Close"]) if row.get("Close") == row["Close"] else None,
                    "volume": float(row["Volume"]) if row.get("Volume") == row["Volume"] else None,
                    "source": "yfinance",
                }
            )
        return rows

    def get_price_history(self, ticker: str, days: int = 3650) -> list[dict]:
        cutoff = (datetime.utcnow().date() - timedelta(days=days)).isoformat()
        rows = self.repo.fetch_prices(ticker, since=cutoff)
        return [
            {
                "date": row["date"],
                "close": row["close"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "volume": row["volume"],
            }
            for row in rows
        ]

    def get_quotes(self, tickers: list[str]) -> dict:
        quotes = {}
        for ticker in tickers:
            latest_two = self.repo.fetch_prices(ticker.upper(), limit=2)
            latest = latest_two[0] if latest_two else {}
            previous = latest_two[1] if len(latest_two) > 1 else {}
            quotes[ticker.upper()] = {
                "last": latest.get("close"),
                "prevClose": previous.get("close"),
                "open": latest.get("open"),
                "high": latest.get("high"),
                "low": latest.get("low"),
                "timestamp": latest.get("date"),
            }
        return quotes

    def get_daily_changes(self, tickers: list[str]) -> dict:
        changes = {}
        for ticker in tickers:
            latest_two = self.repo.fetch_prices(ticker.upper(), limit=2)
            today_close = latest_two[0]["close"] if latest_two else None
            prev_close = latest_two[1]["close"] if len(latest_two) > 1 else None
            changes[ticker.upper()] = {"prevClose": prev_close, "todayClose": today_close}
        return changes
