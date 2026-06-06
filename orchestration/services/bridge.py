from __future__ import annotations

import json
import traceback
from typing import Any

from ..config import get_settings
from ..db.session import init_db, session_scope
from ..queues.event_bus import EventBus
from ..queues.redis_queue import RedisEventQueue
from ..services.schemas import EventType


def _publish(event_type: EventType, payload: dict[str, Any], priority: int | None = None) -> int:
    init_db()
    with session_scope() as session:
        bus = EventBus(session)
        event_id = bus.publish(event_type, payload, priority=priority)
    redis = RedisEventQueue(get_settings().redis_url)
    if redis.available:
        redis.publish(event_type, payload, priority=priority)
    return event_id


def emit_news_ingested(article_id: int, tickers: list[str] | None = None, **extra: Any) -> int:
    return _publish(EventType.NEWS_INGESTED, {
        "article_id": article_id,
        "tickers": tickers or [],
        **extra,
    })


def emit_analysis_completed(tickers: list[str], analysis_id: int | None = None, **extra: Any) -> int:
    return _publish(EventType.ANALYSIS_COMPLETED, {
        "tickers": tickers,
        "analysis_id": analysis_id,
        **extra,
    })


def emit_fetch_failed(
    failure_type: str,
    source: str,
    error: str,
    *,
    job_type: str | None = None,
    tickers: list[str] | None = None,
    exc: BaseException | None = None,
) -> int:
    return _publish(EventType.FETCH_FAILED, {
        "failure_type": failure_type,
        "source": source,
        "error": error[:2000],
        "job_type": job_type,
        "tickers": tickers or [],
        "stack_trace": traceback.format_exc() if exc else "",
    }, priority=20)


def emit_portfolio_check(tickers: list[str] | None = None) -> int:
    return _publish(EventType.PORTFOLIO_CHECK, {"tickers": tickers or []})


def emit_high_priority_signal(ticker: str, score: float, drivers: list[str] | None = None) -> int:
    return _publish(EventType.HIGH_PRIORITY_SIGNAL, {
        "ticker": ticker,
        "score": score,
        "drivers": drivers or [],
    }, priority=10)
