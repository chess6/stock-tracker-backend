from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AgentMemory, Analysis


class MemoryStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        return self._session

    def put(self, memory_type: str, key: str, value: dict[str, Any], *, ticker: str | None = None) -> None:
        existing = self.session.execute(
            select(AgentMemory).where(
                AgentMemory.memory_type == memory_type,
                AgentMemory.key == key,
            )
        ).scalar_one_or_none()
        payload = json.dumps(value)
        if existing:
            existing.value_json = payload
            existing.ticker = ticker
            existing.updated_at = datetime.now(timezone.utc)
        else:
            self.session.add(
                AgentMemory(
                    memory_type=memory_type,
                    key=key,
                    value_json=payload,
                    ticker=ticker,
                )
            )

    def get(self, memory_type: str, key: str) -> Optional[dict[str, Any]]:
        row = self.session.execute(
            select(AgentMemory).where(
                AgentMemory.memory_type == memory_type,
                AgentMemory.key == key,
            )
        ).scalar_one_or_none()
        return json.loads(row.value_json) if row else None

    def list_by_ticker(self, ticker: str, memory_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        stmt = select(AgentMemory).where(AgentMemory.ticker == ticker.upper())
        if memory_type:
            stmt = stmt.where(AgentMemory.memory_type == memory_type)
        stmt = stmt.order_by(AgentMemory.updated_at.desc()).limit(limit)
        rows = self.session.execute(stmt).scalars().all()
        return [
            {
                "memory_type": r.memory_type,
                "key": r.key,
                "value": json.loads(r.value_json),
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]

    def save_analysis(
        self,
        *,
        analysis_type: str,
        subject_type: str,
        subject_id: str,
        summary: str,
        result: dict[str, Any],
        confidence: float,
        provider: str,
        model: str,
        ticker: str | None = None,
        event_id: int | None = None,
    ) -> int:
        row = Analysis(
            analysis_type=analysis_type,
            subject_type=subject_type,
            subject_id=subject_id,
            ticker=ticker,
            summary=summary,
            result_json=json.dumps(result),
            confidence=confidence,
            model_provider=provider,
            model_name=model,
            event_id=event_id,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def get_ticker_summary(self, ticker: str) -> Optional[str]:
        data = self.get("ticker_summary", ticker.upper())
        return data.get("summary") if data else None
