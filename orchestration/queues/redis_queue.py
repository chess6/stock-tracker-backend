from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ..config import get_settings
from ..services.schemas import EVENT_PRIORITIES, EventType

logger = logging.getLogger(__name__)

QUEUE_KEY = "orchestrator:events:priority"


class RedisEventQueue:
    """Optional hot queue — falls back gracefully if Redis unavailable."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url or get_settings().redis_url
        self._client = None

    @property
    def available(self) -> bool:
        if not self.redis_url:
            return False
        try:
            return self.client is not None
        except Exception:
            return False

    @property
    def client(self):
        if self._client is None and self.redis_url:
            import redis

            self._client = redis.from_url(self.redis_url, decode_responses=True)
            self._client.ping()
        return self._client

    def publish(self, event_type: EventType | str, payload: dict[str, Any], priority: int | None = None) -> bool:
        if not self.available:
            return False
        et = EventType(event_type) if isinstance(event_type, str) else event_type
        score = priority if priority is not None else EVENT_PRIORITIES.get(et, 100)
        body = json.dumps({"event_type": et.value, "payload": payload, "priority": score})
        self.client.zadd(QUEUE_KEY, {body: score})
        return True

    def pop(self) -> Optional[dict[str, Any]]:
        if not self.available:
            return None
        items = self.client.zpopmin(QUEUE_KEY, count=1)
        if not items:
            return None
        raw, _score = items[0]
        return json.loads(raw)
