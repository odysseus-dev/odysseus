"""Tests for OXS admin REST API (using FastAPI TestClient)."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

# Use FastAPI's TestClient which handles cookie/session auth for us.
# We mount the OXS admin router onto a fresh FastAPI app per test so
# the only auth context we need is the per-test user session.
from fastapi import Cookie, FastAPI, HTTPException, Depends  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from core.extensions import ExtensionRegistry  # noqa: E402
from routes.oxs_api import setup_oxs_api_routes  # noqa: E402

# Ensure DB tables exist before any audit mirror writes. Other test
# files monkey-patch ``sys.modules["core.database"]`` and may leave a
# stub in place by the time we run. We force a fresh import of the
# real module here so we always bind to the actual engine that owns
# the in-memory DB and its tables.
import importlib as _il
import sys as _sys
_real_db = _il.import_module("core.database")
# Re-resolve names that other tests may have stubbed in sys.modules.
SessionLocal = _real_db.SessionLocal
Extension = _real_db.Extension
ExtensionAudit = _real_db.ExtensionAudit
engine = _real_db.engine
init_db = _real_db.init_db

init_db()

# Force the audit module to use the same SessionLocal we just bound.
# This avoids the audit log's lazy factory picking up a stubbed
# ``core.database`` from sys.modules.
from core.extensions import audit as _oxs_audit
from sqlalchemy import inspect as _oxs_inspect
_oxs_audit.set_sink(_oxs_audit.AuditSink(
    log_path=_oxs_audit.OXS_AUDIT_LOG,
    db_session_factory=SessionLocal,
))

# Switch the engine's pool to StaticPool so every sessionmaker session
# shares the SAME in-memory DB connection (without this, SQLite
# :memory: gets a fresh DB per connection and cross-thread writes
# from FastAPI's worker thread miss the tables we created in the
# main thread).
from sqlalchemy.pool import StaticPool as _StaticPool
# Replace the existing engine with one that uses StaticPool. We must
# recreate all the dependent objects (SessionLocal uses the engine via
# its bind).
new_engine = create_engine(
    "sqlite:///:memory:",
    future=True,
    connect_args={"check_same_thread": False},
    poolclass=_StaticPool,
)
_real_db.engine = new_engine
SessionLocal.configure(bind=new_engine)
init_db()

# Sanity: verify the SessionLocal points to the engine with tables.
_test_session = SessionLocal()
_test_engine = _test_session.get_bind()
_test_session.close()
print("DEBUG _oxs_init_db complete. tables on bound engine:", sorted(_oxs_inspect(_test_engine).get_table_names())[:5])
print("DEBUG SessionLocal id:", id(SessionLocal), "engine id:", id(_test_engine))


# ── Auth shim ────────────────────────────────────────────────────────────
#
# We replace ``require_admin`` for tests with a permissive version that
# trusts a ``X-Oxs-Operator`` header. The real ``require_admin`` lives
# in core/middleware.py and depends on auth_manager state we don't want
# to set up here.


def _fake_admin(request):
    op = request.headers.get("X-Oxs-Operator")
    if not op:
        raise HTTPException(403, "X-Oxs-Operator header required for tests")
    return op


@pytest.fixture
def tmp_root():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture(autouse=True)
def _reset_oxs_state():
    """Reset OXS module-level singletons between tests so state doesn't leak."""
    from core.extensions import events, webhooks, ui_mount
    events._subscribers.clear()
    webhooks.reset_for_tests()
    webhooks.reset_rate_limits()
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO
    for k in [k for k in BUILTIN_ACTIONS if k.startswith("oxs_")]:
        del BUILTIN_ACTIONS[k]
    for k in [k for k in BUILTIN_ACTION_INFO if k.startswith("oxs_")]:
        del BUILTIN_ACTION_INFO[k]
    from src.mcp_manager import get_mcp_manager as McpManager
    m = McpManager()
    for sid in list(m._in_process_tools.keys()):
        m.unregister_in_process_tools(sid)
    ui_mount.reset_for_tests()
    yield
    # cleanup again
    events._subscribers.clear()
    webhooks.reset_for_tests()
    webhooks.reset_rate_limits()


@pytest.fixture
def registry(tmp_root):
    return ExtensionRegistry(
        bundled_dir=os.path.join(tmp_root, "bundled"),
        user_dir=os.path.join(tmp_root, "user"),
    )


@pytest.fixture
def app_and_client(registry):
    """Build a FastAPI app with the OXS router and a permissive admin check."""
    import routes.oxs_api as oxs_api_mod
    real_require_admin = oxs_api_mod._require_admin
    oxs_api_mod._require_admin = _fake_admin
    router = setup_oxs_api_routes(registry)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    yield app, client
    # Restore so the next test gets the patched version again (idempotent).
    oxs_api_mod._require_admin = real_require_admin


def _hdrs(op: str = "admin") -> dict:
    return {"X-Oxs-Operator": op}


# ── helpers ─────────────────────────────────────────────────────────────


def _install_user_ext(registry, tmp_root, ext_id, plugin_src="def register(host): pass"):
    sub = os.path.join(tmp_root, "user", ext_id)
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "plugin.py"), "w") as f:
        f.write(plugin_src)
    manifest = {
        "id": ext_id,
        "name": ext_id,
        "version": "1.0.0",
        "oxs_version": ">=1.0.0",
        "entry_point": "plugin:register",
        "capabilities": {},
        "file_hashes": {},
    }
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump(manifest, f)
    registry.rescan()


# ── /health ────────────────────────────────────────────────────────────


def test_health(app_and_client):
    _app, client = app_and_client
    r = client.get("/api/oxs/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ─- /extensions requires operator header ────────────────────────────────


def test_list_extensions_requires_operator(app_and_client):
    _app, client = app_and_client
    r = client.get("/api/oxs/extensions")
    assert r.status_code == 403


def test_list_extensions_returns_empty(app_and_client):
    _app, client = app_and_client
    r = client.get("/api/oxs/extensions", headers=_hdrs())
    assert r.status_code == 200
    assert r.json() == {"extensions": []}


def test_list_extensions_returns_installed(tmp_root, registry, app_and_client):
    _install_user_ext(registry, tmp_root, "ext1")
    _app, client = app_and_client
    r = client.get("/api/oxs/extensions", headers=_hdrs())
    assert r.status_code == 200
    body = r.json()
    assert len(body["extensions"]) == 1
    assert body["extensions"][0]["id"] == "ext1"


def test_get_unknown_extension_returns_404(tmp_root, registry, app_and_client):
    _install_user_ext(registry, tmp_root, "ext1")
    _app, client = app_and_client
    r = client.get("/api/oxs/extensions/nope", headers=_hdrs())
    assert r.status_code == 404


# ── Full enable / disable cycle ──────────────────────────────────────────


def test_full_lifecycle(tmp_root, registry, app_and_client):
    _install_user_ext(registry, tmp_root, "ext1")
    _app, client = app_and_client

    # enable
    r = client.post("/api/oxs/extensions/ext1/enable", headers=_hdrs())
    assert r.status_code == 200
    assert r.json()["is_enabled"] is True

    # verify state
    r = client.get("/api/oxs/extensions/ext1", headers=_hdrs())
    assert r.json()["is_enabled"] is True

    # disable
    r = client.post("/api/oxs/extensions/ext1/disable", headers=_hdrs())
    assert r.status_code == 200
    assert r.json()["is_enabled"] is False

    # uninstall
    r = client.post("/api/oxs/extensions/ext1/uninstall", headers=_hdrs())
    assert r.status_code == 200
    r = client.get("/api/oxs/extensions", headers=_hdrs())
    assert r.json() == {"extensions": []}


def test_enable_unknown_returns_404(tmp_root, registry, app_and_client):
    _app, client = app_and_client
    r = client.post("/api/oxs/extensions/nope/enable", headers=_hdrs())
    assert r.status_code == 404


def test_reload_action(tmp_root, registry, app_and_client):
    _install_user_ext(registry, tmp_root, "ext1")
    _app, client = app_and_client
    client.post("/api/oxs/extensions/ext1/enable", headers=_hdrs())
    r = client.post("/api/oxs/extensions/ext1/reload", headers=_hdrs())
    assert r.status_code == 200
    assert r.json()["is_enabled"] is True


def test_rescan_finds_newly_added(tmp_root, registry, app_and_client):
    _app, client = app_and_client
    assert client.get("/api/oxs/extensions", headers=_hdrs()).json() == {"extensions": []}
    _install_user_ext(registry, tmp_root, "ext_a")
    r = client.post("/api/oxs/rescan", headers=_hdrs())
    assert r.status_code == 200
    assert r.json() == {"ok": True, "count": 1}


# ─- Summary shape ────────────────────────────────────────────────────────


def test_summary_includes_capabilities(tmp_root, registry, app_and_client):
    """When the manifest declares capabilities, summary lists them."""
    # Write the manifest with capabilities from the start.
    sub = os.path.join(tmp_root, "user", "ext1")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "plugin.py"), "w") as f:
        f.write("def register(host): pass")
    manifest = {
        "id": "ext1",
        "name": "ext1",
        "version": "1.0.0",
        "oxs_version": ">=1.0.0",
        "entry_point": "plugin:register",
        "capabilities": {"tools": True, "static": True, "events_sub": True},
        "file_hashes": {},
    }
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump(manifest, f)
    registry.rescan()

    _app, client = app_and_client
    r = client.get("/api/oxs/extensions/ext1", headers=_hdrs())
    body = r.json()
    assert "tools" in body["capabilities"]
    assert "static" in body["capabilities"]
    assert "events_sub" in body["capabilities"]
    assert "stats" in body
    assert "tools" in body["stats"]


# ─- Audit ───────────────────────────────────────────────────────────────


def test_global_audit_returns_events(tmp_root, registry, app_and_client):
    from sqlalchemy import inspect as _inspect
    from core.database import engine as _engine
    print("DEBUG sink:", _oxs_audit._DEFAULT_SINK)
    print("DEBUG tables:", sorted(_inspect(_engine).get_table_names()))
    print("DEBUG audit_query:", audit_query_direct())
    _install_user_ext(registry, tmp_root, "ext1")
    _app, client = app_and_client
    print("DEBUG after install:", audit_query_direct())
    print("DEBUG enabled:", registry.get('ext1').enabled)
    # Direct write to see if path works at all from main thread
    _oxs_audit.audit_log("test.main_thread", extension_id="ext1", actor="main")
    print("DEBUG after direct audit_log:", audit_query_direct())
    r = client.post("/api/oxs/extensions/ext1/enable", headers=_hdrs())
    print("DEBUG enable response:", r.status_code, r.json())
    print("DEBUG after enable:", audit_query_direct())
    r = client.get("/api/oxs/audit", headers=_hdrs())
    assert r.status_code == 200
    events = r.json()["events"]
    assert isinstance(events, list)
    assert len(events) >= 1


def audit_query_direct():
    from core.extensions.audit import audit_query
    return audit_query(limit=10)


def test_extension_audit_filters_by_id(tmp_root, registry, app_and_client):
    _install_user_ext(registry, tmp_root, "ext1")
    _app, client = app_and_client
    client.post("/api/oxs/extensions/ext1/enable", headers=_hdrs())
    r = client.get("/api/oxs/extensions/ext1/audit", headers=_hdrs())
    events = r.json()["events"]
    for e in events:
        assert e["extension_id"] == "ext1"


# ─- Webhook listing ─────────────────────────────────────────────────────


def test_list_webhooks_empty(tmp_root, registry, app_and_client):
    _install_user_ext(registry, tmp_root, "ext1")
    _app, client = app_and_client
    r = client.get("/api/oxs/extensions/ext1/webhooks", headers=_hdrs())
    assert r.status_code == 200
    assert r.json() == {"webhooks": []}


def test_list_webhooks_after_install(tmp_root, registry, app_and_client):
    from core.extensions import webhooks as _oxs_wh
    plugin = (
        "def register(host):\n"
        "    @host.on_enable\n"
        "    def _e():\n"
        "        host.register_webhook('/wh/{token}', ['messages.upsert'], lambda **kw: None)\n"
    )
    sub = os.path.join(tmp_root, "user", "ext1")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "plugin.py"), "w") as f:
        f.write(plugin)
    manifest = {
        "id": "ext1",
        "name": "ext1",
        "version": "1.0.0",
        "oxs_version": ">=1.0.0",
        "entry_point": "plugin:register",
        "capabilities": {"webhooks_in": True},
        "file_hashes": {},
    }
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump(manifest, f)
    registry.rescan()

    _app, client = app_and_client
    print("DEBUG webhooks before enable:", _oxs_wh.list_for_extension("ext1"))
    client.post("/api/oxs/extensions/ext1/enable", headers=_hdrs())
    print("DEBUG webhooks after enable:", _oxs_wh.list_for_extension("ext1"))
    r = client.get("/api/oxs/extensions/ext1/webhooks", headers=_hdrs())
    body = r.json()
    assert len(body["webhooks"]) == 1
    wh = body["webhooks"][0]
    assert wh["path_suffix"] == "/wh/{token}"
    assert wh["event_filter"] == ["messages.upsert"]
    assert wh["raw_token"]
    assert isinstance(wh["raw_token"], str)
    assert len(wh["raw_token"]) >= 32


def test_webhook_endpoint_404s_unknown_extension(registry, app_and_client):
    _app, client = app_and_client
    r = client.get("/api/oxs/extensions/nope/webhooks", headers=_hdrs())
    assert r.status_code == 404


# ─- Webhook delivery end-to-end ─────────────────────────────────────────


def test_inbound_webhook_round_trip(tmp_root, registry, app_and_client):
    """Install an extension with a webhook, dispatch via the dispatcher,
    confirm the side effect happened."""
    out_path = os.path.join(tmp_root, "webhook.log")
    plugin = (
        "import json\n"
        "def _on(body=None, **kw):\n"
        "    with open(r'" + out_path + "', 'a') as f:\n"
        "        f.write(json.dumps(body) + chr(10))\n"
        "def register(host):\n"
        "    @host.on_enable\n"
        "    def _e():\n"
        "        host.register_webhook('/wh/{token}', [], _on)\n"
    )
    sub = os.path.join(tmp_root, "user", "ext1")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "plugin.py"), "w") as f:
        f.write(plugin)
    manifest = {
        "id": "ext1",
        "name": "ext1",
        "version": "1.0.0",
        "oxs_version": ">=1.0.0",
        "entry_point": "plugin:register",
        "capabilities": {"webhooks_in": True},
        "file_hashes": {},
    }
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump(manifest, f)
    registry.rescan()
    _app, client = app_and_client
    client.post("/api/oxs/extensions/ext1/enable", headers=_hdrs())

    # Get the raw token
    body = client.get("/api/oxs/extensions/ext1/webhooks", headers=_hdrs()).json()
    token = body["webhooks"][0]["raw_token"]

    # Dispatch via the dispatcher
    import asyncio
    from core.extensions.webhooks import dispatch
    status, _ = asyncio.run(dispatch("ext1", token, b'{"hello": "world"}'))
    assert status == 202
    asyncio.run(asyncio.sleep(0.05))
    assert os.path.exists(out_path)
    assert '"hello": "world"' in open(out_path).read()