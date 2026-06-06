from __future__ import annotations

import json
from typing import Any

from ..services.schemas import AgentOutput, EventType
from .base import BaseAgent


class NewsAnalysisAgent(BaseAgent):
    name = "NewsAnalysisAgent"

    def build_user_prompt(self, event: dict[str, Any]) -> str:
        payload = event.get("payload", {})
        return (
            f"Analyze newly ingested news.\n"
            f"Event payload: {json.dumps(payload)}\n"
            "Identify tickers, sentiment, materiality. "
            "If high impact, add follow_up_events with event_type analysis_completed or high_priority_signal."
        )

    def post_process(self, output: AgentOutput, event: dict[str, Any]) -> AgentOutput:
        payload = event.get("payload", {})
        article_id = payload.get("article_id")
        tickers = payload.get("tickers", [])
        if not output.findings and article_id:
            output.findings.append({
                "article_id": article_id,
                "tickers": tickers,
                "sentiment": payload.get("sentiment_label", "neutral"),
            })
        if output.confidence >= 0.7 and tickers:
            output.follow_up_events.append({
                "event_type": EventType.ANALYSIS_COMPLETED.value,
                "payload": {"article_id": article_id, "tickers": tickers},
            })
        return output
