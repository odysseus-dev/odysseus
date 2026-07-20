"""Regression: manage_settings can tighten the disabled_tools denylist but
never loosen it. Rationale for the boundary lives on the enable_tool handler
in src/agent_tools/admin_tools.py.
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
    store = {"disabled_tools": ["bash"]}
    _patch_store(monkeypatch, store)

    result = asyncio.run(do_manage_settings(json.dumps({
        "action": "enable_tool", "tool": "shell",
    })))

    # Reports exit 0 but leaves the denylist untouched and points at the admin path.
    assert result.get("exit_code") == 0, result
    assert store["disabled_tools"] == ["bash"], result
    assert "bash" in result.get("disabled", []), result
    assert "admin" in result.get("response", "").lower(), result


def test_enable_tool_cannot_undo_manage_settings_self_disable(monkeypatch):
    # Once manage_settings is disabled it can't remove itself from the denylist.
    store = {"disabled_tools": ["manage_settings"]}
    _patch_store(monkeypatch, store)

    result = asyncio.run(do_manage_settings(json.dumps({
        "action": "enable_tool", "tool": "manage_settings",
    })))

    assert result.get("exit_code") == 0, result
    assert store["disabled_tools"] == ["manage_settings"], result


def test_enable_tool_does_not_re_enable_benign_tool(monkeypatch):
    # The block is uniform: every entry is a deliberate operator choice, not
    # just the dangerous ones (here, image generation kept off for cost).
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


def test_agent_descriptions_do_not_advertise_re_enabling():
    # The served prompt, compact index, and schema describe a one-way toggle:
    # they may name enable_tool only to say it won't loosen the denylist, and
    # must never tell the agent it can turn a disabled tool back on. Keeps the
    # descriptions from drifting back to "toggle on/off" once behavior changed.
    import src.agent_tools  # import first: avoids a tool_schemas circular import
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    from src.agent_loop import TOOL_SECTIONS, _DOMAIN_RULES

    schema_desc = next(
        t["function"]["description"]
        for t in FUNCTION_TOOL_SCHEMAS
        if t["function"]["name"] == "manage_settings"
    )
    surfaces = {
        "schema": schema_desc,
        "index": BUILTIN_TOOL_DESCRIPTIONS["manage_settings"],
        "prompt_section": TOOL_SECTIONS["manage_settings"],
        "domain_rules": "\n".join(_DOMAIN_RULES.values()),
    }
    forbidden = ("tools on/off", "tools on or off", "turn tools on",
                 "enable/disable", "disable/enable", "disable_tool|enable_tool")
    for name, text in surfaces.items():
        low = text.lower()
        for phrase in forbidden:
            assert phrase not in low, f"{name} still advertises enabling: {phrase!r}"
