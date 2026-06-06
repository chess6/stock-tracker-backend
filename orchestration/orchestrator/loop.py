from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from ..config import get_settings
from ..db.models import AgentDecision, AgentRun, ApprovalRequest, OrchestratorEvent
from ..db.session import session_scope
from ..memory.store import MemoryStore
from ..middleware.logging import configure_logging
from ..queues.event_bus import EventBus
from ..queues.redis_queue import RedisEventQueue
from ..services.schemas import AgentOutput
from .dispatcher import AgentDispatcher
from .executor import ActionExecutor

logger = logging.getLogger(__name__)


class OrchestratorLoop:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.redis = RedisEventQueue()

    async def run_forever(self) -> None:
        configure_logging()
        logger.info("Orchestrator loop started")
        while True:
            processed = await self.process_once()
            if not processed:
                await asyncio.sleep(self.settings.poll_interval_seconds)

    async def process_once(self) -> bool:
        return await asyncio.to_thread(self._process_once_sync)

    def _process_once_sync(self) -> bool:
        with session_scope() as session:
            bus = EventBus(session)
            # Optional Redis fan-in
            if self.redis.available:
                item = self.redis.pop()
                if item:
                    bus.publish(item["event_type"], item.get("payload", {}), priority=item.get("priority"))

            event = bus.claim_next()
            if not event:
                return False

            memory = MemoryStore(session)
            dispatcher = AgentDispatcher(memory)
            executor = ActionExecutor(session, bus)

            run = AgentRun(agent_name=event["event_type"], event_id=event["id"], status="running")
            session.add(run)
            session.flush()

            try:
                output: AgentOutput = dispatcher.dispatch(event)
                self._persist_output(session, memory, output, event["id"])

                result = executor.process_output(output, event["id"])
                bus.complete(event["id"])

                run.status = "completed"
                run.finished_at = datetime.now(timezone.utc)
                logger.info(
                    "Event %s completed by %s (confidence=%.2f, executed=%s)",
                    event["id"],
                    output.agent,
                    output.confidence,
                    result.get("executed"),
                    extra={"event_id": event["id"], "agent": output.agent, "confidence": output.confidence},
                )
            except Exception as exc:
                logger.exception("Event %s failed", event["id"])
                bus.fail(event["id"], str(exc))
                run.status = "failed"
                run.error_message = str(exc)[:2000]
                run.retry_count = event.get("attempt_count", 0)
                run.finished_at = datetime.now(timezone.utc)

            return True

    def _persist_output(self, session, memory: MemoryStore, output: AgentOutput, event_id: int) -> None:
        decision = AgentDecision(
            agent_name=output.agent,
            event_id=event_id,
            decision_json=output.json(),
            confidence=output.confidence,
            status="completed",
            requires_approval=any(a.requires_approval for a in output.proposed_actions),
        )
        session.add(decision)

        memory.save_analysis(
            analysis_type=output.agent,
            subject_type="event",
            subject_id=str(event_id),
            summary=output.summary,
            result=output.dict(),
            confidence=output.confidence,
            provider=self.settings.ai_default_provider,
            model=getattr(self.settings, f"{self.settings.ai_default_provider}_model", "default"),
            event_id=event_id,
        )

        for mem in output.memory_updates:
            memory.put(
                mem.get("memory_type", "general"),
                mem.get("key", f"event:{event_id}"),
                mem.get("value", {}),
                ticker=mem.get("ticker"),
            )

    @staticmethod
    def dashboard_stats() -> dict[str, Any]:
        with session_scope() as session:
            pending = session.execute(
                select(func.count()).select_from(OrchestratorEvent).where(OrchestratorEvent.status == "pending")
            ).scalar_one()
            failed = session.execute(
                select(func.count()).select_from(OrchestratorEvent).where(OrchestratorEvent.status == "failed")
            ).scalar_one()
            active = session.execute(
                select(func.count()).select_from(AgentRun).where(AgentRun.status == "running")
            ).scalar_one()
            approvals = session.execute(
                select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.status == "pending")
            ).scalar_one()
            decisions = session.execute(
                select(AgentDecision).order_by(AgentDecision.created_at.desc()).limit(10)
            ).scalars().all()
            runs = session.execute(
                select(AgentRun).order_by(AgentRun.started_at.desc()).limit(10)
            ).scalars().all()
            return {
                "pending_events": pending,
                "failed_events": failed,
                "active_agents": active,
                "pending_approvals": approvals,
                "recent_decisions": [
                    {
                        "id": d.id,
                        "agent": d.agent_name,
                        "confidence": d.confidence,
                        "status": d.status,
                        "created_at": d.created_at.isoformat() if d.created_at else None,
                    }
                    for d in decisions
                ],
                "agent_runs": [
                    {
                        "id": r.id,
                        "agent": r.agent_name,
                        "status": r.status,
                        "retry_count": r.retry_count,
                        "error": r.error_message,
                    }
                    for r in runs
                ],
            }
