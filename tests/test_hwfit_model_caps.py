"""Tests for GET /api/hwfit/model-caps endpoint."""

from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client():
    from routes.hwfit_routes import setup_hwfit_routes

    app = FastAPI()
    app.include_router(setup_hwfit_routes())
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeModel:
    """Minimal DiscoveredModel stand-in for DB query results."""

    def __init__(self, name, capabilities):
        self.name = name
        self.capabilities = capabilities


class _FakeQuery:
    """Chains .filter().first() over a static list of rows."""

    def __init__(self, rows):
        self._rows = rows
        self._filtered = list(rows)

    def filter(self, *args, **kwargs):
        import sqlalchemy.sql.operators as _ops

        clone = _FakeQuery(self._rows)
        clause = args[0] if args else None
        if clause is None:
            return clone
        try:
            op = clause.operator
            pat = clause.right.value
            if op is _ops.eq:
                clone._filtered = [r for r in self._rows if r.name == pat]
            elif op is _ops.like_op:
                # Pattern is like "%/suffix" — match names ending with the suffix part.
                suffix = pat.lstrip('%')
                clone._filtered = [
                    r for r in self._rows if r.name.endswith(suffix)
                ]
            else:
                clone._filtered = list(self._rows)
        except AttributeError:
            clone._filtered = list(self._rows)
        return clone

    def first(self):
        return self._filtered[0] if self._filtered else None


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model_cls):
        return _FakeQuery(self._rows)


@contextmanager
def _fake_db_session_factory(rows):
    yield _FakeDB(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_model_caps_known_exact_match(monkeypatch):
    """Returns capabilities from a catalog row matched by exact name."""
    rows = [_FakeModel("google/gemma-4-12b-it", ["vision", "tools"])]
    monkeypatch.setattr(
        "core.database.get_db_session",
        lambda: _fake_db_session_factory(rows),
    )

    resp = _client().get("/api/hwfit/model-caps?model=google/gemma-4-12b-it")
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert "vision" in data["capabilities"]
    assert "tools" in data["capabilities"]


def test_model_caps_known_suffix_match(monkeypatch):
    """Falls back to suffix match when the full model path isn't in the catalog."""
    rows = [_FakeModel("google/gemma-4-12b-it", ["vision"])]
    monkeypatch.setattr(
        "core.database.get_db_session",
        lambda: _fake_db_session_factory(rows),
    )

    # Query with just the basename — no org prefix
    resp = _client().get("/api/hwfit/model-caps?model=gemma-4-12b-it")
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert "vision" in data["capabilities"]


def test_model_caps_unknown_model(monkeypatch):
    """Returns empty capabilities and found=False for an unknown model."""
    monkeypatch.setattr(
        "core.database.get_db_session",
        lambda: _fake_db_session_factory([]),
    )

    resp = _client().get("/api/hwfit/model-caps?model=org/unknown-7b")
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["capabilities"] == []


def test_model_caps_empty_model_param():
    """Returns empty capabilities immediately without hitting the DB when model=''."""
    resp = _client().get("/api/hwfit/model-caps")
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["capabilities"] == []
    assert data["model_id"] == ""


def test_model_caps_non_list_capabilities(monkeypatch):
    """Handles a row whose capabilities field is None gracefully."""
    rows = [_FakeModel("org/model-no-caps", None)]
    monkeypatch.setattr(
        "core.database.get_db_session",
        lambda: _fake_db_session_factory(rows),
    )

    resp = _client().get("/api/hwfit/model-caps?model=org/model-no-caps")
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["capabilities"] == []
