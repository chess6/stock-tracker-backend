from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import create_app
from app.config import Config


@pytest.fixture()
def app(tmp_path: Path):
    config = Config(
        database_path=str(tmp_path / "test.sqlite3"),
        nasdaq_api_key=None,
        sec_user_agent="TestApp test@example.com",
    )
    app = create_app(config)
    app.config.update(TESTING=True)
    return app


@pytest.fixture()
def client(app):
    return app.test_client()
