from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from ..repositories import Repository

logger = logging.getLogger("stock_tracker.narrative")

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 300


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _day_key(value: str | None) -> str | None:
    dt = _parse_dt(value)
    return dt.date().isoformat() if dt else None


def _sentiment_value(article: dict) -> float | None:
    score = article.get("sentimentScore")
    if score is not None:
        return float(score)
    reaction = article.get("reactionSentiment")
    if reaction is not None:
        return float(reaction)
    return None


def _articles_in_window(articles: list[dict], days: int, *, end: datetime | None = None) -> list[dict]:
    end_dt = end or datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    filtered: list[dict] = []
    for article in articles:
        pub = _parse_dt(article.get("publishedAt"))
        if pub is None:
            continue
        if start_dt <= pub <= end_dt:
            filtered.append(article)
    return filtered


def _articles_between(
    articles: list[dict],
    start_days: int,
    end_days: int,
    *,
    end: datetime | None = None,
) -> list[dict]:
    end_dt = end or datetime.now(timezone.utc)
    outer_start = end_dt - timedelta(days=start_days)
    inner_end = end_dt - timedelta(days=end_days)
    filtered: list[dict] = []
    for article in articles:
        pub = _parse_dt(article.get("publishedAt"))
        if pub is None:
            continue
        if outer_start <= pub < inner_end:
            filtered.append(article)
    return filtered


def _avg_sentiment(articles: list[dict]) -> float | None:
    values = [v for article in articles if (v := _sentiment_value(article)) is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _price_change(prices: list[dict], days: int) -> float | None:
    if len(prices) < 2:
        return None
    ordered = sorted(prices, key=lambda row: row.get("date") or "")
    end_price = ordered[-1].get("close")
    if end_price in (None, 0):
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    start_price = None
    for row in ordered:
        if (row.get("date") or "") >= cutoff:
            start_price = row.get("close")
            break
    if start_price in (None, 0):
        start_price = ordered[0].get("close")
    if start_price in (None, 0):
        return None
    return (end_price - start_price) / start_price


def _build_daily_sentiment(articles: list[dict]) -> list[dict]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for article in articles:
        day = _day_key(article.get("publishedAt"))
        score = _sentiment_value(article)
        if day and score is not None:
            by_day[day].append(score)
    return [
        {
            "date": day,
            "avgSentiment": sum(values) / len(values),
            "articleCount": len(values),
        }
        for day, values in sorted(by_day.items())
    ]


def _rolling_average(series: list[dict], window_days: int) -> list[dict]:
    if not series:
        return []
    output: list[dict] = []
    for idx, point in enumerate(series):
        window_start = max(0, idx - window_days + 1)
        window = series[window_start : idx + 1]
        avg = sum(item["avgSentiment"] for item in window) / len(window)
        output.append({**point, "movingAvg": avg})
    return output


def _detect_divergence(
    articles: list[dict],
    prices: list[dict],
) -> dict[str, Any] | None:
    recent = _articles_in_window(articles, 90)
    prior = _articles_between(articles, 180, 90)
    recent_avg = _avg_sentiment(recent)
    prior_avg = _avg_sentiment(prior)
    price_change = _price_change(prices, 90)
    if recent_avg is None or prior_avg is None or price_change is None:
        return None

    sentiment_delta = recent_avg - prior_avg
    threshold = 0.05
    if sentiment_delta >= threshold and price_change <= -0.03:
        return {
            "type": "bullish_divergence",
            "description": "Sentiment improved while price declined over the last 90 days.",
            "sentimentChange90d": sentiment_delta,
            "priceChange90d": price_change,
        }
    if sentiment_delta <= -threshold and price_change >= 0.03:
        return {
            "type": "bearish_divergence",
            "description": "Sentiment weakened while price rose over the last 90 days.",
            "sentimentChange90d": sentiment_delta,
            "priceChange90d": price_change,
        }
    return None


def _build_event_timeline(articles: list[dict]) -> list[dict]:
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for article in articles:
        day = _day_key(article.get("publishedAt"))
        event_type = article.get("primaryEvent") or article.get("eventType") or "other"
        if not day:
            continue
        week_start = datetime.fromisoformat(day).date()
        week_start -= timedelta(days=week_start.weekday())
        key = (week_start.isoformat(), event_type)
        buckets[key].append(article)

    timeline: list[dict] = []
    for (week_start, event_type), group in sorted(buckets.items()):
        returns = [
            article["abnormalReturn1d"]
            for article in group
            if article.get("abnormalReturn1d") is not None
        ]
        timeline.append(
            {
                "weekStart": week_start,
                "eventType": event_type,
                "count": len(group),
                "avgSentiment": _avg_sentiment(group),
                "avgAbnormalReturn1d": (sum(returns) / len(returns)) if returns else None,
            }
        )
    return timeline[-52:]


def _rank_top_events(articles: list[dict], *, limit: int = 15) -> list[dict]:
    ranked = sorted(
        [
            article
            for article in articles
            if article.get("abnormalReturn1d") is not None
        ],
        key=lambda article: abs(article.get("abnormalReturn1d") or 0),
        reverse=True,
    )
    output: list[dict] = []
    for article in ranked[:limit]:
        output.append(
            {
                "articleId": article.get("id"),
                "title": article.get("title"),
                "publishedAt": article.get("publishedAt"),
                "eventType": article.get("primaryEvent") or article.get("eventType"),
                "sentimentScore": _sentiment_value(article),
                "sentimentLabel": article.get("sentimentLabel"),
                "abnormalReturn1d": article.get("abnormalReturn1d"),
                "return1d": article.get("return1d"),
                "url": article.get("url"),
            }
        )
    return output


def _cluster_events(articles: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for article in articles:
        event_type = article.get("primaryEvent") or article.get("eventType") or "other"
        grouped[event_type].append(article)

    clusters: list[dict] = []
    for event_type, group in grouped.items():
        ordered = sorted(group, key=lambda item: item.get("publishedAt") or "")
        if not ordered:
            continue
        window: list[dict] = []
        window_start: str | None = None
        for article in ordered:
            day = _day_key(article.get("publishedAt"))
            if not day:
                continue
            if not window:
                window = [article]
                window_start = day
                continue
            start_dt = datetime.fromisoformat(window_start)
            current_dt = datetime.fromisoformat(day)
            if (current_dt - start_dt).days <= 7:
                window.append(article)
            else:
                if len(window) >= 2:
                    clusters.append(
                        {
                            "eventType": event_type,
                            "startDate": window_start,
                            "endDate": _day_key(window[-1].get("publishedAt")),
                            "articleCount": len(window),
                            "avgSentiment": _avg_sentiment(window),
                        }
                    )
                window = [article]
                window_start = day
        if len(window) >= 2:
            clusters.append(
                {
                    "eventType": event_type,
                    "startDate": window_start,
                    "endDate": _day_key(window[-1].get("publishedAt")),
                    "articleCount": len(window),
                    "avgSentiment": _avg_sentiment(window),
                }
            )
    clusters.sort(key=lambda item: (item.get("startDate") or "", item.get("articleCount") or 0), reverse=True)
    return clusters[:20]


def _build_price_overlay(
    prices: list[dict],
    daily_sentiment: list[dict],
) -> list[dict]:
    sentiment_by_day = {point["date"]: point["avgSentiment"] for point in daily_sentiment}
    ordered_prices = sorted(prices, key=lambda row: row.get("date") or "")
    rolling_window: list[float] = []
    output: list[dict] = []
    for row in ordered_prices[-365:]:
        day = (row.get("date") or "")[:10]
        sentiment = sentiment_by_day.get(day)
        if sentiment is not None:
            rolling_window.append(sentiment)
            if len(rolling_window) > 30:
                rolling_window = rolling_window[-30:]
        output.append(
            {
                "date": day,
                "close": row.get("close"),
                "sentiment": sentiment,
                "sentimentMa30": (sum(rolling_window) / len(rolling_window)) if rolling_window else None,
            }
        )
    return output


def build_narrative_analysis(
    repo: Repository,
    ticker: str,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    symbol = ticker.upper()
    if use_cache:
        cached = _CACHE.get(symbol)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

    company = repo.get_company_by_ticker(symbol)
    if not company:
        payload = {"ticker": symbol, "error": "not_found"}
        return payload

    articles = repo.fetch_narrative_articles_for_ticker(symbol)
    prices = list(reversed(repo.fetch_prices(symbol, limit=365)))

    daily_sentiment = _build_daily_sentiment(articles)
    daily_with_ma = _rolling_average(daily_sentiment, 30)

    payload: dict[str, Any] = {
        "ticker": symbol,
        "companyName": company.get("name"),
        "sentimentTrend": {
            "movingAverages": {
                "30d": _avg_sentiment(_articles_in_window(articles, 30)),
                "90d": _avg_sentiment(_articles_in_window(articles, 90)),
                "180d": _avg_sentiment(_articles_in_window(articles, 180)),
            },
            "dailySeries": daily_with_ma[-180:],
        },
        "priceOverlay": _build_price_overlay(prices, daily_sentiment),
        "divergence": _detect_divergence(articles, prices),
        "eventTimeline": _build_event_timeline(articles),
        "topEvents": _rank_top_events(articles),
        "eventClusters": _cluster_events(articles),
        "recentArticles": [
            {
                "articleId": article.get("id"),
                "title": article.get("title"),
                "publishedAt": article.get("publishedAt"),
                "sentimentScore": _sentiment_value(article),
                "sentimentLabel": article.get("sentimentLabel"),
                "eventType": article.get("primaryEvent") or article.get("eventType"),
                "abnormalReturn1d": article.get("abnormalReturn1d"),
                "return1d": article.get("return1d"),
                "url": article.get("url"),
            }
            for article in articles[:25]
        ],
        "articleCount": len(articles),
    }

    if use_cache:
        _CACHE[symbol] = (time.time(), payload)
    logger.debug(
        "build_narrative_analysis ticker=%s articles=%d prices=%d",
        symbol,
        len(articles),
        len(prices),
    )
    return payload


def clear_narrative_cache() -> None:
    _CACHE.clear()
