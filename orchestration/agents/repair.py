from __future__ import annotations

import json
import traceback
from typing import Any

from ..db.models import RepairLog
from ..services.schemas import AgentOutput, EventType, ProposedAction, RepairStrategy
from .base import BaseAgent

JOB_TYPE_MAP = {
    "fundamentals": "refresh_fundamentals",
    "prices": "refresh_prices",
    "news": "ingest_default_feeds",
    "insiders": "refresh_insiders",
    "companies": "sync_companies",
}


class RepairAgent(BaseAgent):
    name = "RepairAgent"

    def build_user_prompt(self, event: dict[str, Any]) -> str:
        payload = event.get("payload", {})
        return (
            f"A fetch/parsing job failed. Diagnose and propose a safe retry strategy.\n"
            f"Failure context: {json.dumps(payload)}\n"
            "Return findings with failure_type, root_cause, proposed_fix. "
            "Use proposed_actions enqueue_job only if safe_to_auto_retry is true in your reasoning."
        )

    def run(self, event: dict[str, Any]) -> AgentOutput:
        from ..services.ai_provider import DeterministicProvider

        if isinstance(self.ai, DeterministicProvider):
            return self.deterministic_fallback(event)
        try:
            return super().run(event)
        except Exception:
            return self.deterministic_fallback(event)

    def deterministic_fallback(self, event: dict[str, Any]) -> AgentOutput:
        payload = event.get("payload", {})
        failure_type = payload.get("failure_type", "unknown")
        source = payload.get("source", "unknown")
        stack = payload.get("stack_trace", "")
        job_hint = payload.get("job_type") or JOB_TYPE_MAP.get(failure_type, "refresh_fundamentals")
        tickers = payload.get("tickers", [])

        strategy = RepairStrategy(
            failure_type=failure_type,
            root_cause=payload.get("error", "Unknown error")[:500],
            proposed_fix=f"Retry {job_hint} with backoff; verify SEC_USER_AGENT and network.",
            retry_job_type=job_hint,
            retry_payload={"tickers": tickers} if tickers else {},
            safe_to_auto_retry=event.get("attempt_count", 1) <= 2,
        )

        self.memory.session.add(
            RepairLog(
                failure_type=failure_type,
                source=source,
                stack_trace=stack[:8000] if stack else traceback.format_exc()[:8000],
                context_json=json.dumps(payload),
                strategy_json=strategy.json(),
                status="proposed",
                event_id=event.get("id"),
            )
        )

        actions = []
        if strategy.safe_to_auto_retry and strategy.retry_job_type:
            actions.append(ProposedAction(
                action_type="enqueue_job",
                params={"job_type": strategy.retry_job_type, **strategy.retry_payload},
                rationale=strategy.proposed_fix,
                requires_approval=False,
            ))
        else:
            actions.append(ProposedAction(
                action_type="enqueue_job",
                params={"job_type": strategy.retry_job_type, **strategy.retry_payload},
                rationale=strategy.proposed_fix,
                requires_approval=True,
            ))

        return AgentOutput(
            agent=self.name,
            confidence=0.75 if strategy.safe_to_auto_retry else 0.5,
            summary=strategy.proposed_fix,
            findings=[strategy.dict()],
            proposed_actions=actions,
            follow_up_events=[{
                "event_type": EventType.REPAIR_REQUIRED.value,
                "payload": {"strategy": strategy.dict(), "source": source},
            }] if not strategy.safe_to_auto_retry else [],
        )
