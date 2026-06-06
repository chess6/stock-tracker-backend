from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db.models import OrchestratorEvent
from ..services.schemas import EVENT_PRIORITIES, EventType, OrchestratorEventPayload


class EventBus:
    """Publish and consume orchestrator events (SQLite-backed)."""

    def __init__(self, session: Session, worker_id: str = "orchestrator-1") -> None:
        self.session = session
        self.worker_id = worker_id

    def publish(
        self,
        event_type: EventType | str,
        payload: dict[str, Any] | None = None,
        *,
        priority: int | None = None,
        correlation_id: str | None = None,
        max_attempts: int = 5,
    ) -> int:
        et = EventType(event_type) if isinstance(event_type, str) else event_type
        event = OrchestratorEvent(
            event_type=et.value,
            priority=priority if priority is not None else EVENT_PRIORITIES.get(et, 100),
            payload_json=json.dumps(payload or {}),
            correlation_id=correlation_id or str(uuid.uuid4()),
            max_attempts=max_attempts,
        )
        self.session.add(event)
        self.session.flush()
        return event.id

    def publish_model(self, model: OrchestratorEventPayload) -> int:
        return self.publish(
            model.event_type,
            model.payload,
            priority=model.priority,
            correlation_id=model.correlation_id,
        )

    def claim_next(self) -> Optional[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        stmt = (
            select(OrchestratorEvent)
            .where(
                OrchestratorEvent.status == "pending",
                OrchestratorEvent.available_at <= now,
            )
            .order_by(OrchestratorEvent.priority.asc(), OrchestratorEvent.id.asc())
            .limit(1)
        )
        event = self.session.execute(stmt).scalar_one_or_none()
        if not event:
            return None
        event.status = "processing"
        event.locked_at = now
        event.locked_by = self.worker_id
        event.attempt_count += 1
        self.session.flush()
        return {
            "id": event.id,
            "event_type": event.event_type,
            "priority": event.priority,
            "payload": json.loads(event.payload_json or "{}"),
            "correlation_id": event.correlation_id,
            "attempt_count": event.attempt_count,
            "max_attempts": event.max_attempts,
        }

    def complete(self, event_id: int) -> None:
        self.session.execute(
            update(OrchestratorEvent)
            .where(OrchestratorEvent.id == event_id)
            .values(status="completed", locked_at=None, locked_by=None, last_error=None)
        )

    def fail(self, event_id: int, error: str) -> None:
        event = self.session.get(OrchestratorEvent, event_id)
        if not event:
            return
        if event.attempt_count >= event.max_attempts:
            event.status = "failed"
            event.last_error = error[:4000]
        else:
            delay = min(60 * (2 ** (event.attempt_count - 1)), 3600)
            event.status = "pending"
            event.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            event.last_error = error[:4000]
        event.locked_at = None
        event.locked_by = None
