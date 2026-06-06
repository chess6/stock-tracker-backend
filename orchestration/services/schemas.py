from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, validator


class EventType(str, Enum):
    NEWS_INGESTED = "news_ingested"
    ANALYSIS_COMPLETED = "analysis_completed"
    HIGH_PRIORITY_SIGNAL = "high_priority_signal"
    WATCHLIST_CANDIDATE = "watchlist_candidate"
    FETCH_FAILED = "fetch_failed"
    REPAIR_REQUIRED = "repair_required"
    PORTFOLIO_CHECK = "portfolio_check"


EVENT_PRIORITIES: dict[EventType, int] = {
    EventType.FETCH_FAILED: 20,
    EventType.REPAIR_REQUIRED: 15,
    EventType.HIGH_PRIORITY_SIGNAL: 10,
    EventType.NEWS_INGESTED: 50,
    EventType.ANALYSIS_COMPLETED: 40,
    EventType.WATCHLIST_CANDIDATE: 30,
    EventType.PORTFOLIO_CHECK: 35,
}


class ProposedAction(BaseModel):
    action_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    requires_approval: bool = False


class AgentOutput(BaseModel):
    """All agents must return this shape (JSON only)."""

    agent: str
    version: str = "1.0"
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    findings: list[dict[str, Any]] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    follow_up_events: list[dict[str, Any]] = Field(default_factory=list)
    memory_updates: list[dict[str, Any]] = Field(default_factory=list)

    @validator("confidence")
    def round_confidence(cls, v: float) -> float:
        return round(v, 4)

    class Config:
        extra = "ignore"


class NewsAnalysisFinding(BaseModel):
    article_id: int
    ticker: Optional[str] = None
    sentiment: Literal["positive", "negative", "neutral"] = "neutral"
    impact_score: float = Field(ge=0.0, le=1.0)
    key_points: list[str] = Field(default_factory=list)


class SignalRankingFinding(BaseModel):
    ticker: str
    signal_type: str
    score: float = Field(ge=0.0, le=1.0)
    drivers: list[str] = Field(default_factory=list)


class WatchlistProposal(BaseModel):
    ticker: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class PortfolioAlert(BaseModel):
    ticker: str
    alert_type: str
    severity: Literal["low", "medium", "high"] = "medium"
    message: str


class RepairStrategy(BaseModel):
    failure_type: str
    root_cause: str
    proposed_fix: str
    retry_job_type: Optional[str] = None
    retry_payload: dict[str, Any] = Field(default_factory=dict)
    safe_to_auto_retry: bool = False


class OrchestratorEventPayload(BaseModel):
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: Optional[int] = None
    correlation_id: Optional[str] = None


class DashboardStats(BaseModel):
    pending_events: int
    failed_events: int
    active_agents: int
    pending_approvals: int
    recent_decisions: list[dict[str, Any]]
    agent_runs: list[dict[str, Any]]
