"""Pin owner-scoping of the cleanup preview and cleanup routes.

Security invariant under test:

    The original _apply_owner_filter used an OR predicate
    `(owner == user) | (owner IS NULL)`, which let a caller archive/delete
    every null-owner session in the database — including unmigrated rows
    from other tenants. The fix replaced it with strict equality.

    These tests pin:

      1. _apply_owner_filter uses strict equality for authenticated callers —
         no null-OR predicate, no cross-owner rows (tests 1–3).

      2. owner=None (single-user / auth-disabled mode) leaves the query
         unfiltered — intentional, mirrors owner_filter() in auth_helpers.py.

      3. Both routes forward the resolved caller identity as `owner=` to the
         service layer; they do not hardcode a value or drop the parameter
         (tests 4–5).
"""
import sys
from unittest.mock import MagicMock, AsyncMock

import pytest


# ---------------------------------------------------------------------------
# Lightweight model/query stubs — no SQLAlchemy required.
# Mirrors the pattern in test_document_tool_owner_scope.py.
# ---------------------------------------------------------------------------

class _Column:
    """Records equality comparisons so filter clauses can be inspected."""
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return (self.name, "eq", value)

    def __hash__(self):
        return hash(self.name)


class _SessionModel:
    owner = _Column("owner")


class _Query:
    def __init__(self):
        self.filters = []

    def filter(self, *clauses):
        self.filters.extend(clauses)
        return self

    def order_by(self, *_):
        return self

    def all(self):
        return []

    def first(self):
        return None


# ---------------------------------------------------------------------------
# Fixture: isolate cleanup module imports per-test
# ---------------------------------------------------------------------------

@pytest.fixture
def cleanup_imports(monkeypatch):
    """Return (_apply_owner_filter, setup_cleanup_routes) from a clean import.

    Drops any cached copy of the cleanup modules from sys.modules before
    importing so that prior tests' monkeypatched state does not bleed in.
    monkeypatch restores sys.modules entries on teardown.
    """
    monkeypatch.delitem(sys.modules, "src.cleanup_service", raising=False)
    monkeypatch.delitem(sys.modules, "routes.cleanup_routes", raising=False)

    import src.cleanup_service as svc
    import routes.cleanup_routes as rts
    return svc._apply_owner_filter, rts.setup_cleanup_routes


# ---------------------------------------------------------------------------
# 1–3. _apply_owner_filter unit tests
# ---------------------------------------------------------------------------

def test_apply_owner_filter_strict_equality_no_null_predicate(cleanup_imports):
    """Authenticated caller gets strict owner equality — null-owner rows excluded.

    The bug this pins: the previous OR predicate `(owner == user) | (owner IS NULL)`
    silently included every unmigrated/null-owner session in the caller's cleanup.
    """
    apply_owner_filter, _ = cleanup_imports
    q = _Query()
    result = apply_owner_filter(q, _SessionModel, "alice")

    assert len(q.filters) == 1, (
        f"Expected exactly one filter clause for owner='alice', got {q.filters}"
    )
    assert ("owner", "eq", "alice") in q.filters
    assert ("owner", "eq", None) not in q.filters, (
        "null-owner OR predicate regression: _apply_owner_filter is including "
        "null-owner sessions for an authenticated caller."
    )
    assert result is q


def test_apply_owner_filter_excludes_cross_owner_rows(cleanup_imports):
    """Filter for 'alice' must not produce a 'bob' equality predicate."""
    apply_owner_filter, _ = cleanup_imports
    q = _Query()
    apply_owner_filter(q, _SessionModel, "alice")

    assert ("owner", "eq", "bob") not in q.filters


def test_apply_owner_filter_none_bypasses_filter_for_single_user_mode(cleanup_imports):
    """owner=None (auth disabled / single-user) must leave the query unfiltered.

    Intentional: mirrors owner_filter() in src/auth_helpers.py — in a
    single-user deployment there are no other tenants to protect.
    """
    apply_owner_filter, _ = cleanup_imports
    q = _Query()
    result = apply_owner_filter(q, _SessionModel, None)

    assert q.filters == [], (
        "owner=None should skip filtering entirely (single-user mode), "
        f"but filter clauses were applied: {q.filters}"
    )
    assert result is q


# ---------------------------------------------------------------------------
# 4–5. Route boundary: both routes forward caller identity as owner=
# ---------------------------------------------------------------------------

def test_preview_route_passes_caller_identity_as_owner(monkeypatch, cleanup_imports):
    """GET /api/cleanup/preview must call get_cleanup_preview(owner=<caller>)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _, setup_cleanup_routes = cleanup_imports

    mock_preview = AsyncMock(return_value={
        "sessions_to_archive": [],
        "sessions_to_delete": [],
        "preserved_sessions": [],
        "estimated_space_freed_mb": 0.0,
    })
    monkeypatch.setattr("routes.cleanup_routes.get_cleanup_preview", mock_preview)
    monkeypatch.setattr("routes.cleanup_routes.get_current_user", lambda _req: "alice")

    app = FastAPI()
    app.include_router(setup_cleanup_routes(MagicMock()))
    client = TestClient(app)

    resp = client.get("/api/cleanup/preview")

    assert resp.status_code == 200
    mock_preview.assert_awaited_once_with(owner="alice")


def test_cleanup_route_passes_caller_identity_as_owner(monkeypatch, cleanup_imports):
    """POST /api/cleanup must call cleanup_sessions(session_manager, owner=<caller>)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _, setup_cleanup_routes = cleanup_imports

    mock_cleanup = AsyncMock(return_value=(3, 2, 1.5))
    monkeypatch.setattr("routes.cleanup_routes.cleanup_sessions", mock_cleanup)
    monkeypatch.setattr("routes.cleanup_routes.get_current_user", lambda _req: "alice")

    sm = MagicMock()
    app = FastAPI()
    app.include_router(setup_cleanup_routes(sm))
    client = TestClient(app)

    resp = client.post("/api/cleanup")

    assert resp.status_code == 200
    body = resp.json()
    assert body["archived_count"] == 3
    assert body["deleted_count"] == 2
    assert body["space_freed_mb"] == 1.5
    mock_cleanup.assert_awaited_once_with(sm, owner="alice")
