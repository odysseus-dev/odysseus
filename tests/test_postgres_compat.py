"""Dual-dialect smoke test for the optional PostgreSQL support.

The SQLite leg always runs; the Postgres leg runs only when TEST_DATABASE_URL is
set (e.g. TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/odysseus_test).
It proves the ORM schema + a real round-trip work identically on both dialects,
so a future change can't silently break Postgres. Values are asserted (not just
"no exception") to catch representation drift: JSON (json type on Postgres),
Boolean, EncryptedText decrypt, and Integer autoincrement PKs.

Uses a fresh engine per leg (not the import-time singleton) so it does not depend
on the process-wide DATABASE_URL.
"""
import os
import types

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
if not isinstance(sqlalchemy, types.ModuleType):
    pytest.skip("sqlalchemy is stubbed in this environment", allow_module_level=True)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ModelEndpoint, UserTool, UserToolData
from core.database import Session as DBSession


def _roundtrip(url: str) -> None:
    eng = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    Base.metadata.create_all(bind=eng)
    Make = sessionmaker(bind=eng)
    try:
        # ---- write ----
        s = Make()
        s.add(DBSession(
            id="pgcompat-session",
            name="compat",
            endpoint_url="http://example/v1",
            model="test-model",
            rag=True,                       # Boolean
            headers={"a": 1, "b": "two"},   # generic JSON column (json type on Postgres)
        ))
        s.add(ModelEndpoint(
            id="pgcompat-endpoint",
            name="compat-ep",
            base_url="http://example/v1",
            api_key="super-secret-plaintext",   # EncryptedText (encrypts on write)
        ))
        # UserToolData.tool_id is a NOT NULL FK -> insert the parent tool first.
        s.add(UserTool(
            id="pgcompat-tool",
            name="compat-tool",
            html_content="<div>x</div>",
        ))
        s.add(UserToolData(                 # Integer autoincrement PK (id left unset)
            tool_id="pgcompat-tool",
            key="k",
            value="v",
        ))
        s.commit()
        s.close()

        # ---- read back, assert VALUES ----
        s2 = Make()
        sess = s2.get(DBSession, "pgcompat-session")
        assert sess.headers == {"a": 1, "b": "two"}     # JSON dict survives round-trip
        assert sess.rag is True                          # Boolean survives round-trip

        ep = s2.get(ModelEndpoint, "pgcompat-endpoint")
        assert ep.api_key == "super-secret-plaintext"    # EncryptedText decrypts to original

        td = s2.query(UserToolData).filter_by(tool_id="pgcompat-tool", key="k").one()
        assert isinstance(td.id, int) and td.id > 0      # autoincrement PK was assigned
        assert td.value == "v"
        s2.close()
    finally:
        Base.metadata.drop_all(bind=eng)
        eng.dispose()


def test_sqlite_roundtrip(tmp_path):
    _roundtrip(f"sqlite:///{tmp_path / 'compat.db'}")


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL=postgresql://... to run the Postgres leg",
)
def test_postgres_roundtrip():
    pytest.importorskip("psycopg2")
    _roundtrip(os.environ["TEST_DATABASE_URL"])
