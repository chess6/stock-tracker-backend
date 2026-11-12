from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import get_settings
from .db.session import init_db, session_scope
from .orchestrator.executor import ActionExecutor
from .orchestrator.loop import OrchestratorLoop
from .queues.event_bus import EventBus
from .services.schemas import DashboardStats, EventType

app = FastAPI(title="Stock Tracker AI Orchestrator", version="0.1.0")

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_DEFAULT_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001")
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _env_list(name: str) -> set[str]:
    return {item.strip().rstrip("/").lower() for item in os.getenv(name, "").split(",") if item.strip()}


@app.middleware("http")
async def _reject_cross_site(request: Request, call_next):
    """This API has no auth and binds to loopback, so a web page in the operator's browser
    is the main threat: block cross-site writes (e.g. approving agent actions) and
    DNS-rebinding hosts."""
    host = (urlsplit(f"//{request.headers.get('host', '')}").hostname or "").lower()
    if host not in _LOCAL_HOSTS and host not in _env_list("ORCHESTRATOR_ALLOWED_HOSTS"):
        return JSONResponse({"detail": "Host not allowed"}, status_code=403)
    origin = (request.headers.get("origin") or "").rstrip("/").lower()
    if request.method in _MUTATING_METHODS and origin:
        allowed = _env_list("ORCHESTRATOR_CORS_ORIGINS") or set(_DEFAULT_ORIGINS)
        if origin not in allowed:
            return JSONResponse({"detail": "Cross-site request rejected"}, status_code=403)
    return await call_next(request)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/dashboard", response_model=DashboardStats)
def dashboard() -> dict[str, Any]:
    stats = OrchestratorLoop.dashboard_stats()
    return stats


@app.get("/events")
def list_events(status: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from .db.models import OrchestratorEvent

    with session_scope() as session:
        rows = session.execute(
            select(OrchestratorEvent)
            .where(OrchestratorEvent.status == status)
            .order_by(OrchestratorEvent.priority, OrchestratorEvent.id)
            .limit(limit)
        ).scalars().all()
        return [
            {
                "id": r.id,
                "event_type": r.event_type,
                "priority": r.priority,
                "status": r.status,
                "attempt_count": r.attempt_count,
                "payload": json.loads(r.payload_json or "{}"),
                "last_error": r.last_error,
            }
            for r in rows
        ]


class PublishEventRequest(BaseModel):
    event_type: str
    payload: dict[str, Any] = {}
    priority: int | None = None


@app.post("/events")
def publish_event(body: PublishEventRequest) -> dict[str, int]:
    with session_scope() as session:
        bus = EventBus(session)
        event_id = bus.publish(EventType(body.event_type), body.payload, priority=body.priority)
    return {"event_id": event_id}


@app.get("/approvals")
def list_approvals(status: str = "pending") -> list[dict[str, Any]]:
    from sqlalchemy import select
    from .db.models import ApprovalRequest

    with session_scope() as session:
        rows = session.execute(
            select(ApprovalRequest).where(ApprovalRequest.status == status).limit(50)
        ).scalars().all()
        return [
            {
                "id": r.id,
                "decision_id": r.decision_id,
                "action": json.loads(r.action_json),
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


@app.post("/approvals/{approval_id}/approve")
def approve(approval_id: int, reviewer: str = "human") -> dict[str, Any]:
    with session_scope() as session:
        bus = EventBus(session)
        executor = ActionExecutor(session, bus)
        try:
            return executor.approve(approval_id, reviewer=reviewer)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))


@app.post("/approvals/{approval_id}/reject")
def reject(approval_id: int, reviewer: str = "human") -> dict[str, str]:
    with session_scope() as session:
        bus = EventBus(session)
        executor = ActionExecutor(session, bus)
        executor.reject(approval_id, reviewer=reviewer)
    return {"status": "rejected"}


@app.get("/memory/{ticker}")
def ticker_memory(ticker: str, memory_type: str | None = None) -> list[dict[str, Any]]:
    from .memory.store import MemoryStore

    with session_scope() as session:
        store = MemoryStore(session)
        return store.list_by_ticker(ticker, memory_type=memory_type)


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "orchestration.api:app",
        host=settings.orchestrator_api_host,
        port=settings.orchestrator_api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
