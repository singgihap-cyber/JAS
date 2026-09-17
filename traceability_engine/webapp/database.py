"""Session/engine wiring for the web layer.

Kept deliberately tiny: the engine's models already run identically on
SQLite and PostgreSQL (models.py docstring, PROJECT_STATUS.md stack
decision), so the only thing this module owns is *which* database URL a
given process talks to, and handing FastAPI a per-request Session via
`get_db()`.

`DATABASE_URL` follows the standard SQLAlchemy URL format
(`postgresql+psycopg2://user:pass@host/dbname` in production). Defaults to
a local SQLite file so the app runs out of the box for manual testing /
demos without a PostgreSQL server -- this default is NOT the production
configuration (13_STOCK.md / DATABASE_DESIGN.md assume PostgreSQL); ops
must set DATABASE_URL before deploying for real use.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ..models import Base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./jas_traceability.db")

_is_sqlite = DATABASE_URL.startswith("sqlite")
_is_memory = _is_sqlite and ":memory:" in DATABASE_URL
_engine_kwargs: dict = {}
if _is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
if _is_memory:
    # A bare sqlite:///:memory: engine hands out a brand-new (empty) DB per
    # connection under the default pool -- StaticPool pins it to a single
    # connection so init_db()'s create_all() and every request's session
    # see the same database. Only relevant for :memory: -- the default
    # (file-based) DATABASE_URL doesn't need this.
    _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Create tables if they don't exist yet.

    Deliberately `create_all`, not a migration tool -- no Alembic (or
    equivalent) has been set up in this project yet. Fine for the current
    scaffolding stage; a real migration tool is a gap worth flagging before
    this schema needs its first change against a live production database
    (noted in PROJECT_STATUS.md).
    """
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency: one Session per request.

    Commits on a clean return (persisting whatever the route's
    `services.*` call flushed), rolls back on any exception (domain error
    or otherwise) so a half-applied event/ledger write is never left
    sitting in the session, and always closes.
    """
    db: Session = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
