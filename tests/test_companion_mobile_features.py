"""Owner-scope tests for the read-only mobile companion endpoints.

Mirrors tests/test_companion_readonly.py: exercise the pure scoping helpers
(`token_owner` / `owner_can_see` / `has_companion_scope`) that every read
endpoint in companion/mobile_features.py relies on, so the multi-tenant rule
can't silently regress. A bearer token for owner A must never see owner B's
rows; legacy null-owner rows are shared; a scope-less token is rejected.
"""

import contextlib
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@contextlib.contextmanager
def _import_time_core_database_stub():
    """Stub core.database ONLY while importing the module under test, then restore.

    companion.mobile_features lazy-imports core.database inside its handlers, so the
    top-level import below normally never touches it. Under a minimal/stubbed-deps
    env a transitive import could still pull it in (it builds a SQLAlchemy engine at
    import time), so stub it defensively for the import. Crucially we restore
    sys.modules afterwards: leaving a fake core.database behind persists for the whole
    pytest session and pollutes sibling test modules that import the real one.
    """
    sentinel = object()
    prev = sys.modules.get("core.database", sentinel)
    if prev is sentinel:
        stub = types.ModuleType("core.database")
        stub.__getattr__ = lambda name: MagicMock()  # type: ignore[attr-defined]
        sys.modules["core.database"] = stub
    try:
        yield
    finally:
        if prev is sentinel:
            # We installed the stub; remove it so the real module loads on next import.
            sys.modules.pop("core.database", None)


with _import_time_core_database_stub():
    from companion.mobile_features import (  # noqa: E402
        setup_mobile_companion_routes,
        token_owner,
        owner_can_see,
        has_companion_scope,
    )


def _request(**state):
    return SimpleNamespace(state=SimpleNamespace(**state))


# ── owner_can_see: the core read-scoping predicate ──────────────────────────

def test_owner_can_see_own_row():
    assert owner_can_see("alice", "alice") is True


def test_owner_can_see_rejects_another_owner():
    assert owner_can_see("bob", "alice") is False


def test_owner_can_see_legacy_null_owner_is_shared():
    assert owner_can_see(None, "alice") is True


def test_owner_can_see_null_caller_never_sees_named_rows():
    # A null caller (legacy single-user) sees only null rows, never a named owner's.
    assert owner_can_see("bob", None) is False
    assert owner_can_see(None, None) is True


# ── token_owner: a bearer resolves to its REAL owner, not the "api" pseudo-user ─

def test_token_owner_bearer_uses_stamped_owner():
    assert token_owner(_request(api_token=True, api_token_owner="alice")) == "alice"


def test_token_owner_bearer_without_owner_is_none():
    assert token_owner(_request(api_token=True, api_token_owner=None)) is None


# ── has_companion_scope: only a real paired (chat/companion) token may read ──

def test_scope_accepts_companion_token():
    assert has_companion_scope(_request(api_token=True, api_token_scopes=["companion"])) is True


def test_scope_accepts_chat_token():
    assert has_companion_scope(_request(api_token=True, api_token_scopes=["chat"])) is True


def test_scope_rejects_scopeless_token():
    assert has_companion_scope(_request(api_token=True, api_token_scopes=[])) is False


def test_scope_cookie_session_always_allowed():
    assert has_companion_scope(_request(api_token=False)) is True


# ── router smoke: only the read endpoints register in this tier ─────────────

def test_router_registers_only_read_endpoints():
    paths = {route.path for route in setup_mobile_companion_routes().routes}
    for p in (
        "/api/companion/documents",
        "/api/companion/gallery",
        "/api/companion/calendars",
        "/api/companion/events",
        "/api/companion/email/messages",
        "/api/companion/skills",
        "/api/companion/assistant",
    ):
        assert p in paths, f"missing read endpoint {p}"
    # write / admin endpoints must NOT leak into the read-only tier
    for p in (
        "/api/companion/email/send",
        "/api/companion/events",  # POST shares the path; verified by method below
        "/api/companion/terminal/exec",
        "/api/companion/vault/unlock",
        "/api/companion/admin/status",
    ):
        if p == "/api/companion/events":
            continue
        assert p not in paths, f"unexpected non-read endpoint {p}"
    methods = {(r.path, m) for r in setup_mobile_companion_routes().routes for m in getattr(r, "methods", []) or []}
    assert ("/api/companion/events", "GET") in methods
    assert ("/api/companion/events", "POST") not in methods
