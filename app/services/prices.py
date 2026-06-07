from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from ..clients.stooq import StooqClient
from ..repositories import Repository

logger = logging.getLogger("stock_tracker.pipeline.prices")

try:
    import yfinance as yf  # type: ignore
except ImportError:  # pragma: no cover
    yf = None


class PricesService:
    def __init__(self, repo: Repository, stooq: StooqClient | None = None) -> None:
        self.repo = repo
        self.stooq = stooq or StooqClient()

    def refresh_prices(self, tickers: list[str], days: int = 400) -> dict:
        logger.info("refresh_prices start tickers=%d days=%d", len(tickers), days)
        t0 = time.monotonic()
        refreshed = []
        records_written = 0
        for ticker in [item.upper() for item in tickers if item]:
            rows = self._fetch_ticker_prices(ticker, days=days)
            if rows:
                source = rows[0].get("source", "stooq")
                records_written += self.repo.upsert_prices(ticker, rows, source=source)
                logger.debug("refresh_prices ticker=%s rows=%d source=%s", ticker, len(rows), source)
                refreshed.append(ticker)
            else:
                logger.debug("refresh_prices ticker=%s no data", ticker)
        elapsed = time.monotonic() - t0
        logger.info("refresh_prices done tickers=%d records=%d elapsed=%.1fs", len(refreshed), records_written, elapsed)
        return {"tickers": refreshed, "recordsWritten": records_written}

    def _fetch_ticker_prices(self, ticker: str, days: int) -> list[dict]:
        try:
            rows = self.stooq.fetch_daily_csv(ticker, days=days)
            if rows:
                for row in rows:
                    row["source"] = "stooq"
                return rows
        except Exception as exc:
            logger.debug("stooq fetch failed ticker=%s: %s", ticker, exc)
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
        batch = self.repo.fetch_prices_batch(tickers, limit_per_ticker=2)
        quotes = {}
        for ticker in tickers:
            rows = batch.get(ticker.upper(), [])
            latest = rows[0] if rows else {}
            previous = rows[1] if len(rows) > 1 else {}
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
        batch = self.repo.fetch_prices_batch(tickers, limit_per_ticker=2)
        changes = {}
        for ticker in tickers:
            rows = batch.get(ticker.upper(), [])
            today_close = rows[0]["close"] if rows else None
            prev_close = rows[1]["close"] if len(rows) > 1 else None
            changes[ticker.upper()] = {"prevClose": prev_close, "todayClose": today_close}
        return changes

    @staticmethod
    def _pct_change(current: float | None, past: float | None) -> float | None:
        if current is None or past is None or past == 0:
            return None
        return ((current - past) / abs(past)) * 100

    def get_market_stats(self, tickers: list[str]) -> dict:
        """Multi-horizon returns and 52-week range stats from cached prices."""
        horizons = {
            "change1w": 5,
            "change4w": 20,
            "change16w": 80,
            "change6m": 126,
        }
        batch = self.repo.fetch_prices_batch(tickers, limit_per_ticker=260)
        stats: dict[str, dict] = {}
        for ticker in tickers:
            symbol = ticker.upper()
            rows = batch.get(symbol, [])
            if not rows:
                stats[symbol] = {}
                continue
            latest_close = rows[0].get("close")
            entry: dict = {}
            window = rows[:252]
            highs = [row["high"] for row in window if row.get("high") is not None]
            lows = [row["low"] for row in window if row.get("low") is not None]
            if highs and lows and latest_close is not None:
                high52 = max(highs)
                low52 = min(lows)
                entry["high52w"] = high52
                entry["low52w"] = low52
                entry["pctTo52wHi"] = self._pct_change(latest_close, high52)
                entry["pctFrom52wLo"] = self._pct_change(latest_close, low52)
            for key, offset in horizons.items():
                if len(rows) > offset:
                    past_close = rows[offset].get("close")
                    entry[key] = self._pct_change(latest_close, past_close)
            stats[symbol] = entry
        return stats

    def get_movers(self, window: str = "d", threshold: float = 10.0, limit: int = 50) -> list[dict]:
        """Daily or weekly movers exceeding threshold % from cached prices."""
        offset = 1 if window == "d" else 5
        candidates = self.repo.fetch_tickers_with_recent_prices(limit=500)
        batch = self.repo.fetch_prices_batch(candidates, limit_per_ticker=offset + 2)
        movers: list[dict] = []
        for ticker in candidates:
            rows = batch.get(ticker, [])
            if len(rows) <= offset:
                continue
            latest = rows[0].get("close")
            past = rows[offset].get("close")
            change = self._pct_change(latest, past)
            if change is None or abs(change) < threshold:
                continue
            movers.append({
                "ticker": ticker,
                "price": latest,
                "change": change,
                "window": window,
            })
        movers.sort(key=lambda item: abs(item["change"]), reverse=True)
        return movers[:limit]
