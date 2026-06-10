from __future__ import annotations

from typing import Any

from ..agents import AGENT_REGISTRY, EVENT_AGENT_MAP, BaseAgent
from ..memory.store import MemoryStore
from ..services.ai_provider import get_ai_provider


class AgentDispatcher:
    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory
        self._agents: dict[str, BaseAgent] = {}

    def resolve(self, event_type: str) -> BaseAgent | None:
        if event_type == "analysis_completed":
            from app.services.feature_flags import is_enabled

            if not is_enabled("experimental_signal_ranking"):
                return None
        agent_name = EVENT_AGENT_MAP.get(event_type)
        if not agent_name:
            return None
        if agent_name not in self._agents:
            cls = AGENT_REGISTRY[agent_name]
            self._agents[agent_name] = cls(memory=self.memory, ai=get_ai_provider())
        return self._agents[agent_name]

    def dispatch(self, event: dict[str, Any]):
        agent = self.resolve(event["event_type"])
        if not agent:
            raise ValueError(f"No agent for event type: {event['event_type']}")
        return agent.run(event)
