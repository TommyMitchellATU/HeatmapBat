from __future__ import annotations

import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

"""Database session utilities for the ETI subsystem.

This module owns the SQLAlchemy engine and a `SessionLocal` factory that are
used by both the FastAPI app (via `get_db`) and standalone ETL/CLI tools.

The connection URL is taken from the ``DATABASE_URL`` environment variable,
falling back to the Postgres DSN provided by ``docker-compose.yml``. This
keeps local dev, CI, and Docker all using the same configuration surface.
"""

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://app:app@db:5432/app",
)

# Single process‑wide engine reused by all sessions.
engine = create_engine(DATABASE_URL, future=True)

# Factory that creates scoped Session objects bound to the shared engine.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    """Yield a DB session and ensure it is closed afterwards.

    This is written in the style FastAPI expects for `Depends(get_db)`, but the
    same generator is also handy in scripts/tests that want a short‑lived
    session with correct teardown semantics.
    """

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
