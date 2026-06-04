"""run_document_tidy must not crash when a duplicate has NULL timestamps.

The duplicate-keeper sort used key=(real_len, updated_at or created_at). When
two duplicates tie on real length and one has both timestamps NULL, Python
compared None against a datetime and raised TypeError, aborting the entire
tidy run. The sort key is now total-order safe.
"""
import asyncio
import tempfile
import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import Document


@pytest.fixture
def db_factory(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    engine = create_engine(f"sqlite:///{tmp.name}", connect_args={"check_same_thread": False}, poolclass=NullPool)
    cdb.Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(cdb, "SessionLocal", TS)
    return TS


def test_tidy_survives_duplicate_with_null_timestamps(db_factory):
    content = "This is a real document body long enough to survive junk rules."
    db = db_factory()
    try:
        # Same title + content => same dedup group, equal real length.
        db.add(Document(id=str(uuid.uuid4()), owner="alice", title="My Report",
                        current_content=content, updated_at=None, created_at=None))
        db.add(Document(id=str(uuid.uuid4()), owner="alice", title="My Report",
                        current_content=content,
                        updated_at=datetime(2026, 6, 1, 9, 0), created_at=datetime(2026, 6, 1, 9, 0)))
        db.commit()
    finally:
        db.close()

    # Old code raised TypeError (None vs datetime) and aborted.
    result = asyncio.run(run_tidy())
    assert isinstance(result, str)

    db = db_factory()
    try:
        remaining = db.query(Document).filter(Document.owner == "alice").count()
        assert remaining == 1  # one duplicate kept, the other removed
    finally:
        db.close()


async def run_tidy():
    from src.document_actions import run_document_tidy
    return await run_document_tidy("alice")
