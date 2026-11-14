"""Both vault.json save paths must create the file 0o600 before any session
secret is written, and must leave no temp files behind (review of #6263).

Covers the two writers changed by that PR:
- ``routes/vault/vault_routes.py`` — ``_save_config`` (HTTP API)
- ``src/tools/vault.py`` — ``do_vault_unlock`` (agent tool)
"""
import asyncio
import json
import os

import pytest

import routes.vault.vault_routes as vault_routes_mod
import src.tools.vault as vault_tool_mod


def _mode(path) -> int:
    return os.stat(path).st_mode & 0o777


def test_routes_save_config_writes_valid_json_and_leaves_no_tmp(tmp_path, monkeypatch):
    target = tmp_path / "vault.json"
    monkeypatch.setattr(vault_routes_mod, "VAULT_FILE", target)

    vault_routes_mod._save_config({"session": "s3cret"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"session": "s3cret"}
    assert list(tmp_path.glob("vault.json.tmp.*")) == []


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX permission bits are not observable on Windows"
)
def test_routes_save_config_file_is_0600(tmp_path, monkeypatch):
    target = tmp_path / "vault.json"
    monkeypatch.setattr(vault_routes_mod, "VAULT_FILE", target)

    vault_routes_mod._save_config({"session": "s3cret"})

    assert _mode(target) == 0o600


def test_tool_vault_unlock_saves_session_and_leaves_no_tmp(tmp_path, monkeypatch):
    target = tmp_path / "vault.json"
    monkeypatch.setattr(vault_tool_mod, "VAULT_FILE", str(target))

    async def fake_run_bw(args, session=None, input_text=None):
        return ("session-token-123", "", 0)

    monkeypatch.setattr(vault_tool_mod, "_run_bw", fake_run_bw)
    result = asyncio.run(vault_tool_mod.do_vault_unlock('{"master_password": "pw"}'))

    assert result["exit_code"] == 0
    cfg = json.loads(target.read_text(encoding="utf-8"))
    assert cfg["session"] == "session-token-123"
    assert list(tmp_path.glob("vault.json.tmp.*")) == []


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX permission bits are not observable on Windows"
)
def test_tool_vault_unlock_file_is_0600(tmp_path, monkeypatch):
    target = tmp_path / "vault.json"
    monkeypatch.setattr(vault_tool_mod, "VAULT_FILE", str(target))

    async def fake_run_bw(args, session=None, input_text=None):
        return ("session-token-123", "", 0)

    monkeypatch.setattr(vault_tool_mod, "_run_bw", fake_run_bw)
    asyncio.run(vault_tool_mod.do_vault_unlock('{"master_password": "pw"}'))

    assert _mode(target) == 0o600
