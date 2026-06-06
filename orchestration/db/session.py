from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from .models import Base


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve()}"


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine(db_path: Path | None = None) -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        path = db_path or get_settings().resolved_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(_sqlite_url(path), connect_args={"check_same_thread": False})
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def init_db(db_path: Path | None = None) -> None:
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(db_path: Path | None = None) -> Generator[Session, None, None]:
    get_engine(db_path)
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()
