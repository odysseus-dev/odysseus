"""Integration tests for GET /api/telemetry.

Tests:
- 403 when telemetry_enabled=False
- 200 + valid JSON snapshot when telemetry_enabled=True (all required keys present)

Throttle threshold correctness (temp 86 vs 87) is covered by
test_telemetry_sampler.py, which exercises the sampler directly without
needing to patch unexposed module attributes.
"""

import sys
import types
import pytest
from fastapi.testclient import TestClient


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def app_client(monkeypatch):
    """Minimal FastAPI TestClient with the telemetry route and a fake sampler."""
    # Stub heavy optional deps before any import touches them.
    for mod_name in ["pynvml", "psutil", "chromadb", "chromadb_client",
                     "fastembed", "numpy", "bcrypt", "cryptography",
                     "faster_whisper"]:
        if mod_name not in sys.modules:
            monkeypatch.setitem(sys.modules, mod_name, types.ModuleType(mod_name))

    # Provide a minimal settings module so the route can call get_setting().
    _settings_store = {"telemetry_enabled": False}

    settings_mod = types.ModuleType("src.settings")
    settings_mod.get_setting = lambda key, default=None: _settings_store.get(key, default)
    monkeypatch.setitem(sys.modules, "src.settings", settings_mod)

    # Provide a fake sampler singleton.
    _fake_snap = {
        "timestamp": 1234567890.0,
        "cpu_pct": 42.0,
        "ram_gb": 7.5,
        "ram_pct": 55.0,
        "vram_gb": 4.2,
        "gpu_pct": 70,
        "gpu_temp_c": 80,
        "throttle": False,
    }

    class _FakeSampler:
        def get_latest(self):
            return dict(_fake_snap)

    sampler_mod = types.ModuleType("services.telemetry.sampler")
    sampler_mod.get_sampler = lambda: _FakeSampler()
    monkeypatch.setitem(sys.modules, "services.telemetry.sampler", sampler_mod)
    monkeypatch.setitem(sys.modules, "services.telemetry", types.ModuleType("services.telemetry"))

    # Build a minimal FastAPI app with only the telemetry route.
    from fastapi import FastAPI
    from routes.telemetry_routes import setup_telemetry_routes

    mini_app = FastAPI()
    mini_app.include_router(setup_telemetry_routes())

    return TestClient(mini_app, raise_server_exceptions=True), _settings_store, _fake_snap


# ── tests ─────────────────────────────────────────────────────────────────────

def test_telemetry_disabled_returns_403(app_client):
    """GET /api/telemetry returns 403 when telemetry_enabled=False."""
    client, store, _ = app_client
    store["telemetry_enabled"] = False
    r = client.get("/api/telemetry")
    assert r.status_code == 403
    assert "telemetry_disabled" in r.text


def test_telemetry_enabled_returns_snapshot(app_client):
    """GET /api/telemetry returns 200 and a valid JSON snapshot when enabled."""
    client, store, snap = app_client
    store["telemetry_enabled"] = True
    r = client.get("/api/telemetry")
    assert r.status_code == 200
    data = r.json()
    for key in ("timestamp", "cpu_pct", "ram_gb", "ram_pct", "vram_gb", "gpu_pct", "gpu_temp_c", "throttle"):
        assert key in data, f"Missing key in response: {key}"
    assert data["cpu_pct"] == pytest.approx(42.0)
    assert data["vram_gb"] == pytest.approx(4.2)
    assert data["throttle"] is False
