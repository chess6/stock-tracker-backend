"""FINRA/Nasdaq semi-monthly short interest ingestion (B7)."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime

import requests

from ..repositories import Repository
from .fundamentals import fetch_resolved_wide_rows, resolve_financial_dimension

try:
    import yfinance as yf  # type: ignore
except ImportError:  # pragma: no cover
    yf = None

logger = logging.getLogger("stock_tracker.pipeline.finra")

# Legacy Nasdaq trader directory (often 404); yfinance fallback when unavailable.
SHORT_INTEREST_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqshortinterest.txt"


class FinraShortInterestService:
    def __init__(
        self,
        repo: Repository,
        *,
        timeout: int = 30,
        user_agent: str = "stock-tracker/1.0",
    ) -> None:
        self.repo = repo
        self.timeout = timeout
        self.user_agent = user_agent

    def refresh_short_interest(self, tickers: list[str] | None = None) -> dict:
        logger.info("refresh_short_interest start")
        rows: list[dict] = []
        source = "finra_short_interest"
        try:
            response = requests.get(
                SHORT_INTEREST_URL,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
            )
            response.raise_for_status()
            rows = parse_short_interest_file(response.text)
        except requests.RequestException as exc:
            logger.warning("refresh_short_interest fetch failed, using yfinance fallback: %s", exc)
            rows = []
        if not rows:
            rows = self._fetch_yfinance_short_interest(tickers)
            source = "yfinance_short_interest"
        target = {t.upper() for t in tickers} if tickers else None
        written = 0
        for row in rows:
            symbol = row.get("ticker")
            if not symbol:
                continue
            if target is not None and symbol not in target:
                continue
            short_pct = row.get("short_interest_pct")
            if short_pct is None:
                short_pct = self._short_interest_pct(symbol, row.get("short_interest_shares"))
            if short_pct is None:
                continue
            written += self.repo.upsert_company_market_data(
                symbol,
                row.get("as_of_date"),
                "short_interest_pct",
                short_pct,
                source=source,
            )
            self._upsert_short_interest_snapshot(symbol, row, source=source)
        logger.info("refresh_short_interest done written=%d parsed=%d source=%s", written, len(rows), source)
        return {"written": written, "parsed": len(rows), "source": source}

    def _upsert_short_interest_snapshot(self, ticker: str, row: dict, *, source: str) -> None:
        settlement = row.get("as_of_date")
        short_shares = row.get("short_interest_shares")
        if not settlement or short_shares is None:
            return
        avg_volume = self._latest_avg_volume(ticker)
        days_to_cover = None
        if avg_volume and avg_volume > 0:
            days_to_cover = float(short_shares) / float(avg_volume)
        self.repo.conn.execute(
            """
            INSERT INTO short_interest_snapshots (
                ticker, settlement_date, short_interest, avg_daily_volume, days_to_cover, source
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, settlement_date, source) DO UPDATE SET
                short_interest = excluded.short_interest,
                avg_daily_volume = excluded.avg_daily_volume,
                days_to_cover = excluded.days_to_cover
            """,
            (ticker.upper(), settlement, float(short_shares), avg_volume, days_to_cover, source),
        )
        self.repo.commit()

    def _latest_avg_volume(self, ticker: str) -> float | None:
        row = self.repo.conn.execute(
            """
            SELECT AVG(volume) AS avg_volume
            FROM (
                SELECT volume
                FROM prices
                WHERE ticker = ?
                  AND volume IS NOT NULL
                  AND volume > 0
                ORDER BY date DESC
                LIMIT 20
            )
            """,
            (ticker.upper(),),
        ).fetchone()
        if not row or row["avg_volume"] is None:
            return None
        return float(row["avg_volume"])

    def _fetch_yfinance_short_interest(self, tickers: list[str] | None) -> list[dict]:
        if yf is None:
            logger.warning("refresh_short_interest yfinance unavailable")
            return []
        symbols = [t.upper() for t in tickers if t] if tickers else []
        if not symbols:
            return []
        as_of = datetime.utcnow().strftime("%Y-%m-%d")
        rows: list[dict] = []
        for symbol in symbols:
            try:
                info = yf.Ticker(symbol).info or {}
            except Exception as exc:
                logger.warning("yfinance short interest lookup failed ticker=%s err=%s", symbol, exc)
                continue
            short_pct = info.get("shortPercentOfFloat")
            if short_pct is None:
                shares_short = info.get("sharesShort")
                float_shares = info.get("floatShares") or info.get("sharesOutstanding")
                if shares_short and float_shares:
                    short_pct = float(shares_short) / float(float_shares)
            if short_pct is None:
                continue
            # yfinance returns fraction (e.g. 0.008); normalize to percent 0–100.
            short_pct = float(short_pct)
            if 0 < short_pct <= 1.0:
                short_pct *= 100.0
            rows.append(
                {
                    "ticker": symbol,
                    "short_interest_shares": info.get("sharesShort"),
                    "short_interest_pct": short_pct,
                    "as_of_date": as_of,
                }
            )
        return rows

    def _short_interest_pct(self, ticker: str, short_shares: float | None) -> float | None:
        if short_shares in (None, 0):
            return None
        shares_out = self._latest_shares_outstanding(ticker)
        if shares_out in (None, 0):
            return None
        return (float(short_shares) / float(shares_out)) * 100.0

    def _latest_shares_outstanding(self, ticker: str) -> float | None:
        resolved = resolve_financial_dimension("MRY", most_recent=False)
        rows = fetch_resolved_wide_rows(self.repo, [ticker], gte=None, resolved=resolved)
        if not rows:
            return None
        shares = rows[0].get("sharesbas")
        return float(shares) if shares is not None else None


def parse_short_interest_file(text: str) -> list[dict]:
    """
    Parse Nasdaq short interest directory file.
    Format: Symbol|Company Name|Short Interest|Settlement Date|Market
    """
    if not text or "Symbol|" not in text:
        return []
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    rows: list[dict] = []
    for record in reader:
        symbol = (record.get("Symbol") or record.get("NASDAQ Symbol") or "").strip().upper()
        if not symbol:
            continue
        short_interest_raw = record.get("Short Interest") or record.get("ShortInterest")
        settlement = record.get("Settlement Date") or record.get("SettlementDate") or ""
        try:
            short_interest = float(str(short_interest_raw).replace(",", "")) if short_interest_raw else None
        except (TypeError, ValueError):
            short_interest = None
        as_of = settlement.strip() or datetime.utcnow().strftime("%Y-%m-%d")
        rows.append(
            {
                "ticker": symbol,
                "short_interest_shares": short_interest,
                "short_interest_pct": None,
                "as_of_date": as_of,
            }
        )
    return rows
