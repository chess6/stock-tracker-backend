from __future__ import annotations

import re
from typing import Any

from .schemas import ProposedAction

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
ALLOWED_JOB_TYPES = {
    "sync_companies",
    "refresh_fundamentals",
    "ingest_default_feeds",
    "refresh_prices",
    "refresh_insiders",
    "bootstrap",
}
ALLOWED_ACTION_TYPES = {
    "enqueue_job",
    "add_watchlist_item",
    "emit_event",
    "store_memory",
    "no_op",
}


class ValidationError(Exception):
    pass


def validate_ticker(ticker: str) -> str:
    t = ticker.strip().upper()
    if not TICKER_RE.match(t):
        raise ValidationError(f"Invalid ticker: {ticker}")
    return t


def validate_action(action: ProposedAction) -> ProposedAction:
    if action.action_type not in ALLOWED_ACTION_TYPES:
        raise ValidationError(f"Disallowed action_type: {action.action_type}")

    params = action.params or {}

    if action.action_type == "enqueue_job":
        job_type = params.get("job_type")
        if job_type not in ALLOWED_JOB_TYPES:
            raise ValidationError(f"Disallowed job_type: {job_type}")
        tickers = params.get("tickers", [])
        if tickers:
            params["tickers"] = [validate_ticker(t) for t in tickers]

    if action.action_type == "add_watchlist_item":
        validate_ticker(str(params.get("ticker", "")))

    if action.action_type == "emit_event":
        event_type = params.get("event_type")
        if not event_type or not isinstance(event_type, str):
            raise ValidationError("emit_event requires event_type string")

    return action


def validate_agent_output_actions(actions: list[ProposedAction]) -> list[ProposedAction]:
    return [validate_action(a) for a in actions]


def should_auto_execute(confidence: float, action: ProposedAction, min_confidence: float) -> bool:
    if action.requires_approval:
        return False
    if action.action_type == "no_op":
        return True
    return confidence >= min_confidence
