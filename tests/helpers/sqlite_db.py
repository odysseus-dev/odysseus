"""Construct a file-backed temp sqlite DB for tests.

Only builds the SQLAlchemy objects from the repeated temp-sqlite block. It
does not patch modules, manage cleanup, or own any global state — the caller
keeps the returned objects alive and binds ``SessionLocal`` where needed.
"""
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


def make_temp_sqlite(metadata):
    """Build a file-backed temp sqlite database and create its tables.

    Returns ``(SessionLocal, engine, tmpfile)``. The caller must keep these
    references alive (temp file and engine GC are the caller's concern) and
    bind ``SessionLocal`` onto whatever module the code under test reads.
    """
    tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    engine = create_engine(
        f"sqlite:///{tmpfile.name}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return SessionLocal, engine, tmpfile
