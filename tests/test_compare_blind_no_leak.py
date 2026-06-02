"""Regression tests for issue #1285 — blind compare leaked model identities.

Compare's blind mode anonymizes the pane headers ("Model A"/"Model B"), but the
backend `/api/compare/start` path still named the helper sessions after the real
models (`[CMP] gpt-4o`) and echoed `model_left`/`model_right` in the response —
so the sidebar, `/api/sessions`, and the start response all revealed which model
was which before the user voted, defeating blind compare.

These tests drive the real `/api/compare/start` handler directly (no TestClient,
which hangs in this env) and pin that blind mode neither names sessions after the
model nor returns the model sides, while non-blind mode is unchanged.
"""
import types
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
from fastapi import APIRouter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
import routes.compare_routes as cr


@pytest.fixture
def temp_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    engine = create_engine(
        f"sqlite:///{tmp.name}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine)
    monkeypatch.setattr(cr, "SessionLocal", TS)
    return TS


class _FakeManager:
    def __init__(self):
        self.created = {}      # sid -> name
        self.sessions = {}

    def create_session(self, session_id, name, endpoint_url, model, rag=False, owner=None):
        self.created[session_id] = name
        self.sessions[session_id] = types.SimpleNamespace(headers={})
        return self.sessions[session_id]


def _start_endpoint(manager):
    # Module-level router accumulates routes across setup calls — reset it.
    cr.router = APIRouter(prefix="/api/compare", tags=["compare"])
    cr.setup_compare_routes(manager)
    for r in cr.router.routes:
        if getattr(r, "path", None) == "/api/compare/start" and "POST" in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError("start endpoint not found")


def _request():
    return types.SimpleNamespace(state=types.SimpleNamespace(current_user="tester"))


def test_blind_start_does_not_leak_model(temp_db):
    mgr = _FakeManager()
    endpoint = _start_endpoint(mgr)
    resp = endpoint(
        _request(), prompt="hi", model_a="gpt-4o", model_b="llama-3.1-70b",
        endpoint_a="http://a", endpoint_b="http://b", is_blind="true",
    )
    # Session names must NOT contain the real model ids.
    names = list(mgr.created.values())
    assert names and all("gpt-4o" not in n and "llama" not in n for n in names), names
    assert sorted(names) == ["[CMP] Model A", "[CMP] Model B"]
    # The response must not reveal which model is on which side before voting.
    assert resp["model_left"] is None and resp["model_right"] is None
    assert resp["is_blind"] is True


def test_non_blind_start_keeps_real_names(temp_db):
    mgr = _FakeManager()
    endpoint = _start_endpoint(mgr)
    resp = endpoint(
        _request(), prompt="hi", model_a="gpt-4o", model_b="llama-3.1-70b",
        endpoint_a="http://a", endpoint_b="http://b", is_blind="false",
    )
    names = list(mgr.created.values())
    assert any("gpt-4o" in n for n in names)
    # Non-blind mapping is fixed left=a, right=b.
    assert resp["model_left"] == "gpt-4o" and resp["model_right"] == "llama-3.1-70b"
    assert resp["is_blind"] is False


# The frontend session-creation path is the one the live UI uses; it pulls in
# browser globals so it can't be imported under node. Guard the masking at the
# source level so a future edit can't silently drop it (issue #1285).
@pytest.mark.parametrize("relpath", [
    "static/js/compare/index.js",
    "static/js/compare/panes.js",
])
def test_frontend_masks_session_name_in_blind_mode(relpath):
    src = (_REPO / relpath).read_text(encoding="utf-8")
    # Every place that names a [CMP] session must be gated on blind mode and
    # fall back to a neutral "Model X" label.
    assert "state._blindMode ? '[CMP] Model '" in src, relpath
    # The old unconditional real-name append must be gone.
    assert "fd.append('name', '[CMP] ' + modelShorts[i])" not in src
    assert "fd.append('name', '[CMP] ' + m.name)" not in src
