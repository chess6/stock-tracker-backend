from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..repositories import Repository

logger = logging.getLogger("stock_tracker.pipeline.market_reaction")

BENCHMARK_TICKER = "SPY"


@dataclass
class MarketReaction:
    ticker: str
    published_at: str | None
    sentiment_score: float | None
    primary_event: str | None
    price_at_publish: float | None
    return_1d: float | None
    return_1w: float | None
    benchmark_return_1d: float | None
    abnormal_return_1d: float | None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _price_on_or_before(repo: Repository, ticker: str, dt: datetime) -> float | None:
    day = dt.date().isoformat()
    row = repo.conn.execute(
        """
        SELECT close
        FROM prices
        WHERE ticker = ? AND date <= ?
        ORDER BY date DESC, CASE source WHEN 'stooq' THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (ticker.upper(), day),
    ).fetchone()
    return float(row["close"]) if row and row["close"] is not None else None


def _return_between(
    repo: Repository,
    ticker: str,
    start_dt: datetime,
    end_dt: datetime,
) -> float | None:
    start_price = _price_on_or_before(repo, ticker, start_dt)
    end_price = _price_on_or_before(repo, ticker, end_dt)
    if start_price is None or end_price is None or start_price == 0:
        return None
    return (end_price - start_price) / start_price


def compute_market_reactions(
    repo: Repository,
    *,
    article_id: int,
    tickers: list[str],
    published_at: str | None,
    sentiment_score: float | None,
    primary_event: str | None,
) -> list[MarketReaction]:
    pub_dt = _parse_dt(published_at)
    if pub_dt is None:
        return []

    reactions: list[MarketReaction] = []
    for ticker in tickers:
        ticker = ticker.upper()
        price_at = _price_on_or_before(repo, ticker, pub_dt)
        ret_1d = _return_between(repo, ticker, pub_dt, pub_dt + timedelta(days=1))
        ret_1w = _return_between(repo, ticker, pub_dt, pub_dt + timedelta(days=7))
        bench_1d = _return_between(repo, BENCHMARK_TICKER, pub_dt, pub_dt + timedelta(days=1))
        abnormal = None
        if ret_1d is not None and bench_1d is not None:
            abnormal = ret_1d - bench_1d
        reactions.append(
            MarketReaction(
                ticker=ticker,
                published_at=published_at,
                sentiment_score=sentiment_score,
                primary_event=primary_event,
                price_at_publish=price_at,
                return_1d=ret_1d,
                return_1w=ret_1w,
                benchmark_return_1d=bench_1d,
                abnormal_return_1d=abnormal,
            )
        )
    return reactions


def backfill_market_reactions(
    repo: Repository,
    *,
    ticker: str | None = None,
    limit: int = 200,
) -> dict:
    """Recompute stored market reactions using current price/benchmark data."""
    symbol = ticker.upper() if ticker else None
    params: list[object] = []
    ticker_clause = ""
    if symbol:
        ticker_clause = "AND c.ticker = ?"
        params.append(symbol)

    rows = repo.conn.execute(
        f"""
        SELECT DISTINCT a.id, a.published_at, a.sentiment_score
        FROM articles a
        JOIN article_company ac ON ac.article_id = a.id AND ac.confidence >= 0.80
        JOIN companies c ON c.id = ac.company_id
        WHERE a.duplicate_of_article_id IS NULL
          {ticker_clause}
        ORDER BY a.published_at DESC, a.id DESC
        LIMIT ?
        """,
        [*params, int(limit)],
    ).fetchall()

    updated = 0
    for row in rows:
        article_id = row["id"]
        event_row = repo.conn.execute(
            """
            SELECT event_type
            FROM article_event_classifications
            WHERE article_id = ?
            ORDER BY confidence DESC
            LIMIT 1
            """,
            (article_id,),
        ).fetchone()
        tickers = repo.get_article_tickers(article_id)
        if not tickers:
            continue
        reactions = compute_market_reactions(
            repo,
            article_id=article_id,
            tickers=tickers,
            published_at=row["published_at"],
            sentiment_score=row["sentiment_score"],
            primary_event=event_row["event_type"] if event_row else None,
        )
        repo.replace_article_market_reactions(article_id, reactions)
        updated += 1

    logger.info(
        "backfill_market_reactions ticker=%s articles=%d limit=%d",
        symbol or "*",
        updated,
        limit,
    )
    return {"ticker": symbol, "articlesUpdated": updated, "limit": int(limit)}
