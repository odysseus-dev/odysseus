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

import pytest
from fastapi import HTTPException

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


# ── Behavioural endpoint tests ──────────────────────────────────────────────
# The tests above cover the pure predicates; these exercise what the handlers
# actually do — scope gate (403), owner-scoping (cross-owner excluded / 404), the
# gallery filename sanitization, and the new ?limit/?offset paging — by calling
# the registered handlers with a fake, injected DB. The DB is monkeypatched onto
# `sys.modules["core.database"]` with `monkeypatch.setitem` so it is restored
# after each test and never leaks into sibling modules.


def _handler(path_suffix, method="GET"):
    """The endpoint callable registered at /api/companion<path_suffix>."""
    for r in setup_mobile_companion_routes().routes:
        if getattr(r, "path", "") == "/api/companion" + path_suffix and method in (r.methods or set()):
            return r.endpoint
    raise AssertionError(f"{method} {path_suffix} not registered")


# --- fake SQLAlchemy query layer (predicate-based, supports the new chain) ---

class _Pred:
    def __init__(self, fn):
        self._fn = fn

    def __call__(self, row):
        return self._fn(row)

    def __or__(self, other):
        return _Pred(lambda r: self(r) or other(r))


class _Col:
    __hash__ = None  # predicates are not hashable; columns aren't used as keys

    def __init__(self, name):
        self.name = name

    def _v(self, row):
        return getattr(row, self.name, None)

    def __eq__(self, value):
        return _Pred(lambda r: self._v(r) == value)

    def __ne__(self, value):
        return _Pred(lambda r: self._v(r) != value)

    def __gt__(self, value):
        return _Pred(lambda r: self._v(r) is not None and self._v(r) > value)

    def __lt__(self, value):
        return _Pred(lambda r: self._v(r) is not None and self._v(r) < value)

    def in_(self, values):
        allowed = set(values)
        return _Pred(lambda r: self._v(r) in allowed)

    def desc(self):
        return self  # ordering is irrelevant to these assertions; offset/limit slice


def _model(name, columns):
    return type(name, (), {c: _Col(c) for c in columns})


# Column sets the handlers reference (so `.filter(Model.col == ...)` resolves).
_MODELS = {
    "Document": _model("Document", ["id", "is_active", "archived", "owner"]),
    "Comparison": _model("Comparison", ["id", "owner", "voted_at"]),
    "CalendarCal": _model("CalendarCal", ["id", "owner"]),
    "CalendarEvent": _model("CalendarEvent", ["calendar_id", "status", "dtstart", "dtend"]),
    "EmailAccount": _model("EmailAccount", ["id", "owner"]),
    "GalleryImage": _model("GalleryImage", ["id", "is_active", "owner", "taken_at"]),
}


class _Query:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *preds):
        self._rows = [r for r in self._rows if all(p(r) for p in preds)]
        return self

    def order_by(self, *args):
        return self

    def offset(self, n):
        self._rows = self._rows[n:]
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _DB:
    def __init__(self, rows_by_model):
        self._rows = rows_by_model

    def query(self, model):
        return _Query(self._rows.get(model.__name__, []))

    def close(self):
        pass


def _install_db(monkeypatch, **rows_by_model):
    """Install a fake core.database (SessionLocal + model classes) for one test."""
    fake = types.ModuleType("core.database")
    for name, cls in _MODELS.items():
        setattr(fake, name, cls)
    db = _DB(rows_by_model)
    fake.SessionLocal = lambda: db
    monkeypatch.setitem(sys.modules, "core.database", fake)
    return db


def _bearer(owner="alice", scopes=("companion",)):
    return _request(api_token=True, api_token_owner=owner, api_token_scopes=list(scopes))


# --- 403: every GET endpoint refuses a scope-less bearer token --------------

# (path, method, extra kwargs) — the scope check runs before any param use.
_GET_ENDPOINTS = [
    ("/documents", {}),
    ("/documents/{doc_id}", {"doc_id": "d1"}),
    ("/compare/history", {}),
    ("/calendars", {}),
    ("/events", {}),
    ("/email/accounts", {}),
    ("/email/messages", {"account_id": "a1"}),
    ("/email/message/{uid}", {"uid": "u1", "account_id": "a1"}),
    ("/gallery", {}),
    ("/gallery/image/{image_id}", {"image_id": "i1"}),
    ("/assistant", {}),
    ("/skills", {}),
]


@pytest.mark.parametrize("suffix,kwargs", _GET_ENDPOINTS)
def test_every_read_endpoint_rejects_scopeless_token(suffix, kwargs):
    fn = _handler(suffix)
    req = _request(api_token=True, api_token_owner="alice", api_token_scopes=[])
    with pytest.raises(HTTPException) as exc:
        fn(req, **kwargs)
    assert exc.value.status_code == 403


# --- list endpoints: a cross-owner row is never returned --------------------

def test_documents_list_excludes_other_owners(monkeypatch):
    _install_db(monkeypatch, Document=[
        SimpleNamespace(id="a", owner="alice", is_active=True, archived=False,
                        current_content="mine", title="A", language="en"),
        SimpleNamespace(id="b", owner="bob", is_active=True, archived=False,
                        current_content="secret", title="B", language="en"),
        SimpleNamespace(id="s", owner=None, is_active=True, archived=False,
                        current_content="shared", title="S", language="en"),
    ])
    res = _handler("/documents")(_bearer("alice"))
    ids = {r["id"] for r in res["items"]}
    assert ids == {"a", "s"}  # own + shared null-owner; bob's never appears


def test_gallery_list_excludes_other_owners(monkeypatch):
    _install_db(monkeypatch, GalleryImage=[
        SimpleNamespace(id="a", owner="alice", is_active=True, taken_at=None,
                        prompt="", model="", favorite=False, width=1, height=1, filename="a.png"),
        SimpleNamespace(id="b", owner="bob", is_active=True, taken_at=None,
                        prompt="", model="", favorite=False, width=1, height=1, filename="b.png"),
    ])
    res = _handler("/gallery")(_bearer("alice"))
    assert {r["id"] for r in res["items"]} == {"a"}


def test_email_accounts_list_excludes_other_owners(monkeypatch):
    _install_db(monkeypatch, EmailAccount=[
        SimpleNamespace(id="a", owner="alice", name="A", from_address="a@x", enabled=True, is_default=True),
        SimpleNamespace(id="b", owner="bob", name="B", from_address="b@x", enabled=True, is_default=False),
    ])
    res = _handler("/email/accounts")(_bearer("alice"))
    assert {r["id"] for r in res["items"]} == {"a"}


# --- detail endpoints: cross-owner is 404 (never confirm existence) ---------

def test_document_detail_cross_owner_is_404(monkeypatch):
    _install_db(monkeypatch, Document=[
        SimpleNamespace(id="b", owner="bob", is_active=True, archived=False,
                        current_content="secret", title="B", language="en"),
    ])
    with pytest.raises(HTTPException) as exc:
        _handler("/documents/{doc_id}")(_bearer("alice"), doc_id="b")
    assert exc.value.status_code == 404


def test_gallery_image_cross_owner_is_404(monkeypatch):
    _install_db(monkeypatch, GalleryImage=[
        SimpleNamespace(id="b", owner="bob", is_active=True, taken_at=None, filename="b.png"),
    ])
    with pytest.raises(HTTPException) as exc:
        _handler("/gallery/image/{image_id}")(_bearer("alice"), image_id="b")
    assert exc.value.status_code == 404


# --- gallery filename sanitization (path traversal / NUL byte) --------------

@pytest.mark.parametrize("stored,expected_basename", [
    ("../../etc/passwd", "passwd"),
    ("/etc/passwd", "passwd"),
    ("foo\x00.png", "foo_.png"),
    ("a/b/../../../../secret.png", "secret.png"),
])
def test_gallery_image_filename_is_confined(monkeypatch, stored, expected_basename):
    _install_db(monkeypatch, GalleryImage=[
        SimpleNamespace(id="i1", owner="alice", is_active=True, taken_at=None, filename=stored),
    ])
    seen = {}

    def _fake_isfile(path):
        seen["path"] = path
        return False  # force the 404 path; we only care that the path was confined

    monkeypatch.setattr("os.path.isfile", _fake_isfile)
    with pytest.raises(HTTPException) as exc:
        _handler("/gallery/image/{image_id}")(_bearer("alice"), image_id="i1")
    assert exc.value.status_code == 404
    # The resolved path never escapes data/generated_images and has no traversal/NUL.
    assert seen["path"] == os.path.join("data", "generated_images", expected_basename)
    assert ".." not in seen["path"] and "\x00" not in seen["path"]


# --- pagination: ?limit / ?offset bound and walk the result -----------------

def _docs(n):
    return [SimpleNamespace(id=f"d{i:02d}", owner="alice", is_active=True, archived=False,
                            current_content="x", title=f"t{i}", language="en") for i in range(n)]


def test_documents_limit_bounds_the_page(monkeypatch):
    _install_db(monkeypatch, Document=_docs(5))
    res = _handler("/documents")(_bearer("alice"), limit=2, offset=0)
    assert len(res["items"]) == 2 and res["limit"] == 2 and res["offset"] == 0


def test_documents_offset_walks_the_page(monkeypatch):
    _install_db(monkeypatch, Document=_docs(5))
    res = _handler("/documents")(_bearer("alice"), limit=2, offset=4)
    assert len(res["items"]) == 1 and res["offset"] == 4


def test_documents_limit_is_clamped_to_max(monkeypatch):
    _install_db(monkeypatch, Document=_docs(3))
    res = _handler("/documents")(_bearer("alice"), limit=10_000)
    # clamped to MAX_PAGE_LIMIT (still returns all 3 here, but limit is bounded)
    from companion.mobile_features import MAX_PAGE_LIMIT
    assert res["limit"] == MAX_PAGE_LIMIT


def test_documents_bad_pagination_falls_back_to_defaults(monkeypatch):
    _install_db(monkeypatch, Document=_docs(1))
    from companion.mobile_features import DEFAULT_PAGE_LIMIT
    res = _handler("/documents")(_bearer("alice"), limit="abc", offset="-9")
    assert res["limit"] == DEFAULT_PAGE_LIMIT and res["offset"] == 0
