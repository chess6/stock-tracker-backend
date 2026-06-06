import os
import tempfile
from pathlib import Path

from orchestration import config
from orchestration.db import session as db_session
from orchestration.db.session import init_db, session_scope
from orchestration.orchestrator.loop import OrchestratorLoop
from orchestration.queues.event_bus import EventBus
from orchestration.services.schemas import EventType


def test_process_once_completes_event(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.sqlite3"
        monkeypatch.setenv("STOCK_TRACKER_DB_PATH", str(db))
        config.get_settings.cache_clear()
        db_session._engine = None
        db_session._SessionLocal = None
        init_db(db)

        with session_scope(db) as session:
            bus = EventBus(session)
            bus.publish(EventType.FETCH_FAILED, {
                "failure_type": "prices",
                "source": "stooq",
                "error": "connection reset",
                "tickers": ["AAPL"],
            })

        loop = OrchestratorLoop()
        processed = loop._process_once_sync()
        assert processed is True

        stats = OrchestratorLoop.dashboard_stats()
        assert stats["failed_events"] >= 0
