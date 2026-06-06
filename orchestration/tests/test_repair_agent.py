import tempfile
from pathlib import Path

from orchestration.agents.repair import RepairAgent
from orchestration.db.session import init_db, session_scope
from orchestration.memory.store import MemoryStore
from orchestration.services.ai_provider import DeterministicProvider


def test_repair_agent_deterministic():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.sqlite3"
        init_db(db)
        with session_scope(db) as session:
            memory = MemoryStore(session)
            agent = RepairAgent(memory=memory, ai=DeterministicProvider())
            output = agent.run({
                "id": 1,
                "event_type": "fetch_failed",
                "payload": {
                    "failure_type": "fundamentals",
                    "source": "sec",
                    "error": "timeout",
                    "tickers": ["JPM"],
                    "job_type": "refresh_fundamentals",
                },
                "attempt_count": 1,
            })
            assert output.agent == "RepairAgent"
            assert output.confidence > 0
            assert len(output.proposed_actions) >= 1
