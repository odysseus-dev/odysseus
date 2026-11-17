"""Disposable databases for tests that exercise the real ORM and session manager."""

from contextlib import contextmanager
from tempfile import TemporaryDirectory

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


@contextmanager
def disposable_database(tmp_path):
    """Own the database file and engine; keep the canonical ORM classes intact."""
    import core.database as database

    with TemporaryDirectory(prefix="database-", dir=tmp_path) as directory:
        engine = create_engine(
            f"sqlite:///{directory}/test.db",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )
        try:
            database.Base.metadata.create_all(engine)
            yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
        finally:
            engine.dispose()


@contextmanager
def isolated_session_database(tmp_path):
    """Temporarily bind the real manager and database aliases without reloading.

    Reloading core.database changes its ORM classes while existing imports keep
    the old classes and factories. Patch only resource bindings instead, and
    undo them before disposing the owned engine and removing its files.
    """
    import core.database as database
    import core.session_manager as manager
    import src.database as compatibility_database

    with disposable_database(tmp_path) as factory:
        engine = factory.kw["bind"]
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setenv("DATABASE_URL", str(engine.url))
            for module in (database, compatibility_database):
                patcher.setattr(module, "DATABASE_URL", str(engine.url))
                patcher.setattr(module, "engine", engine)
                patcher.setattr(module, "SessionLocal", factory)
            patcher.setattr(manager, "SessionLocal", factory)
            yield manager.SessionManager(), database
