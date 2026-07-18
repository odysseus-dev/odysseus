"""Regression: the manage_settings agent tool must not re-enable disabled tools.

`disabled_tools` is the admin's only global tool-permission boundary. The
agent-facing manage_settings handler runs with no admin check and its arguments
can be steered by prompt injection, so it must be able to tighten the denylist
(disable_tool) but never loosen it (enable_tool). Re-enabling stays an admin
action in Settings -> Agent Tools (POST /tools, require_admin).
"""
import asyncio
import json

import src.settings as settings_mod
from src.agent_tools.admin_tools import do_manage_settings


def _patch_store(monkeypatch, store):
    monkeypatch.setattr(settings_mod, "load_settings", lambda: dict(store))
    monkeypatch.setattr(settings_mod, "save_settings", lambda s: (store.clear(), store.update(s)))
    monkeypatch.setattr(settings_mod, "get_setting", lambda k, d=None: store.get(k, d))


def test_enable_tool_does_not_re_enable_disabled_bash(monkeypatch):
    # Admin has globally disabled shell/bash.
    store = {"disabled_tools": ["bash"]}
    _patch_store(monkeypatch, store)

    result = asyncio.run(do_manage_settings(json.dumps({
        "action": "enable_tool", "tool": "shell",
    })))

    # The tool reports success (exit 0) but must NOT clear bash from the denylist.
    assert result.get("exit_code") == 0, result
    assert store["disabled_tools"] == ["bash"], result
    assert "bash" in result.get("disabled", []), result
    # It should point the user at the admin path, not silently enable.
    assert "admin" in result.get("response", "").lower(), result


def test_enable_tool_cannot_undo_manage_settings_self_disable(monkeypatch):
    # Self-referential: once manage_settings is disabled, the agent tool cannot
    # remove itself from the denylist to regain the ability to loosen it.
    store = {"disabled_tools": ["manage_settings"]}
    _patch_store(monkeypatch, store)

    result = asyncio.run(do_manage_settings(json.dumps({
        "action": "enable_tool", "tool": "manage_settings",
    })))

    assert result.get("exit_code") == 0, result
    assert store["disabled_tools"] == ["manage_settings"], result


def test_enable_tool_does_not_re_enable_benign_tool(monkeypatch):
    # The block is uniform: disabled_tools encodes a deliberate operator choice
    # for EVERY entry, not just dangerous ones. An operator who disabled image
    # generation (e.g. to control API cost) keeps that choice until they
    # re-enable it themselves in Settings; an injected turn cannot override it.
    store = {"disabled_tools": ["generate_image"]}
    _patch_store(monkeypatch, store)

    result = asyncio.run(do_manage_settings(json.dumps({
        "action": "enable_tool", "tool": "images",
    })))

    assert result.get("exit_code") == 0, result
    assert store["disabled_tools"] == ["generate_image"], result


def test_disable_tool_still_works(monkeypatch):
    # Tightening the denylist stays available from chat.
    store = {"disabled_tools": []}
    _patch_store(monkeypatch, store)

    result = asyncio.run(do_manage_settings(json.dumps({
        "action": "disable_tool", "tool": "shell",
    })))

    assert result.get("exit_code") == 0, result
    assert "bash" in store["disabled_tools"], result
    assert "bash" in result.get("changed", []), result


def test_list_tools_still_works(monkeypatch):
    store = {"disabled_tools": ["bash", "web_search"]}
    _patch_store(monkeypatch, store)

    result = asyncio.run(do_manage_settings(json.dumps({"action": "list_tools"})))

    assert result.get("exit_code") == 0, result
    assert set(result.get("disabled", [])) == {"bash", "web_search"}, result
