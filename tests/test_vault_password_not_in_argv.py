"""Pin the vault master-password handling so it never regresses into argv.

`routes.vault_routes._run_bw` launches the Bitwarden CLI with
``asyncio.create_subprocess_exec(bw_path, *args)`` — every element of ``args``
becomes a process argument, which is world-readable through ``ps`` /
``/proc/<pid>/cmdline``. The master password therefore must be handed to ``bw``
via the environment (``--passwordenv BW_PASSWORD``), exactly like the existing
``BW_SESSION`` env passing, and never as a positional argv element.

The /unlock route previously did ``_run_bw(["unlock", req.master_password,
"--raw"])`` — leaking the Bitwarden master password (which decrypts the whole
vault) to any local user for the lifetime of the unlock subprocess.
"""

import os
import json
import re
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importing routes.vault_routes pulls in core.middleware → core/__init__ →
# session_manager, which explodes under the conftest stubs. Stub the heavy
# imports the module needs so we can reach the self-contained _run_bw helper.
if "core.database" not in sys.modules:
    _db = types.ModuleType("core.database")
    for _n in ("SessionLocal", "ChatMessage", "Session", "Document"):
        setattr(_db, _n, MagicMock())
    sys.modules["core.database"] = _db
if "core.middleware" not in sys.modules:
    _mw = types.ModuleType("core.middleware")
    _mw.require_admin = MagicMock()
    sys.modules["core.middleware"] = _mw
if "core.platform_compat" not in sys.modules:
    _pc = types.ModuleType("core.platform_compat")
    _pc.IS_WINDOWS = False
    _pc.safe_chmod = MagicMock()
    _pc.which_tool = MagicMock(return_value="bw")
    sys.modules["core.platform_compat"] = _pc

import routes.vault_routes as vr  # noqa: E402


class _FakeProc:
    def __init__(self, stdout=b"session-key", stderr=b"", rc=0):
        self._out, self._err, self.returncode = stdout, stderr, rc

    async def communicate(self, input=None):
        return self._out, self._err


def _patch_exec(monkeypatch):
    """Capture the argv + env handed to create_subprocess_exec."""
    captured = {}

    async def _fake_exec(*argv, env=None, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = env or {}
        return _FakeProc()

    monkeypatch.setattr(vr, "_find_bw", lambda: "bw")
    monkeypatch.setattr(vr.asyncio, "create_subprocess_exec", _fake_exec)
    return captured


@pytest.mark.asyncio
async def test_run_bw_password_goes_to_env_not_argv(monkeypatch):
    captured = _patch_exec(monkeypatch)
    secret = "correct horse battery staple"
    await vr._run_bw(["unlock", "--passwordenv", "BW_PASSWORD", "--raw"],
                     bw_password=secret)
    # The secret must reach bw through the environment...
    assert captured["env"].get("BW_PASSWORD") == secret
    # ...and must NOT appear anywhere in the argv (which `ps` exposes).
    assert secret not in captured["argv"]
    assert all(secret not in str(a) for a in captured["argv"])


@pytest.mark.asyncio
async def test_run_bw_without_password_does_not_set_env(monkeypatch):
    captured = _patch_exec(monkeypatch)
    await vr._run_bw(["lock"])
    assert "BW_PASSWORD" not in captured["env"]


def test_unlock_handler_uses_passwordenv_not_argv():
    """Source-level guard: the /unlock route must feed the master password via
    --passwordenv / bw_password=, never as a bare positional argv element."""
    src = vr.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    # The old, vulnerable call shape must be gone.
    assert 'req.master_password, "--raw"' not in text
    assert "[\"unlock\", req.master_password" not in text
    # And the secure shape must be present.
    assert "--passwordenv" in text
    assert re.search(r"bw_password\s*=\s*req\.master_password", text)


def test_load_config_ignores_non_object_json(tmp_path, monkeypatch):
    vault_file = tmp_path / "vault.json"
    vault_file.write_text(json.dumps(["not", "a", "config", "object"]), encoding="utf-8")
    monkeypatch.setattr(vr, "VAULT_FILE", vault_file)

    assert vr._load_config() == {}
