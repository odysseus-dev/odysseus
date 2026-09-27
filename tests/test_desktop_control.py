"""Desktop-control wiring: capabilities, schema exposure, intent routing, and
feeding screenshots back to the model as images."""

import sys

import pytest

from mcp_servers import desktop_server as ds
from src.agent_loop import _append_desktop_screenshots, _expand_desktop_mcp_tools
from src.mcp_manager import FUNCTION_CALLING_BUILTINS
from src.tool_capabilities import (
    POST_EXTERNAL_BLOCKED_EFFECTS,
    ResultIntegrity,
    capabilities_for_tool,
)
from src.tool_execution import format_tool_result

SHOT = "mcp__builtin_desktop__desktop_screenshot"
CLICK = "mcp__builtin_desktop__desktop_click"


def _record(name, data="QUJD"):
    return {
        "tool_name": name,
        "content": "{}",
        "result": {
            "stdout": "ok",
            "stderr": "",
            "exit_code": 0,
            "images": [{"data": data, "mimeType": "image/png"}],
        },
    }


def test_screenshot_is_ungated_read_with_untrusted_output():
    caps = capabilities_for_tool(SHOT)
    assert caps.known
    assert not (caps.effects & POST_EXTERNAL_BLOCKED_EFFECTS)
    assert caps.result_integrity is ResultIntegrity.EXTERNAL_UNTRUSTED


def test_input_actions_are_gated_after_untrusted_context():
    for name in (CLICK, "mcp__builtin_desktop__desktop_type", "mcp__builtin_desktop__desktop_key"):
        caps = capabilities_for_tool(name)
        assert caps.known
        assert caps.effects & POST_EXTERNAL_BLOCKED_EFFECTS


def test_desktop_server_uses_native_function_calling():
    assert "builtin_desktop" in FUNCTION_CALLING_BUILTINS
    assert "builtin_browser" in FUNCTION_CALLING_BUILTINS


def test_screenshot_is_appended_as_image_message():
    messages = []
    _append_desktop_screenshots(messages, [_record(CLICK)])
    assert len(messages) == 1
    msg = messages[0]
    assert msg["role"] == "user"
    assert msg["metadata"]["trusted"] is False
    assert msg["metadata"]["tool_gate_untrusted"] is True
    image = [part for part in msg["content"] if part["type"] == "image_url"][0]
    assert image["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_only_latest_screenshot_stays_in_context():
    messages = []
    _append_desktop_screenshots(messages, [_record(SHOT, "T0xE")])
    _append_desktop_screenshots(messages, [_record(CLICK, "TkVX")])
    assert isinstance(messages[0]["content"], str)
    assert "omitted" in messages[0]["content"]
    assert "TkVX" in messages[1]["content"][1]["image_url"]["url"]


def test_non_desktop_images_are_not_sent_to_model():
    messages = []
    _append_desktop_screenshots(
        messages, [_record("mcp__builtin_browser__browser_take_screenshot")]
    )
    assert messages == []


def test_base64_images_are_not_dumped_into_tool_text():
    text = format_tool_result("shot", _record(SHOT, "X" * 500)["result"])
    assert "XXXX" not in text


class _FakeMgr:
    def get_all_tools(self):
        return [
            {"server_id": "builtin_desktop", "qualified_name": SHOT},
            {"server_id": "builtin_desktop", "qualified_name": CLICK},
            {"server_id": "builtin_desktop", "qualified_name": "mcp__builtin_desktop__x", "is_disabled": True},
            {"server_id": "builtin_browser", "qualified_name": "mcp__builtin_browser__browser_click"},
        ]


def test_desktop_intent_expands_to_connected_tools_only():
    out = _expand_desktop_mcp_tools({SHOT}, _FakeMgr())
    assert out == {SHOT, CLICK}
    assert _expand_desktop_mcp_tools({"bash"}, _FakeMgr()) == {"bash"}


def test_desktop_intent_regex():
    from routes.chat_routes import _DESKTOP_INTENT_RE

    for msg in ("can you see my screen?", "take a screenshot", "control my computer"):
        assert _DESKTOP_INTENT_RE.search(msg), msg
    for msg in ("write me a poem", "summarize this pdf", "click the submit button on the site"):
        assert not _DESKTOP_INTENT_RE.search(msg), msg


# ── Server-side helpers (mcp_servers/desktop_server.py) ────────────────────


def test_coordinates_map_from_screenshot_to_screen(monkeypatch):
    monkeypatch.setitem(ds._view, "left", -1920)  # secondary monitor left of primary
    monkeypatch.setitem(ds._view, "top", 0)
    monkeypatch.setitem(ds._view, "width", 1366)
    monkeypatch.setitem(ds._view, "height", 768)
    monkeypatch.setitem(ds._view, "scale", 0.5)
    assert ds._to_screen(100, 50) == (-1720, 100)
    assert ds._to_view(-1720, 100) == (100, 50)
    with pytest.raises(ValueError):
        ds._to_screen(2000, 10)


def test_coordinates_require_a_screenshot(monkeypatch):
    monkeypatch.setitem(ds._view, "width", 0)
    with pytest.raises(ValueError, match="screenshot first"):
        ds._to_screen(1, 1)


def test_key_combos_accept_friendly_aliases():
    if not ds._load_backends():
        pytest.skip("pyautogui unavailable (headless or not installed)")
    assert ds._parse_keys("Ctrl + Return") == ["ctrl", "enter"]
    assert ds._parse_keys("esc") == ["escape"]
    with pytest.raises(ValueError):
        ds._parse_keys("ctrl+bogus")


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 SendInput only")
def test_win32_input_struct_matches_native_layout():
    ctypes, INPUT, _, _ = ds._win_input_api()
    assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
