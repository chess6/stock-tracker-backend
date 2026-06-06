import tempfile
from pathlib import Path

from orchestration.db.session import init_db, session_scope
from orchestration.queues.event_bus import EventBus
from orchestration.services.schemas import EventType


def test_publish_claim_complete():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.sqlite3"
        init_db(db)
        with session_scope(db) as session:
            bus = EventBus(session)
            eid = bus.publish(EventType.NEWS_INGESTED, {"article_id": 1})
            assert eid > 0

        with session_scope(db) as session:
            bus = EventBus(session)
            event = bus.claim_next()
            assert event is not None
            assert event["event_type"] == "news_ingested"
            bus.complete(event["id"])

        with session_scope(db) as session:
            bus = EventBus(session)
            assert bus.claim_next() is None
