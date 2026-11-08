"""Database engine, sessions and append-only enforcement.

Other roles never create engines themselves: they use `session_scope()` (scripts, agents)
or the FastAPI dependency `get_db()` (routes).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from msa.core.config import get_settings


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite stores datetimes without a zone)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Tables whose rows can never be changed or removed (G6). Corrections are new rows.
APPEND_ONLY_TABLES = ["log_entries", "inventory_txns", "audit_events"]
# Reports: the facts of a report are frozen; only workflow fields (status, cluster) may change.
REPORT_FROZEN_COLUMNS = ["kind", "text", "equipment_id", "user_id", "created_at", "created_by"]

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def configure(url: str | None = None) -> Engine:
    """Create (or replace) the global engine. Tests pass "sqlite://" for an in-memory db."""
    global _engine, _SessionLocal
    url = url or get_settings().db_url
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        else:
            path = url.replace("sqlite:///", "", 1)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):
        event.listen(_engine, "connect", _sqlite_pragmas)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def engine() -> Engine:
    return _engine or configure()


def SessionLocal() -> Session:  # noqa: N802 - reads like a class at call sites
    if _SessionLocal is None:
        configure()
    assert _SessionLocal is not None
    return _SessionLocal()


def init_db(drop: bool = False) -> None:
    """Create all tables and the append-only triggers."""
    from msa.core import models  # noqa: F401  (register tables)
    from msa.core.plugins import load_plugins

    load_plugins()  # plugins may add their own tables
    eng = engine()
    if drop:
        Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    if eng.dialect.name == "sqlite":
        _create_sqlite_triggers(eng)


def _create_sqlite_triggers(eng: Engine) -> None:
    with eng.begin() as conn:
        for table in APPEND_ONLY_TABLES:
            for op in ("UPDATE", "DELETE"):
                conn.execute(
                    text(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} "
                        f"BEFORE {op} ON {table} BEGIN "
                        f"SELECT RAISE(ABORT, '{table} is append-only'); END;"
                    )
                )
        cols = ", ".join(REPORT_FROZEN_COLUMNS)
        conn.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS reports_frozen_update "
                f"BEFORE UPDATE OF {cols} ON reports BEGIN "
                "SELECT RAISE(ABORT, 'report facts are append-only'); END;"
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS reports_no_delete BEFORE DELETE ON reports "
                "BEGIN SELECT RAISE(ABORT, 'reports are append-only'); END;"
            )
        )


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
