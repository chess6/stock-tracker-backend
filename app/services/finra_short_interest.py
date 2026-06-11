"""FINRA/Nasdaq semi-monthly short interest ingestion (B7)."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime

import requests

from ..repositories import Repository

logger = logging.getLogger("stock_tracker.pipeline.finra")

# Public semi-monthly short interest publication (Nasdaq trader directory)
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
        try:
            response = requests.get(
                SHORT_INTEREST_URL,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("refresh_short_interest fetch failed: %s", exc)
            return {"written": 0, "error": str(exc), "source": "finra_short_interest"}

        rows = parse_short_interest_file(response.text)
        target = {t.upper() for t in tickers} if tickers else None
        written = 0
        for row in rows:
            symbol = row.get("ticker")
            if not symbol:
                continue
            if target is not None and symbol not in target:
                continue
            written += self.repo.upsert_company_market_data(
                symbol,
                row.get("as_of_date"),
                "short_interest_pct",
                row.get("short_interest_pct"),
                source="finra_short_interest",
            )
        logger.info("refresh_short_interest done written=%d parsed=%d", written, len(rows))
        return {"written": written, "parsed": len(rows), "source": "finra_short_interest"}


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
