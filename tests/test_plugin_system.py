"""Tests for the drop-in plugin system: manifest discovery, setup/teardown
lifecycle, live enable/disable (routes + services), persistence, and isolation
of a broken plugin.

Uses self-contained demo plugins written to a temp dir, so nothing here depends
on Odysseus internals.
"""
import json
import os
import textwrap

import pytest
from fastapi import FastAPI

from src.plugin_system import PluginManager


def _write(pdir, pid, body):
    d = os.path.join(pdir, pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(body))


DEMO = '''
    PLUGIN = {"name": "Demo", "version": "0.2.0", "author": "t",
              "description": "demo", "category": "Test"}
    counters = {"start": 0, "stop": 0}
    def setup(ctx):
        from fastapi import APIRouter
        r = APIRouter()
        @r.get("/api/plugins/demo/ping")
        async def ping():
            return {"ok": True}
        ctx.add_router(r)
        ctx.add_service(start=lambda: counters.__setitem__("start", counters["start"] + 1),
                        stop=lambda: counters.__setitem__("stop", counters["stop"] + 1))
'''

BROKEN = '''
    PLUGIN = {"name": "Broken", "version": "1.0.0"}
    def setup(ctx):
        raise RuntimeError("boom")
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    pdir = tmp_path / "plugins"; pdir.mkdir()
    data = tmp_path / "data"; data.mkdir()
    monkeypatch.setenv("ODYSSEUS_PLUGINS_DIR", str(pdir))
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(data))
    monkeypatch.setenv("DATA_DIR", str(data))
    return str(pdir), str(data)


def _routes(app):
    return [r.path for r in app.router.routes if getattr(r, "path", "").startswith("/api/plugins/demo")]


def test_manifest_read_without_executing(env):
    pdir, _ = env
    _write(pdir, "demo", DEMO)
    mgr = PluginManager(app=FastAPI(), directory=pdir)
    mgr.discover()                       # discovery must NOT import/run the module
    rec = mgr.list()[0]
    assert rec["id"] == "demo" and rec["name"] == "Demo" and rec["version"] == "0.2.0"
    assert rec["status"] == "discovered"


def test_load_mounts_route_and_starts_service(env):
    pdir, _ = env
    _write(pdir, "demo", DEMO)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)
    assert mgr.load_enabled(app) == 1
    assert _routes(app) == ["/api/plugins/demo/ping"]
    assert mgr.list()[0]["status"] == "loaded"


def test_disable_then_enable_toggles_routes_and_services(env):
    pdir, _ = env
    _write(pdir, "demo", DEMO)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)
    mgr.load_enabled(app)
    counters = mgr.records["demo"].module.counters
    assert counters["start"] == 1 and _routes(app)

    mgr.disable("demo")
    assert _routes(app) == [] and counters["stop"] == 1
    assert mgr.list()[0]["enabled"] is False and mgr.list()[0]["status"] == "disabled"

    mgr.enable("demo")
    assert _routes(app) == ["/api/plugins/demo/ping"] and counters["start"] == 2


def test_disabled_state_persists(env):
    pdir, data = env
    _write(pdir, "demo", DEMO)
    mgr = PluginManager(app=FastAPI(), directory=pdir)
    mgr.load_enabled()
    mgr.disable("demo")
    with open(os.path.join(data, "plugins.json"), encoding="utf-8") as f:
        assert json.load(f)["demo"]["enabled"] is False
    # a fresh manager respects the persisted state — does not load it
    app2 = FastAPI()
    mgr2 = PluginManager(app=app2, directory=pdir)
    assert mgr2.load_enabled(app2) == 0
    assert mgr2.list()[0]["enabled"] is False


def test_broken_plugin_is_isolated(env):
    pdir, _ = env
    _write(pdir, "demo", DEMO)
    _write(pdir, "broken", BROKEN)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)
    loaded = mgr.load_enabled(app)            # must not raise
    assert loaded == 1                        # demo loads; broken fails
    by_id = {p["id"]: p for p in mgr.list()}
    assert by_id["demo"]["status"] == "loaded"
    assert by_id["broken"]["status"] == "error" and by_id["broken"]["error"]
    assert _routes(app) == ["/api/plugins/demo/ping"]   # broken left nothing behind


def test_shutdown_all_stops_services(env):
    pdir, _ = env
    _write(pdir, "demo", DEMO)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)
    mgr.load_enabled(app)
    mgr.shutdown_all()
    assert mgr.records["demo"].module.counters["stop"] == 1 and _routes(app) == []


OFF_NAMESPACE = '''
    PLUGIN = {"name": "OffNs", "version": "1.0.0"}
    def setup(ctx):
        from fastapi import APIRouter
        r = APIRouter()
        @r.get("/static/evil")          # auth-exempt prefix → must be rejected
        async def evil(): return {"x": 1}
        ctx.add_router(r)
'''


def test_add_router_rejects_off_namespace_routes(env):
    pdir, _ = env
    _write(pdir, "offns", OFF_NAMESPACE)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)
    assert mgr.load_enabled(app) == 0                       # plugin fails to load
    assert mgr.list()[0]["status"] == "error"
    assert not any(getattr(r, "path", "") == "/static/evil" for r in app.router.routes)


def test_ui_field_sanitized():
    """public()'s `ui.open` must be a same-origin path — blocks javascript:/`//evil`."""
    from src.plugin_system import _safe_ui
    assert _safe_ui({"ui": {"open": "/api/plugins/x/app"}}) == {"open": "/api/plugins/x/app", "label": "Open"}
    assert _safe_ui({"ui": {"open": "/api/x", "label": "Go"}})["label"] == "Go"
    assert _safe_ui({"ui": {"open": "javascript:alert(1)"}}) is None
    assert _safe_ui({"ui": {"open": "//evil.com/x"}}) is None
    assert _safe_ui({"ui": {"open": 123}}) is None
    assert _safe_ui({}) is None
