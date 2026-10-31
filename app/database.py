"""Database engine, session factory, and the declarative base.

The only difference between dev and prod is the URL. SQLite gets WAL mode and a
long busy timeout because a control plane issues concurrent writes and the
default 5s timeout produces spurious "database is locked" errors under load.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
        )

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine

    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables. Alembic takes over once the schema starts migrating."""
    from . import models  # noqa: F401, PLC0415  (import registers the mappers)

    Base.metadata.create_all(bind=engine)
