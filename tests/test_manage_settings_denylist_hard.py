"""The global disabled_tools denylist must be a hard boundary for the agent.

``manage_settings`` used to expose ``enable_tool``, which let any agent turn
(admin-run, but reading arbitrary web pages, emails, and documents) remove
entries from ``settings.json:disabled_tools`` — the only global tool
permission mechanism the Settings > Agent Tools panel offers. A prompt
injection could therefore re-enable bash, email, or any other tool an admin
had switched off. Regression for issue #5523: the agent may still narrow the
denylist (``disable_tool``) and inspect it (``list_tools``), but widening it
is refused and left to a human in the Settings panel.
"""

import asyncio
import json

import pytest

import src.settings as settings_mod
from src.agent_tools.admin_tools import do_manage_settings


@pytest.fixture
def store(monkeypatch):
    state = {"disabled_tools": ["bash", "manage_memory", "web_search", "web_fetch"]}
    monkeypatch.setattr(settings_mod, "load_settings", lambda: dict(state))

    def _save(s):
        state.clear()
        state.update(s)

    monkeypatch.setattr(settings_mod, "save_settings", _save)
    monkeypatch.setattr(
        settings_mod, "get_setting",
        lambda key, default=None: state.get(key, default),
    )
    return state


def _call(payload, owner="admin"):
    return asyncio.run(do_manage_settings(json.dumps(payload), owner=owner))


@pytest.mark.parametrize("tool", ["shell", "bash", "memory", "manage_memory", "search"])
def test_enable_tool_is_refused_and_denylist_unchanged(store, tool):
    before = list(store["disabled_tools"])
    result = _call({"action": "enable_tool", "tool": tool})
    assert result["exit_code"] == 1, result
    assert "Settings" in result["error"]
    assert store["disabled_tools"] == before


def test_enable_tool_refused_without_tool_name(store):
    before = list(store["disabled_tools"])
    result = _call({"action": "enable_tool"})
    assert result["exit_code"] == 1, result
    assert store["disabled_tools"] == before


def test_enable_tool_refused_via_name_alias_argument(store):
    # The toggle branch also accepted `name` as the tool argument.
    before = list(store["disabled_tools"])
    result = _call({"action": "enable_tool", "name": "shell"})
    assert result["exit_code"] == 1, result
    assert store["disabled_tools"] == before


def test_disable_tool_still_narrows_the_denylist(store):
    result = _call({"action": "disable_tool", "tool": "images"})
    assert result["exit_code"] == 0, result
    assert "generate_image" in store["disabled_tools"]
    # Nothing that was disabled before is re-enabled as a side effect.
    for name in ("bash", "manage_memory", "web_search", "web_fetch"):
        assert name in store["disabled_tools"]


def test_list_tools_still_reports_the_denylist(store):
    result = _call({"action": "list_tools"})
    assert result["exit_code"] == 0, result
    assert set(result["disabled"]) == {"bash", "manage_memory", "web_search", "web_fetch"}


@pytest.mark.parametrize("action", ["set", "delete", "reset"])
def test_disabled_tools_key_is_not_reachable_through_settings_actions(store, action):
    # Belt and braces: the free-form settings actions must not offer a second
    # path to the denylist (a `reset` would restore the default empty list).
    before = list(store["disabled_tools"])
    payload = {"action": action, "key": "disabled_tools"}
    if action == "set":
        payload["value"] = []
    result = _call(payload)
    assert result["exit_code"] == 1, result
    assert store["disabled_tools"] == before


def test_schema_no_longer_advertises_enable_tool():
    import src.agent_tools  # noqa: F401  (resolves the schema import cycle)
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    schema = next(
        t["function"] for t in FUNCTION_TOOL_SCHEMAS
        if (t.get("function") or {}).get("name") == "manage_settings"
    )
    actions = schema["parameters"]["properties"]["action"]["enum"]
    assert "enable_tool" not in actions
    assert "disable_tool" in actions and "list_tools" in actions
    assert "enable_tool" not in schema["description"]
