from __future__ import annotations

import json
from typing import Any

from ..services.schemas import AgentOutput, ProposedAction
from .base import BaseAgent


class WatchlistExpansionAgent(BaseAgent):
    name = "WatchlistExpansionAgent"

    def build_user_prompt(self, event: dict[str, Any]) -> str:
        payload = event.get("payload", {})
        return (
            f"Evaluate watchlist expansion candidates.\n"
            f"Payload: {json.dumps(payload)}\n"
            "Propose add_watchlist_item actions for tickers with strong fundamentals/news signals. "
            "Set requires_approval=true for new tickers."
        )

    def post_process(self, output: AgentOutput, event: dict[str, Any]) -> AgentOutput:
        for finding in output.findings:
            ticker = finding.get("ticker")
            if ticker and not any(
                a.action_type == "add_watchlist_item" and a.params.get("ticker") == ticker
                for a in output.proposed_actions
            ):
                output.proposed_actions.append(ProposedAction(
                    action_type="add_watchlist_item",
                    params={"ticker": ticker, "watchlist": "agent_candidates", "reason": finding.get("reason", "")},
                    rationale=finding.get("reason", "Agent candidate"),
                    requires_approval=True,
                ))
        return output
