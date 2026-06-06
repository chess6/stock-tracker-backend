from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import ApprovalRequest, Watchlist, WatchlistItem
from ..queues.event_bus import EventBus
from ..services.schemas import AgentOutput, EventType, ProposedAction
from ..services.validators import ValidationError, should_auto_execute, validate_action

logger = logging.getLogger(__name__)


class ActionExecutor:
    """Execute validated actions — bridges to existing ingestion_jobs queue."""

    def __init__(self, session: Session, event_bus: EventBus, db_path: Path | None = None) -> None:
        self.session = session
        self.event_bus = event_bus
        self.db_path = db_path or get_settings().resolved_db_path()

    def process_output(self, output: AgentOutput, event_id: int | None) -> dict[str, Any]:
        settings = get_settings()
        executed: list[str] = []
        pending_approval: list[str] = []
        errors: list[str] = []

        for raw_action in output.proposed_actions:
            try:
                action = validate_action(raw_action)
            except ValidationError as exc:
                errors.append(str(exc))
                continue

            if should_auto_execute(output.confidence, action, settings.min_confidence_auto_execute):
                try:
                    self._execute(action)
                    executed.append(action.action_type)
                except Exception as exc:
                    logger.exception("Action execution failed")
                    errors.append(str(exc))
            else:
                self._queue_approval(output, action, event_id)
                pending_approval.append(action.action_type)

        for mem in output.memory_updates:
            # memory persisted by orchestrator separately
            pass

        for follow in output.follow_up_events:
            et = follow.get("event_type")
            if et:
                self.event_bus.publish(EventType(et), follow.get("payload", {}))

        return {"executed": executed, "pending_approval": pending_approval, "errors": errors}

    def _execute(self, action: ProposedAction) -> None:
        if action.action_type == "no_op":
            return
        if action.action_type == "enqueue_job":
            self._enqueue_ingestion_job(action.params)
        elif action.action_type == "add_watchlist_item":
            self._add_watchlist_item(action.params)
        elif action.action_type == "emit_event":
            self.event_bus.publish(action.params["event_type"], action.params.get("payload", {}))
        elif action.action_type == "store_memory":
            pass  # handled by orchestrator memory layer

    def _enqueue_ingestion_job(self, params: dict[str, Any]) -> int:
        job_type = params["job_type"]
        payload = {k: v for k, v in params.items() if k != "job_type"}
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO ingestion_jobs (job_type, payload_json, status, priority)
                VALUES (?, ?, 'queued', ?)
                """,
                (job_type, json.dumps(payload), params.get("priority", 100)),
            )
            conn.commit()
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

    def _add_watchlist_item(self, params: dict[str, Any]) -> None:
        name = params.get("watchlist", "agent_candidates")
        ticker = str(params["ticker"]).upper()
        wl = self.session.execute(select(Watchlist).where(Watchlist.name == name)).scalar_one_or_none()
        if not wl:
            wl = Watchlist(name=name, description="Agent-expanded watchlist", source="agent")
            self.session.add(wl)
            self.session.flush()
        existing = self.session.execute(
            select(WatchlistItem).where(
                WatchlistItem.watchlist_id == wl.id,
                WatchlistItem.ticker == ticker,
            )
        ).scalar_one_or_none()
        if not existing:
            self.session.add(
                WatchlistItem(
                    watchlist_id=wl.id,
                    ticker=ticker,
                    reason=params.get("reason"),
                    confidence=float(params.get("confidence", 0)),
                    added_by_agent=params.get("agent", "orchestrator"),
                )
            )

    def _queue_approval(self, output: AgentOutput, action: ProposedAction, event_id: int | None) -> None:
        from ..db.models import AgentDecision

        decision = AgentDecision(
            agent_name=output.agent,
            event_id=event_id,
            decision_json=output.json(),
            confidence=output.confidence,
            status="pending_approval",
            requires_approval=True,
        )
        self.session.add(decision)
        self.session.flush()
        self.session.add(
            ApprovalRequest(
                decision_id=decision.id,
                action_json=action.json(),
                status="pending",
            )
        )

    def approve(self, approval_id: int, reviewer: str = "human") -> dict[str, Any]:
        approval = self.session.get(ApprovalRequest, approval_id)
        if not approval or approval.status != "pending":
            raise ValueError("Approval not found or already reviewed")
        action = ProposedAction.parse_raw(approval.action_json)
        validate_action(action)
        self._execute(action)
        approval.status = "approved"
        approval.reviewed_by = reviewer
        from datetime import datetime, timezone
        approval.reviewed_at = datetime.now(timezone.utc)
        return {"status": "approved", "action": action.action_type}

    def reject(self, approval_id: int, reviewer: str = "human") -> None:
        approval = self.session.get(ApprovalRequest, approval_id)
        if approval:
            approval.status = "rejected"
            approval.reviewed_by = reviewer
            from datetime import datetime, timezone
            approval.reviewed_at = datetime.now(timezone.utc)
