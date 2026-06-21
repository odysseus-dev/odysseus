"""Integration tests for the profile API routes.

Tests:
- GET /api/profiles returns list of three profiles
- GET /api/profiles/max returns MAX profile
- GET /api/profiles/daily returns DAILY profile
- GET /api/profiles/custom returns CUSTOM profile
- GET /api/profiles/unknown returns 404
- PUT /api/profiles/custom persists and returns merged profile
"""

import sys
import types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch, tmp_path):
    """Minimal FastAPI TestClient with the profile route and a patched profiles module."""

    # Stub heavy optional deps before any import touches them.
    for mod_name in ["chromadb", "chromadb_client", "fastembed", "numpy",
                     "bcrypt", "cryptography", "faster_whisper"]:
        if mod_name not in sys.modules:
            monkeypatch.setitem(sys.modules, mod_name, types.ModuleType(mod_name))

    # Redirect DATA_DIR so tests never touch the real data directory.
    constants_mod = types.ModuleType("core.constants")
    constants_mod.DATA_DIR = str(tmp_path)
    monkeypatch.setitem(sys.modules, "core.constants", constants_mod)

    # Force fresh import of the profiles module with the patched DATA_DIR.
    sys.modules.pop("services.profiles.profiles", None)
    sys.modules.pop("services.profiles", None)

    from fastapi import FastAPI
    from routes.profile_routes import setup_profile_routes

    mini_app = FastAPI()
    mini_app.include_router(setup_profile_routes())
    return TestClient(mini_app, raise_server_exceptions=True)


# ── GET /api/profiles ──────────────────────────────────────────────────────────

def test_list_profiles_returns_200(client):
    """GET /api/profiles returns HTTP 200."""
    r = client.get("/api/profiles")
    assert r.status_code == 200


def test_list_profiles_returns_three_items(client):
    """GET /api/profiles returns exactly three profiles."""
    data = client.get("/api/profiles").json()
    assert isinstance(data, list)
    assert len(data) == 3


def test_list_profiles_keys_in_order(client):
    """GET /api/profiles returns [max, daily, custom] in that order."""
    data = client.get("/api/profiles").json()
    assert [p["key"] for p in data] == ["max", "daily", "custom"]


# ── GET /api/profiles/{key} ───────────────────────────────────────────────────

def test_get_max_profile(client):
    """GET /api/profiles/max returns MAX profile with correct ctx_size."""
    r = client.get("/api/profiles/max")
    assert r.status_code == 200
    data = r.json()
    assert data["key"] == "max"
    assert data["ctx_size"] == 16384
    assert data["is_builtin"] is True


def test_get_daily_profile(client):
    """GET /api/profiles/daily returns DAILY profile with correct ctx_size."""
    r = client.get("/api/profiles/daily")
    assert r.status_code == 200
    data = r.json()
    assert data["key"] == "daily"
    assert data["ctx_size"] == 4096


def test_get_custom_profile(client):
    """GET /api/profiles/custom returns CUSTOM profile."""
    r = client.get("/api/profiles/custom")
    assert r.status_code == 200
    data = r.json()
    assert data["key"] == "custom"
    assert data["is_builtin"] is False


def test_get_unknown_profile_returns_404(client):
    """GET /api/profiles/unknown returns HTTP 404."""
    r = client.get("/api/profiles/unknown")
    assert r.status_code == 404


# ── PUT /api/profiles/custom ──────────────────────────────────────────────────

def test_save_custom_profile(client):
    """PUT /api/profiles/custom persists overrides and returns merged profile."""
    payload = {"ctx_size": 12288, "label": "My Preset"}
    r = client.put("/api/profiles/custom", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["ctx_size"] == 12288
    assert data["label"] == "My Preset"
    assert data["key"] == "custom"
    assert data["is_builtin"] is False


def test_save_custom_persists_across_requests(client):
    """PUT then GET /api/profiles/custom returns the saved ctx_size."""
    client.put("/api/profiles/custom", json={"ctx_size": 10240})
    r = client.get("/api/profiles/custom")
    assert r.json()["ctx_size"] == 10240


def test_save_custom_strips_builtin_flag(client):
    """PUT /api/profiles/custom ignores is_builtin=True from the request body."""
    r = client.put("/api/profiles/custom", json={"is_builtin": True, "key": "max"})
    assert r.status_code == 200
    data = r.json()
    assert data["is_builtin"] is False
    assert data["key"] == "custom"


# ── Profile chip → serve command parameter mapping (integration) ─────────────
# These tests verify the values that cookbookServe.js reads when a profile
# chip is clicked. The chip fills ctx_size and flash_attn into the serve form;
# the serve command builder then includes --ctx-size and flash-attn flags.

def test_max_profile_enables_reasoning(client):
    """MAX profile enables the reasoning feature so the serve command includes it."""
    data = client.get("/api/profiles/max").json()
    assert data["features"]["reasoning"] is True


def test_daily_profile_disables_reasoning(client):
    """DAILY profile disables reasoning so --reasoning is absent from the serve command."""
    data = client.get("/api/profiles/daily").json()
    assert data["features"]["reasoning"] is False


def test_max_profile_ctx_larger_than_daily(client):
    """MAX ctx_size exceeds DAILY ctx_size — the serve command produces a larger --ctx-size."""
    max_ctx = client.get("/api/profiles/max").json()["ctx_size"]
    daily_ctx = client.get("/api/profiles/daily").json()["ctx_size"]
    assert max_ctx > daily_ctx


def test_all_profiles_have_flash_attn_field(client):
    """All profiles expose flash_attn so the serve form checkbox can be set."""
    for key in ("max", "daily", "custom"):
        data = client.get(f"/api/profiles/{key}").json()
        assert "flash_attn" in data, f"profile {key!r} missing flash_attn"


def test_profiles_have_ttft_estimate(client):
    """All profiles include a TTFT estimate string for the chip tooltip."""
    for key in ("max", "daily", "custom"):
        data = client.get(f"/api/profiles/{key}").json()
        assert data.get("ttft_estimate"), f"profile {key!r} missing ttft_estimate"
