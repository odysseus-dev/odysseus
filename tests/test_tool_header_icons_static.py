from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_tool_header_has_semantic_icons_for_live_and_saved_calls():
    chat = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
    renderer = (ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")
    css = app_css()

    assert "manage_calendar" in renderer
    assert "manage_memory" in renderer
    assert "manage_skills" in renderer
    assert "manage_notes" in renderer
    assert "getToolIcon" in renderer or "TOOL_ICONS" in renderer
    assert "renderToolIcon" in chat
    assert "agent-thread-tool-icon" in renderer

    assert ".agent-thread-tool-icon" in css
    assert ".agent-thread-tool-icon svg" in css


def test_tool_icons_are_navigation_targets_and_calendar_has_one_icon_path():
    chat = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
    renderer = (ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")
    agent_loop = (ROOT / "src/agent_loop.py").read_text(encoding="utf-8")

    assert "export function getToolActionTarget" in renderer
    assert "export function renderToolIcon" in renderer
    assert "href === '#plan'" in renderer
    assert "fallback: 'memory'" in renderer
    assert "renderToolIcon(json.tool, cmd, json)" in chat
    assert "headerActionHtml: ''" in chat
    assert '"memory_id", "note_id", "note_title", "task_id"' in agent_loop


def test_tool_icon_stays_before_label_when_stream_finishes_and_after_reload():
    chat = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
    renderer = (ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")

    live_done = chat[chat.index('currentToolBubble.innerHTML = `<div class="agent-thread-dot"'):][:700]
    saved_done = renderer[renderer.index('node.innerHTML = `<div class="agent-thread-dot"', renderer.index("const evToolIcon")):][:700]
    assert live_done.index("${toolIcon2}") < live_done.index('class="agent-thread-tool"')
    assert saved_done.index("${evToolIcon}") < saved_done.index('class="agent-thread-tool"')
