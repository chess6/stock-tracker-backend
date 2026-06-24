"""Forward catalyst calendar — derived from fundamentals cadence (free-first)."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..repositories import Repository

try:
    import yfinance as yf  # type: ignore
except ImportError:  # pragma: no cover
    yf = None

logger = logging.getLogger(__name__)

QUARTERLY_DAYS = 91
EARNINGS_IMMINENT_PAST_DAYS = 3
EARNINGS_IMMINENT_FUTURE_DAYS = 14


def _project_next_earnings(last_period_end: date, *, anchor: date | None = None) -> date:
    """Roll forward quarterly from last reported period end."""
    anchor = anchor or date.today()
    next_est = last_period_end + timedelta(days=QUARTERLY_DAYS)
    guard = 0
    while next_est < anchor - timedelta(days=EARNINGS_IMMINENT_PAST_DAYS) and guard < 8:
        next_est += timedelta(days=QUARTERLY_DAYS)
        guard += 1
    return next_est


def derive_earnings_dates_from_fundamentals(repo: Repository, *, limit: int = 500) -> list[dict[str, Any]]:
    rows = repo.conn.execute(
        """
        SELECT c.ticker, MAX(f.period_end) AS last_period_end
        FROM fundamentals f
        JOIN companies c ON c.id = f.company_id
        WHERE f.period_end IS NOT NULL
        GROUP BY c.ticker
        ORDER BY last_period_end DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        ticker = row["ticker"]
        last_end = row["last_period_end"]
        if not last_end:
            continue
        try:
            parsed = date.fromisoformat(str(last_end)[:10])
        except ValueError:
            continue
        next_est = _project_next_earnings(parsed)
        records.append(
            {
                "ticker": ticker,
                "event_type": "earnings",
                "event_date": next_est.isoformat(),
                "source": "derived_fundamentals",
                "confidence": 0.55,
                "details": {"lastPeriodEnd": last_end, "method": "period_end_plus_91d_rolled"},
            }
        )
    return records


def _parse_yfinance_earnings_date(raw: Any) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if hasattr(raw, "date"):
        try:
            return raw.date()
        except (TypeError, ValueError):
            pass
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def fetch_yfinance_earnings_dates(tickers: list[str]) -> list[dict[str, Any]]:
    """Best-effort earnings dates from yfinance (portfolio / priority tickers)."""
    if yf is None or not tickers:
        return []
    records: list[dict[str, Any]] = []
    for ticker in tickers:
        symbol = str(ticker).strip().upper()
        if not symbol:
            continue
        earnings_date: date | None = None
        try:
            stock = yf.Ticker(symbol)
            calendar = stock.calendar
            if isinstance(calendar, dict):
                earnings_date = _parse_yfinance_earnings_date(calendar.get("Earnings Date"))
            if earnings_date is None:
                frame = stock.earnings_dates
                if frame is not None and not getattr(frame, "empty", True):
                    today = date.today()
                    for idx in frame.index[:6]:
                        parsed = _parse_yfinance_earnings_date(idx)
                        if parsed is not None and parsed >= today - timedelta(days=EARNINGS_IMMINENT_PAST_DAYS):
                            earnings_date = parsed
                            break
        except Exception as exc:
            logger.warning("yfinance earnings lookup failed ticker=%s err=%s", symbol, exc)
            continue
        if earnings_date is None:
            continue
        records.append(
            {
                "ticker": symbol,
                "event_type": "earnings",
                "event_date": earnings_date.isoformat(),
                "source": "yfinance_earnings",
                "confidence": 0.82,
                "details": {"method": "yfinance_calendar"},
            }
        )
    return records


def upsert_catalyst_calendar(repo: Repository, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    rows = []
    for record in records:
        details = record.get("details")
        rows.append(
            (
                str(record["ticker"]).upper(),
                str(record.get("event_type") or "earnings").lower(),
                record["event_date"],
                record.get("source") or "derived",
                record.get("confidence"),
                json.dumps(details) if details is not None else None,
            )
        )
    repo.conn.executemany(
        """
        INSERT INTO catalyst_calendar (
            ticker, event_type, event_date, source, confidence, details_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, event_type, event_date) DO UPDATE SET
            source = excluded.source,
            confidence = excluded.confidence,
            details_json = excluded.details_json
        """,
        rows,
    )
    repo.commit()
    return len(rows)


def refresh_derived_catalyst_calendar(repo: Repository, *, limit: int = 500) -> dict[str, Any]:
    records = derive_earnings_dates_from_fundamentals(repo, limit=limit)
    priority_tickers = list(dict.fromkeys(
        [*repo.fetch_portfolio_tickers(), *repo.get_boosted_tickers()]
    ))
    yf_records = fetch_yfinance_earnings_dates(priority_tickers[:80])
    merged = {f"{r['ticker']}:{r['event_date']}": r for r in records}
    for row in yf_records:
        merged[f"{row['ticker']}:{row['event_date']}"] = row
    all_records = list(merged.values())
    written = upsert_catalyst_calendar(repo, all_records)
    return {
        "derived": len(records),
        "yfinance": len(yf_records),
        "upserted": written,
    }


def fetch_upcoming_catalysts(
    repo: Repository,
    *,
    limit: int = 50,
    horizon_days: int = 45,
    include_past_days: int = EARNINGS_IMMINENT_PAST_DAYS,
) -> list[dict[str, Any]]:
    """Include recent past dates so same-day / after-hours earnings still surface."""
    window_start = (date.today() - timedelta(days=max(0, int(include_past_days)))).isoformat()
    horizon = (date.today() + timedelta(days=horizon_days)).isoformat()
    rows = repo.conn.execute(
        """
        SELECT ticker, event_type, event_date, source, confidence, details_json
        FROM catalyst_calendar
        WHERE event_date >= ?
          AND event_date <= ?
        ORDER BY ABS(julianday(event_date) - julianday('now')) ASC, confidence DESC
        LIMIT ?
        """,
        (window_start, horizon, max(1, int(limit))),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = row["details_json"]
        if raw:
            try:
                item["details"] = json.loads(raw)
            except (TypeError, ValueError):
                item["details"] = None
        items.append(item)
    return items
