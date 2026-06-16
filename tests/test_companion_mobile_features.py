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
        companion_admin_available,
        require_companion_admin,
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


# ── router smoke: this (final) tier adds the admin-gated tools ──────────────

def test_router_registers_read_write_and_admin():
    methods = {(r.path, m) for r in setup_mobile_companion_routes().routes for m in getattr(r, "methods", []) or []}
    paths = {p for p, _ in methods}
    # reads + writes still present
    assert "/api/companion/documents" in paths
    assert ("/api/companion/email/send", "POST") in methods
    assert ("/api/companion/assistant", "PATCH") in methods
    # admin-gated tools now present (this tier)
    assert "/api/companion/admin/status" in paths
    assert ("/api/companion/contacts", "GET") in methods
    assert ("/api/companion/terminal/exec", "POST") in methods
    assert ("/api/companion/vault/status", "GET") in methods
    assert ("/api/companion/vault/unlock", "POST") in methods
    assert ("/api/companion/mcp/servers", "GET") in methods
    assert ("/api/companion/cookbook/state", "GET") in methods


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


# ── route-level WRITE owner-scoping (tier 2) ────────────────────────────────
# The write handlers (this tier) are driven through a separate tiny fake-query
# harness (prefixed _W to avoid colliding with the read harness above): it adds
# the mutating verbs the writes need — add / delete / commit / refresh — that the
# read harness doesn't. The security crux: writes stamp/verify the RESOLVED owner;
# cross-owner or null-owner writes/deletes are refused (404/403), never mutated.


class _WPred:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, r):
        return self.fn(r)

    def __or__(self, o):
        return _WPred(lambda r: self(r) or o(r))


class _WCol:
    __hash__ = None

    def __init__(self, name):
        self.name = name

    def __eq__(self, v):
        return _WPred(lambda r: getattr(r, self.name) == v)

    def in_(self, vs):
        s = set(vs)
        return _WPred(lambda r: getattr(r, self.name) in s)


class _Comparison:
    id = _WCol("id"); owner = _WCol("owner")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _CalendarCal:
    id = _WCol("id"); owner = _WCol("owner")


class _CalendarEvent:
    uid = _WCol("uid"); calendar_id = _WCol("calendar_id")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _CrewMember:
    owner = _WCol("owner"); is_default_assistant = _WCol("is_default_assistant")

    def __init__(self, **kw):
        self.id = self.name = self.user_name = self.personality = None
        self.model = self.greeting = self.timezone = self.avatar = None
        self.is_active = True; self.is_default_assistant = False; self.owner = None
        for k, v in kw.items():
            setattr(self, k, v)


class _WQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *ps):
        self.rows = [r for r in self.rows if all(p(r) for p in ps)]
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _WDB:
    def __init__(self, by_model):
        self.by_model = by_model
        self.added = []; self.deleted = []; self.committed = False

    def query(self, model):
        return _WQuery(self.by_model.get(model, []))

    def add(self, o):
        self.added.append(o)

    def delete(self, o):
        self.deleted.append(o)

    def commit(self):
        self.committed = True

    def refresh(self, o):
        pass

    def close(self):
        pass


def _route(path, method):
    for r in setup_mobile_companion_routes().routes:
        if getattr(r, "path", "") == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(f"{method} {path} not found")


def _req(owner="alice", scopes=("companion",)):
    return SimpleNamespace(state=SimpleNamespace(
        api_token=True, api_token_owner=owner, api_token_scopes=list(scopes), current_user="api"))


@pytest.fixture
def db(monkeypatch):
    import companion.mobile_features as mf
    dbmod = sys.modules["core.database"]

    def _install(by_model):
        d = _WDB(by_model)
        monkeypatch.setattr(dbmod, "SessionLocal", lambda: d, raising=False)
        for m, name in ((_Comparison, "Comparison"), (_CalendarCal, "CalendarCal"),
                        (_CalendarEvent, "CalendarEvent"), (_CrewMember, "CrewMember")):
            monkeypatch.setattr(dbmod, name, m, raising=False)
        monkeypatch.setattr(mf, "get_current_user", lambda request: "api", raising=False)
        return d
    return _install


def test_compare_record_stamps_owner(db):
    d = db({_Comparison: []})
    res = _route("/api/companion/compare/record", "POST")(
        _req(), prompt="p", model_a="a", model_b="b", winner="a", is_blind="false")
    assert res["status"] == "ok" and d.added[0].owner == "alice"


def test_compare_record_requires_owner(db):
    db({_Comparison: []})
    with pytest.raises(HTTPException) as e:
        _route("/api/companion/compare/record", "POST")(
            _req(owner=None), prompt="p", model_a="a", model_b="b", winner="a", is_blind="false")
    assert e.value.status_code == 403


def test_compare_delete_cross_owner_404(db):
    d = db({_Comparison: [_Comparison(id="c1", owner="bob")]})
    with pytest.raises(HTTPException) as e:
        _route("/api/companion/compare/{comp_id}", "DELETE")(_req(), comp_id="c1")
    assert e.value.status_code == 404 and d.deleted == []


def test_event_create_into_cross_owner_calendar_404(db):
    d = db({_CalendarCal: [_CalendarCal()], _CalendarEvent: []})
    d.by_model[_CalendarCal][0].id = "b"; d.by_model[_CalendarCal][0].owner = "bob"
    with pytest.raises(HTTPException) as e:
        _route("/api/companion/events", "POST")(
            _req(), calendar_id="b", summary="x",
            dtstart="2026-06-04T10:00:00", dtend="2026-06-04T11:00:00",
            description="", location="", all_day="false")
    assert e.value.status_code == 404 and d.added == []


def test_event_delete_cross_owner_404(db):
    cal = _CalendarCal(); cal.id = "b"; cal.owner = "bob"
    ev = _CalendarEvent(uid="e1", calendar_id="b")
    d = db({_CalendarCal: [cal], _CalendarEvent: [ev]})
    with pytest.raises(HTTPException) as e:
        _route("/api/companion/events/{uid}", "DELETE")(_req(), uid="e1")
    assert e.value.status_code == 404 and d.deleted == []


def test_assistant_patch_refuses_synthetic_owner(db):
    db({_CrewMember: []})
    with pytest.raises(HTTPException) as e:
        _route("/api/companion/assistant", "PATCH")(
            _req(owner="api"), name="x", user_name=None, personality=None,
            greeting=None, model=None, timezone=None)
    assert e.value.status_code == 400


def test_assistant_patch_creates_for_owner(db):
    d = db({_CrewMember: []})
    res = _route("/api/companion/assistant", "PATCH")(
        _req(), name="Ally", user_name=None, personality=None,
        greeting=None, model=None, timezone=None)
    assert res["assistant"]["name"] == "Ally"
    assert d.added and d.added[0].owner == "alice" and d.added[0].is_default_assistant is True


def test_email_send_null_owner_403(db, monkeypatch):
    calls = []
    helpers = types.ModuleType("routes.email_helpers")
    helpers._assert_owns_account = lambda a, o: calls.append((a, o))
    helpers._get_email_config = lambda account_id=None, owner="": {}
    helpers._send_smtp_message = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "routes.email_helpers", helpers)
    with pytest.raises(HTTPException) as e:
        _route("/api/companion/email/send", "POST")(
            _req(owner=None), account_id="a", to="x@y.test", subject="", body="")
    assert e.value.status_code == 403 and calls == []  # never reached the ownership check


# ── admin-gate enforcement (tier 3) ─────────────────────────────────────────
# Every admin endpoint must pass through require_companion_admin, so each 403s
# when the gate is closed. companion_admin_available's own triple-lock is unit-
# tested too. (require_companion_admin reads the module-level
# companion_admin_available, so patching it here controls the gate.)

import companion.mobile_features as _mf  # noqa: E402


@pytest.fixture
def admin_gate(monkeypatch):
    def _set(allowed):
        monkeypatch.setattr(_mf, "companion_admin_available", lambda request: allowed)
    return _set


@pytest.mark.parametrize("path,method,kwargs", [
    ("/api/companion/contacts", "GET", {}),
    ("/api/companion/terminal/exec", "POST", {"command": "echo hi"}),
    ("/api/companion/vault/status", "GET", {}),
    ("/api/companion/vault/unlock", "POST", {"master_password": "x"}),
    ("/api/companion/mcp/servers", "GET", {}),
    ("/api/companion/cookbook/state", "GET", {}),
])
def test_admin_endpoints_403_when_gate_closed(admin_gate, path, method, kwargs):
    admin_gate(False)
    fn = _route(path, method)
    with pytest.raises(HTTPException) as e:
        res = fn(_req(), **kwargs)
        # vault/unlock is async — resolve it to surface the raise. Use asyncio.run so
        # the test owns a fresh loop and never depends on a current event loop left
        # (or closed) by an earlier test in the suite.
        if hasattr(res, "__await__"):
            import asyncio
            asyncio.run(res)
    assert e.value.status_code == 403


def test_terminal_exec_runs_when_gate_open(admin_gate):
    admin_gate(True)
    res = _route("/api/companion/terminal/exec", "POST")(_req(), command="printf hi", timeout=30)
    assert res["stdout"] == "hi" and res["exit_code"] == 0


class _AuthManager:
    def __init__(self, admins):
        self.admins = set(admins)

    def is_admin(self, u):
        return u in self.admins


def _admin_req(owner, scopes=("companion",), admins=("alice",), has_am=True):
    am = _AuthManager(admins) if has_am else None
    return SimpleNamespace(
        state=SimpleNamespace(api_token=True, api_token_owner=owner,
                              api_token_scopes=list(scopes), current_user="api"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=am)))


@pytest.fixture
def setting(monkeypatch):
    import src.settings as s

    def _set(on):
        monkeypatch.setattr(s, "get_setting",
                            lambda k, d=None: on if k == "companion_admin_enabled" else d)
    return _set


def test_admin_available_all_locks(setting):
    setting(True)
    assert companion_admin_available(_admin_req("alice")) is True


def test_admin_unavailable_when_setting_off(setting):
    setting(False)
    assert companion_admin_available(_admin_req("alice")) is False


def test_admin_unavailable_when_owner_not_admin(setting):
    setting(True)
    assert companion_admin_available(_admin_req("bob")) is False


def test_admin_unavailable_for_chat_only_scope(setting):
    # Admin needs the companion scope specifically — a chat-only token can read
    # data (relaxed) but must never reach admin surface.
    setting(True)
    assert companion_admin_available(_admin_req("alice", scopes=("chat",))) is False


def test_require_admin_raises_generic_403(setting):
    setting(False)
    with pytest.raises(HTTPException) as e:
        require_companion_admin(_admin_req("alice"))
    assert e.value.status_code == 403
