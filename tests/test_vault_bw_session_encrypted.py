"""Regression: BW_SESSION must be stored encrypted in vault.json.

The vault /unlock and /login handlers must pass the raw session token
through api_key_manager.encrypt_api_key() before writing it to
vault.json, when an api_key_manager is provided.
"""

import os
import sys
import json
import types
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

# Stub heavy imports so vault_routes loads in isolation. Track what we
# install so the stubs can be evicted after import — leaving them in
# sys.modules poisons later tests that lazily import the real modules.
_installed_stubs = []
for mod_name, attrs in [
    ("core.database", {"SessionLocal": MagicMock(), "ChatMessage": MagicMock(),
                       "Session": MagicMock(), "Document": MagicMock(),
                       "utcnow_naive": MagicMock()}),
    # SecurityHeadersMiddleware + is_cors_preflight included so core/__init__.py
    # loads cleanly if another test in the same process imports from core.*
    # after this module has registered its stubs.
    ("core.middleware", {"require_admin": MagicMock(), "SecurityHeadersMiddleware": MagicMock(),
                         "is_cors_preflight": MagicMock()}),
    ("core.platform_compat", {"IS_WINDOWS": False,
                               "safe_chmod": MagicMock(return_value=True),
                               "which_tool": MagicMock(return_value="bw")}),
    ("src.audit_log", {"audit_event": MagicMock()}),
    ("src.auth_helpers", {"get_current_user": MagicMock(return_value="admin")}),
]:
    if mod_name not in sys.modules:
        m = types.ModuleType(mod_name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[mod_name] = m
        _installed_stubs.append(mod_name)

import routes.vault_routes as vr  # noqa: E402

# vault_routes has bound its names from the stubs (top-level imports only),
# so evict them — later importers must get the real modules.
for _mod_name in _installed_stubs:
    sys.modules.pop(_mod_name, None)


class _FakeProc:
    def __init__(self, stdout=b"raw-session-token-abc", stderr=b"", rc=0):
        self._out, self._err, self.returncode = stdout, stderr, rc
    async def communicate(self, input=None):
        return self._out, self._err


def make_api_key_manager():
    mgr = MagicMock()
    mgr.encrypt_api_key.side_effect = lambda v: f"ENC:{v}"
    mgr.decrypt_api_key.side_effect = lambda v: v[4:] if v.startswith("ENC:") else v
    return mgr


@pytest.mark.asyncio
async def test_unlock_stores_encrypted_session(tmp_path, monkeypatch):
    vault_file = tmp_path / "vault.json"
    monkeypatch.setattr(vr, "VAULT_FILE", vault_file)
    monkeypatch.setattr(vr, "_find_bw", lambda: "bw")
    monkeypatch.setattr(
        vr.asyncio, "create_subprocess_exec",
        lambda *a, **kw: _make_fake_proc()
    )

    async def _fake_exec(*a, **kw):
        return _FakeProc(stdout=b"raw-session-token-abc")
    monkeypatch.setattr(vr.asyncio, "create_subprocess_exec", _fake_exec)

    api_key_manager = make_api_key_manager()
    router_fn = vr.setup_vault_routes(api_key_manager)

    # Directly call the internal unlock logic
    req = MagicMock()
    req.master_password = "master"
    request = MagicMock()

    # Find the unlock endpoint
    unlock_handler = None
    for route in router_fn.routes:
        if hasattr(route, "path") and route.path == "/api/vault/unlock" and "POST" in route.methods:
            unlock_handler = route.endpoint
            break

    if unlock_handler is None:
        pytest.skip("Could not locate /unlock handler (route introspection failed)")

    result = await unlock_handler(req, request)
    assert result.get("ok") is True

    # Verify the vault.json stores an encrypted value, not the raw token
    cfg = json.loads(vault_file.read_text())
    stored_session = cfg.get("session", "")
    assert stored_session == "ENC:raw-session-token-abc", (
        f"Expected encrypted session in vault.json, got: {stored_session!r}"
    )
    api_key_manager.encrypt_api_key.assert_called_once_with("raw-session-token-abc")


def test_setup_vault_routes_accepts_api_key_manager_kwarg():
    """setup_vault_routes must accept api_key_manager as a parameter without raising."""
    mgr = make_api_key_manager()
    try:
        router = vr.setup_vault_routes(mgr)
    except TypeError as e:
        pytest.fail(f"setup_vault_routes rejected api_key_manager parameter: {e}")


def test_get_session_decrypts_stored_value():
    """_get_session must decrypt the stored value via api_key_manager."""
    mgr = make_api_key_manager()
    cfg = {"session": "ENC:raw-session-token-abc"}
    result = vr._get_session(cfg, mgr)
    assert result == "raw-session-token-abc"


def test_get_session_tolerates_no_manager():
    """_get_session with no manager returns raw value (migration tolerance)."""
    cfg = {"session": "plaintext-session"}
    result = vr._get_session(cfg, None)
    assert result == "plaintext-session"
