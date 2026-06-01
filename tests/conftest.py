"""Shared test configuration — ensure project root is on sys.path."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core import database as db


@pytest.fixture(scope="session")
def engine():
    test_engine = create_engine("sqlite:///:memory:")
    db.Base.metadata.create_all(bind=test_engine)
    yield test_engine
    db.Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


@pytest.fixture(autouse=True)
def _bind_core_db_to_test_engine(engine, monkeypatch):
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db, "engine", engine, raising=False)
    monkeypatch.setattr(db, "SessionLocal", TestSession, raising=False)
    yield


@pytest.fixture
def db_session(engine):
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
