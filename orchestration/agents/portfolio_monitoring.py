from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from ..db.models import PortfolioPosition
from ..services.schemas import AgentOutput
from .base import BaseAgent


class PortfolioMonitoringAgent(BaseAgent):
    name = "PortfolioMonitoringAgent"

    def build_user_prompt(self, event: dict[str, Any]) -> str:
        payload = event.get("payload", {})
        positions = self.session_positions()
        return (
            f"Monitor portfolio positions for risks and opportunities.\n"
            f"Event: {json.dumps(payload)}\n"
            f"Positions: {json.dumps(positions)}\n"
            "Flag alerts for price moves, news, insider activity. "
            "Use requires_approval for any enqueue_job actions."
        )

    def session_positions(self) -> list[dict[str, Any]]:
        rows = self.memory.session.execute(select(PortfolioPosition).where(PortfolioPosition.is_active == True)).scalars().all()  # noqa: E712
        return [{"ticker": r.ticker, "notes": r.notes} for r in rows]

    def post_process(self, output: AgentOutput, event: dict[str, Any]) -> AgentOutput:
        ticker = event.get("payload", {}).get("ticker")
        if ticker:
            self.memory.put(
                "portfolio_alert",
                f"{ticker}:{event.get('id')}",
                {"summary": output.summary, "confidence": output.confidence},
                ticker=ticker,
            )
        return output
