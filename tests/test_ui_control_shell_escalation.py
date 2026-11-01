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


@pytest.mark.parametrize("name", ["shell", "bash", "terminal"])
@pytest.mark.parametrize("value", ["on", "true", "1", "yes", "enable", "enabled"])
def test_agent_cannot_enable_shell(name, value):
    result = asyncio.run(do_ui_control(f"toggle {name} {value}"))

    assert result.get("blocked") is True
    assert "error" in result
    assert "user" in result["error"].lower()
    assert "Shell Access" in result["error"]
    # No ui_event/state means the frontend never applies the toggle.
    assert "ui_event" not in result
    assert "state" not in result


def test_agent_can_still_disable_shell():
    result = asyncio.run(do_ui_control("toggle shell off"))

    assert result == {
        "ui_event": "toggle",
        "toggle_name": "bash",
        "state": False,
        "results": "Toggle 'bash' set to off",
    }


def test_non_shell_toggles_still_enable():
    result = asyncio.run(do_ui_control("toggle web on"))

    assert result.get("ui_event") == "toggle"
    assert result.get("toggle_name") == "web"
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


def test_frontend_ignores_agent_shell_enable_event():
    source = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "chatStream.js"
    ).read_text(encoding="utf-8")

    guard = source.index(
        "if (uiData.state && USER_ONLY_ENABLE_TOGGLES.has(uiData.toggle_name))"
    )
    checkbox = source.index("chk.checked = !!uiData.state")
    assert guard < checkbox
