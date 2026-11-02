from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_user_mode_pill_is_rendered_and_live_updated():
    renderer = (ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")
    chat = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
    styles = app_css()
    routes = (ROOT / "routes/chat_routes.py").read_text(encoding="utf-8")
    helpers = (ROOT / "routes/chat_helpers.py").read_text(encoding="utf-8")

    assert "function userModePill(metadata)" in renderer
    assert "export function setUserModePill" in renderer
    assert "json.type === 'turn_mode'" in chat
    assert "_bubbleMeta.interaction_mode = _bubbleMode" in chat
    assert ".user-mode-pill" in styles
    assert "USER_MODE_AGENT_ICON" in renderer
    assert "USER_MODE_CHAT_ICON" in renderer
    assert "pill.innerHTML = mode === 'agent' ? USER_MODE_AGENT_ICON : USER_MODE_CHAT_ICON" in renderer
    assert "Agent mode" in renderer
    assert "Chat mode" in renderer
    assert ".user-mode-pill svg" in styles
    assert "'type': 'turn_mode'" in routes
    assert '"interaction_mode"] = interaction_mode' in helpers


def test_calendar_event_anchor_uses_all_day_label():
    agent_loop = (ROOT / "src/agent_loop.py").read_text(encoding="utf-8")

    assert 'bool(_calendar_args_for_anchor.get("all_day"))' in agent_loop
    assert 'return "All day"' in agent_loop


def test_open_calendar_persists_hidden_context_snapshot():
    agent_loop = (ROOT / "src/agent_loop.py").read_text(encoding="utf-8")
    renderer = (ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")

    assert "def _calendar_open_panel_snapshot_command" in agent_loop
    assert '"triggered_by": "ui_control open_panel calendar"' in agent_loop
    assert '"context_only": True' in agent_loop
    assert "ToolBlock(\"manage_calendar\", _calendar_snapshot_command)" in agent_loop
    assert '"type": "tool_start", "tool": "manage_calendar"' in agent_loop
    assert "rangeSummary" in renderer
    assert "if (ev && ev.context_only) continue;" not in renderer
