"""Regression coverage for #4512: agents must not enable shell access.

``ui_control`` is model-callable. Before this guard, a model could emit
``ui_control toggle shell on``, the frontend flipped the user's Shell Access
toggle, and the next turn sent ``allow_bash=true``.
"""

import asyncio
import json
from pathlib import Path

import pytest

from src.ai_interaction import dispatch_ai_tool, do_ui_control
from src.tool_schemas import function_call_to_tool_block

_STATIC_JS = Path(__file__).resolve().parents[1] / "static" / "js"


@pytest.mark.parametrize(
    "name",
    ["shell", "bash", "terminal", "BASH", "Shell", " Terminal "],
)
@pytest.mark.parametrize(
    "value",
    ["on", "true", "1", "yes", "enable", "enabled", "ON", "Yes"],
)
def test_agent_cannot_enable_shell(name, value):
    result = asyncio.run(do_ui_control(f"toggle {name} {value}"))

    assert result.get("blocked") is True
    assert result.get("failure_kind") == "user_only_action"
    assert "can only be enabled by the user" in result["error"]
    assert "Shell Access button" in result["error"]
    # No ui_event/state means the frontend never applies the toggle.
    assert "ui_event" not in result
    assert "state" not in result


def test_agent_can_still_disable_shell():
    result = asyncio.run(do_ui_control("toggle shell off"))

    assert result.get("ui_event") == "toggle"
    assert result.get("toggle_name") == "bash"
    assert result.get("state") is False
    assert result.get("results") == "Toggle 'bash' set to off"
    assert "error" not in result


def test_extra_token_after_enable_value_fails_closed():
    # split(None, 2) keeps "on please" as one token; it is not a recognized
    # enable spelling, so the command falls back to off rather than enabling.
    result = asyncio.run(do_ui_control("toggle bash on please"))

    assert result.get("ui_event") == "toggle"
    assert result.get("toggle_name") == "bash"
    assert result.get("state") is False


def test_non_shell_toggles_still_enable():
    for name in ("web", "rag", "research", "incognito", "document_editor"):
        result = asyncio.run(do_ui_control(f"toggle {name} on"))

        assert result.get("ui_event") == "toggle", name
        assert result.get("toggle_name") == name
        assert result.get("state") is True
        assert "error" not in result


def test_native_shell_enable_call_is_blocked_before_ui_event():
    block = function_call_to_tool_block(
        "ui_control",
        json.dumps({"action": "toggle", "name": "shell", "value": "on"}),
    )
    assert block.content == "toggle shell on"

    _description, result = asyncio.run(dispatch_ai_tool("ui_control", block.content))

    assert result.get("blocked") is True
    assert "ui_event" not in result


def test_get_toggles_explains_shell_is_user_enabled():
    result = asyncio.run(do_ui_control("get_toggles"))

    text = result["results"].lower()
    assert "shell access (bash) can only be turned off by the agent" in text
    assert "user enables it" in text


def test_frontend_handler_uses_the_shared_toggle_guard():
    source = (_STATIC_JS / "chatStream.js").read_text(encoding="utf-8")

    assert "isBlockedAgentToggle(uiData)" in source
    assert "canonicalToggleName(uiData.toggle_name)" in source
    assert "Storage.setToolToggle(toggleName, toggleState)" in source
