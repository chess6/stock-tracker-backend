from __future__ import annotations

import json
from typing import Any

from ..services.schemas import AgentOutput, EventType
from .base import BaseAgent


class SignalRankingAgent(BaseAgent):
    name = "SignalRankingAgent"

    def build_user_prompt(self, event: dict[str, Any]) -> str:
        payload = event.get("payload", {})
        tickers = payload.get("tickers", [])
        prior = []
        for t in tickers[:5]:
            prior.append(self.memory.get_ticker_summary(t))
        return (
            f"Rank trading signals from completed analysis.\n"
            f"Payload: {json.dumps(payload)}\n"
            f"Prior summaries: {json.dumps(prior)}\n"
            "Score each ticker 0-1. Emit high_priority_signal for scores >= 0.8."
        )

    def post_process(self, output: AgentOutput, event: dict[str, Any]) -> AgentOutput:
        for finding in output.findings:
            score = finding.get("score", 0)
            ticker = finding.get("ticker")
            if score >= 0.8 and ticker:
                output.follow_up_events.append({
                    "event_type": EventType.HIGH_PRIORITY_SIGNAL.value,
                    "payload": {"ticker": ticker, "score": score, "drivers": finding.get("drivers", [])},
                })
        return output
