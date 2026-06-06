from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from ..memory.store import MemoryStore
from ..services.ai_provider import AIProvider, get_ai_provider
from ..services.schemas import AgentOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a stock tracker analysis agent.
You MUST respond with a single JSON object matching this schema:
{
  "agent": "<agent_name>",
  "confidence": 0.0-1.0,
  "summary": "brief summary",
  "findings": [],
  "proposed_actions": [{"action_type": "...", "params": {}, "rationale": "...", "requires_approval": false}],
  "follow_up_events": [{"event_type": "...", "payload": {}}],
  "memory_updates": [{"memory_type": "...", "key": "...", "value": {}, "ticker": "..."}]
}
You may ONLY propose actions. Never claim you executed anything.
Allowed action_type values: enqueue_job, add_watchlist_item, emit_event, store_memory, no_op.
"""


class BaseAgent(ABC):
    name: str = "base"
    version: str = "1.0"

    def __init__(
        self,
        memory: MemoryStore,
        ai: AIProvider | None = None,
    ) -> None:
        self.memory = memory
        self.ai = ai or get_ai_provider()

    @abstractmethod
    def build_user_prompt(self, event: dict[str, Any]) -> str:
        ...

    def run(self, event: dict[str, Any]) -> AgentOutput:
        user_prompt = self.build_user_prompt(event)
        raw = self.ai.complete_json(
            system=SYSTEM_PROMPT.replace("<agent_name>", self.name),
            user=user_prompt,
        )
        raw["agent"] = self.name
        try:
            output = AgentOutput.parse_obj(raw)
        except PydanticValidationError as exc:
            logger.error("Agent %s returned invalid JSON: %s", self.name, exc)
            output = AgentOutput(
                agent=self.name,
                confidence=0.0,
                summary=f"Validation failed: {exc}",
                proposed_actions=[],
            )
        return self.post_process(output, event)

    def post_process(self, output: AgentOutput, event: dict[str, Any]) -> AgentOutput:
        return output

    def deterministic_fallback(self, event: dict[str, Any]) -> AgentOutput:
        """Override for rule-based analysis when AI unavailable."""
        return AgentOutput(
            agent=self.name,
            confidence=0.6,
            summary=f"{self.name} deterministic analysis",
            findings=[],
        )
