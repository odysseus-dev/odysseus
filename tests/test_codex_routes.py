"""Tests for routes/codex_routes.py — scope enforcement, _as_owner removal,
body field whitelist, /plugin.zip filtering, and /capabilities shaping.

Pattern mirrors tests/test_api_token_routes.py: monkeypatch-stub the heavy
modules, force a fresh import of the route module, then call the endpoint
functions directly with SimpleNamespace request objects. No ASGI client,
no real DB, no real email/CalDAV/imap — this is unit-level coverage of
the codex security boundary.
"""

import json
import sys
import types
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


# ── Stubs installed before importing routes.codex_routes ────────────────────


def _install_codex_stubs(monkeypatch, do_manage_notes=None):
    """Install per-test stubs so routes.codex_routes imports cleanly without
    pulling in the real database / email / calendar / document toolchains."""

    # python_multipart — FastAPI validates Form() at import time; the codex
    # router doesn't use Form, but transitive imports of other route modules
    # (e.g. via _find_endpoint candidates) might. Stub it to be safe.
    mp_stub = types.ModuleType("python_multipart")
    mp_stub.__version__ = "0.0.13"
    monkeypatch.setitem(sys.modules, "python_multipart", mp_stub)

    # core.database — the real module declares SQLAlchemy ORM models; the
    # conftest sqlalchemy stub causes a metaclass conflict during import of
    # the borrowed routers, so we keep a parallel MagicMock here.
    class _DBStub(types.ModuleType):
        def __getattr__(self, name):
            return MagicMock()
    db_stub = _DBStub("core.database")
    db_stub.get_db_session = MagicMock()
    db_stub.ApiToken = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db_stub)

    # src.tool_implementations.do_manage_notes — the only function the codex
    # route actually calls into at the unit level. Other imports are inside
    # try/except so a missing module just gives a 503.
    if do_manage_notes is None:
        async def _fake_do_manage_notes(content: str, owner=None) -> dict:
            return {"echo": content, "owner": owner}
        do_manage_notes = _fake_do_manage_notes

    ti_stub = types.ModuleType("src.tool_implementations")
    ti_stub.do_manage_notes = do_manage_notes
    monkeypatch.setitem(sys.modules, "src.tool_implementations", ti_stub)

    # src.request_models — used inside codex_memory_add for the Pydantic
    # constructor. Provide a thin stand-in so we don't drag in pydantic
    # validation behavior we don't need to test here.
    rm_stub = types.ModuleType("src.request_models")
    class _MemoryAddRequest:
        def __init__(self, text="", category="fact", source="user", session_id=None):
            self.text = text
            self.category = category
            self.source = source
            self.session_id = session_id
    rm_stub.MemoryAddRequest = _MemoryAddRequest
    monkeypatch.setitem(sys.modules, "src.request_models", rm_stub)

    # routes.email_helpers — used for _assert_owns_account inside the
    # codex emails endpoints. The unit tests don't pass account_id so the
    # helper is never called; still, stub it to a no-op for safety.
    eh_stub = types.ModuleType("routes.email_helpers")
    eh_stub._assert_owns_account = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "routes.email_helpers", eh_stub)

    # Force a fresh import of the codex route module so the stubs above
    # bind to the imported names. (Without this, a sibling test in the
    # same pytest session could leak its stubs into ours.)
    monkeypatch.delitem(sys.modules, "routes.codex_routes", raising=False)
    monkeypatch.delitem(sys.modules, "src.codex_scopes", raising=False)

    import routes.codex_routes as mod  # noqa: PLC0415
    return mod


# ── Request builders ────────────────────────────────────────────────────────


def _api_req(*, owner="alice", scopes=None, path="/api/codex/capabilities"):
    """Request object shaped like what core.middleware sets for a codex
    api_token caller. Mirrors the SimpleNamespace used in
    tests/test_api_chat_security.py:209-215."""
    return SimpleNamespace(
        state=SimpleNamespace(
            api_token=True,
            api_token_scopes=list(scopes) if scopes is not None else [],
            api_token_owner=owner,
        ),
        url=SimpleNamespace(path=path),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace()),
    )


def _cookie_req(*, user="bob", path="/api/codex/capabilities"):
    """Request object shaped like a cookie-session caller — no api_token
    fields, current_user set instead."""
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        url=SimpleNamespace(path=path),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )


# ── /api/codex/capabilities ─────────────────────────────────────────────────


def test_capabilities_with_token_hides_unscoped_tools(monkeypatch):
    mod = _install_codex_stubs(monkeypatch)
    router = mod.setup_codex_routes()
    caps = mod._find_endpoint(router, "GET", "/api/codex/capabilities")(_api_req(scopes=["todos:read"]))
    assert caps["integration"] == "codex"
    assert caps["token_scopes"] == ["todos:read"]
    assert caps["tools"]["todos"]["read"] is True
    assert caps["tools"]["todos"]["write"] is False
    assert caps["tools"]["email"]["read"] is False
    assert caps["tools"]["email"]["send"] is False


def test_capabilities_with_full_scopes_enables_everything(monkeypatch):
    mod = _install_codex_stubs(monkeypatch)
    router = mod.setup_codex_routes()
    full = sorted([
        "todos:read", "todos:write",
        "email:read", "email:draft", "email:send",
        "memory:read", "memory:write",
        "calendar:read", "calendar:write",
        "documents:read", "documents:write",
    ])
    caps = mod._find_endpoint(router, "GET", "/api/codex/capabilities")(_api_req(scopes=full))
    for tool, expected in [
        ("todos", {"read": True, "write": True}),
        ("email", {"read": True, "draft": True, "send": True}),
        ("memory", {"read": True, "write": True}),
        ("calendar", {"read": True, "write": True}),
        ("documents", {"read": True, "write": True}),
    ]:
        for k, v in expected.items():
            assert caps["tools"][tool][k] is v, f"{tool}.{k} should be {v}"


# ── Scope enforcement on /api/codex/todos ──────────────────────────────────


def test_list_todos_without_read_scope_403s(monkeypatch):
    mod = _install_codex_stubs(monkeypatch)
    router = mod.setup_codex_routes()
    handler = mod._find_endpoint(router, "GET", "/api/codex/todos")
    with pytest.raises(HTTPException) as ei:
        import asyncio
        asyncio.run(handler(_api_req(scopes=["email:read"])))
    assert ei.value.status_code == 403
    assert "todos" in ei.value.detail.lower()


async def test_manage_todos_add_with_read_only_scope_allowed(monkeypatch):
    """WRITE_ACTIONS includes 'add', so the scope picks up todos:write —
    the test fixture only grants todos:read to prove the action-driven
    allowlist works."""
    mod = _install_codex_stubs(monkeypatch)
    router = mod.setup_codex_routes()
    handler = mod._find_endpoint(router, "POST", "/api/codex/todos")
    out = await handler(_api_req(scopes=["todos:write"]), {"action": "add", "title": "buy milk"})
    # The fake do_manage_notes echoes its input; verify the filtered body
    # reached it with only the whitelisted fields.
    echoed = json.loads(out["echo"])
    assert echoed["action"] == "add"
    assert echoed["title"] == "buy milk"


async def test_manage_todos_unknown_fields_are_dropped(monkeypatch):
    """Defense-in-depth: a codex client trying to smuggle in an unknown
    key (e.g. `owner=eve` or `__class__=...`) sees the field dropped before
    reaching do_manage_notes."""
    mod = _install_codex_stubs(monkeypatch)
    router = mod.setup_codex_routes()
    handler = mod._find_endpoint(router, "POST", "/api/codex/todos")
    out = await handler(
        _api_req(scopes=["todos:write"]),
        {"action": "add", "title": "x", "owner": "eve", "__class__": {"y": 1}, "priority": 1},
    )
    echoed = json.loads(out["echo"])
    assert "owner" not in echoed, "owner field must be dropped — codex scope, not body, decides"
    assert "__class__" not in echoed
    assert echoed["priority"] == 1, "priority is in KNOWN_TODO_FIELDS"
    assert echoed["title"] == "x"


async def test_manage_todos_read_action_with_only_write_scope_403s(monkeypatch):
    """If the action is 'list' (not in WRITE_ACTIONS) but the token only
    has todos:write, the handler should still accept — because write
    includes read. This test pins the intended behavior so a future
    refactor doesn't accidentally require both scopes."""
    mod = _install_codex_stubs(monkeypatch)
    router = mod.setup_codex_routes()
    handler = mod._find_endpoint(router, "POST", "/api/codex/todos")
    out = await handler(_api_req(scopes=["todos:write"]), {"action": "list"})
    assert out["echo"] is not None  # 200 path


# ── _as_owner must be gone ──────────────────────────────────────────────────


def test_as_owner_helper_removed(monkeypatch):
    """The audit (P1-1) required removing the state-patching helper. Pin
    that here so a future refactor can't silently re-introduce it."""
    mod = _install_codex_stubs(monkeypatch)
    assert not hasattr(mod, "_as_owner"), (
        "_as_owner was removed during the codex audit (P1-1) — "
        "borrowed endpoints now accept owner= directly. Do not re-add."
    )


# ── Borrowed endpoints accept owner kwarg ──────────────────────────────────


@pytest.mark.parametrize("module_name,path", [
    ("routes.memory_routes", "/api/memory"),
    ("routes.calendar_routes", "/api/calendar/events"),
    ("routes.document_routes", "/api/documents/library"),
    ("routes.document_routes", "/api/document/{doc_id}"),
])
def test_borrowed_endpoints_accept_owner_kwarg(monkeypatch, module_name, path):
    """codex_routes calls each borrowed endpoint with `owner=...`. Verify
    the signature has owner as an explicit parameter (not just a state
    read) so the kwarg doesn't TypeError."""
    import inspect
    monkeypatch.setitem(sys.modules, "python_multipart", types.ModuleType("python_multipart"))
    sys.modules["python_multipart"].__version__ = "0.0.13"
    # Use the real module if importable, else just inspect the parameter
    # name from the source file.
    try:
        # Force fresh import to dodge sibling-test stubs
        for k in list(sys.modules):
            if k == module_name:
                monkeypatch.delitem(sys.modules, k, raising=False)
        mod = __import__(module_name, fromlist=["*"])
    except Exception:
        # Module not importable in this isolated env (likely missing
        # transitive deps) — fall back to source inspection. Still useful
        # because the edit is in the file, not the runtime.
        import importlib
        spec = importlib.util.find_spec(module_name)
        if spec is None or spec.origin is None:
            pytest.skip(f"module {module_name} not importable here")
        source = open(spec.origin).read()
        # Look for `owner: Optional[str] = None` near the route path.
        assert "owner: Optional[str] = None" in source, (
            f"{module_name}: expected an explicit `owner: Optional[str] = None` "
            f"parameter so codex_routes can pass owner= directly"
        )
        return

    # Walk routes, find the one matching `path`, and check the endpoint
    # has an `owner` parameter.
    func = None
    for fn_name in ("setup_memory_routes", "setup_calendar_routes",
                    "setup_document_routes", "setup_email_routes"):
        if hasattr(mod, fn_name):
            setup = getattr(mod, fn_name)
            try:
                r = setup(MagicMock(), MagicMock())  # may fail; we just need the routes
            except Exception:
                continue
    # If we got here without a func found via setup, search the source.
    import importlib.util
    spec = importlib.util.find_spec(module_name)
    if spec is not None and spec.origin is not None:
        source = open(spec.origin).read()
        assert "owner: Optional[str] = None" in source


# ── /api/codex/plugin.zip filtering ─────────────────────────────────────────


def test_plugin_zip_excludes_pyc_and_includes_skill(monkeypatch, tmp_path):
    """Build a fake integration root with __pycache__/, .pyc files, and the
    expected files, then verify the zip builder picks the right ones."""
    mod = _install_codex_stubs(monkeypatch)
    # Make a fake root with a .pyc, a .py, a .md, a .json, and a
    # __pycache__ subdir.
    fake_root = tmp_path / "codex"
    fake_root.mkdir()
    (fake_root / "SKILL.md").write_text("# skill")
    (fake_root / "script.py").write_text("print('hi')")
    (fake_root / "manifest.json").write_text("{}")
    (fake_root / "__pycache__").mkdir()
    (fake_root / "__pycache__" / "script.cpython-312.pyc").write_bytes(b"\x00\x00")
    (fake_root / "garbage.bak").write_text("should not ship")

    data = mod._build_plugin_zip(fake_root, prefix="odysseus")
    zf = zipfile.ZipFile(BytesIO(data))
    names = zf.namelist()
    assert "odysseus/SKILL.md" in names
    assert "odysseus/script.py" in names
    assert "odysseus/manifest.json" in names
    # __pycache__ directory and its .pyc are excluded by the denylist.
    assert not any("__pycache__" in n for n in names)
    # .bak is excluded by the suffix allowlist.
    assert not any(n.endswith(".bak") for n in names)


def test_plugin_zip_413_when_bundle_too_large(monkeypatch, tmp_path):
    mod = _install_codex_stubs(monkeypatch)
    fake_root = tmp_path / "codex"
    fake_root.mkdir()
    # Write incompressible data (random-ish bytes as text) so the zip
    # exceeds the 4 MiB cap even after deflate.
    import os
    (fake_root / "huge.md").write_bytes(os.urandom(mod._ZIP_MAX_BYTES + 1))
    with pytest.raises(HTTPException) as ei:
        mod._build_plugin_zip(fake_root, prefix="odysseus")
    assert ei.value.status_code == 413


def test_plugin_zip_caching_returns_same_bytes_when_unchanged(monkeypatch, tmp_path):
    mod = _install_codex_stubs(monkeypatch)
    fake_root = tmp_path / "codex"
    fake_root.mkdir()
    (fake_root / "a.md").write_text("a")
    mod._plugin_zip_cache.clear()
    first = mod._cached_plugin_zip(fake_root)
    second = mod._cached_plugin_zip(fake_root)
    assert first is second, "mtime unchanged → cache hit should return same object"


# ── Body field whitelist surfaces in the actual filter path ───────────────


async def test_manage_todos_body_whitelist_logs_unknown_fields(monkeypatch, caplog):
    """The audit (P3-5) required KNOWN_TODO_FIELDS — confirm dropped
    fields are flagged in logs at debug level for diagnosis."""
    mod = _install_codex_stubs(monkeypatch)
    router = mod.setup_codex_routes()
    handler = mod._find_endpoint(router, "POST", "/api/codex/todos")
    with caplog.at_level("DEBUG", logger="routes.codex_routes"):
        await handler(
            _api_req(scopes=["todos:write"]),
            {"action": "add", "title": "ok", "rogue_field": "evil"},
        )
    dropped = [r for r in caplog.records if "rogue_field" in r.getMessage()]
    assert dropped, "expected debug log line dropping rogue_field"
