"""Orchestrator worker entrypoint: python -m orchestration.worker"""

from __future__ import annotations

import asyncio

from .db.session import init_db
from .orchestrator.loop import OrchestratorLoop


def main() -> None:
    init_db()
    loop = OrchestratorLoop()
    asyncio.run(loop.run_forever())


if __name__ == "__main__":
    main()
