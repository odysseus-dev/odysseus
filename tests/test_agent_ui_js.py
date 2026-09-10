from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_JS = (_REPO / "static" / "js" / "agents.js").read_text(encoding="utf-8")
_HTML = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
_INIT = (_REPO / "static" / "js" / "init.js").read_text(encoding="utf-8")
_APP = (_REPO / "static" / "app.js").read_text(encoding="utf-8")
_CHAT = (_REPO / "static" / "js" / "chat.js").read_text(encoding="utf-8")


def test_agents_js_exports_native_controls_and_canvas_link():
    assert "export function canvasUrl" in _JS
    assert "export function renderAgentStatus" in _JS
    assert "Open in Agent Canvas" in _JS
    assert "iframe" not in _JS.lower()
    for name in ("launchAgent", "messageAgent", "approveAgent", "cancelAgent", "resumeAgent"):
        assert f"export async function {name}" in _JS


def test_index_has_no_side_panel():
    assert 'id="agent-platform-panel"' not in _HTML
    assert "agent-canvas-frame" not in _HTML
    assert 'id="agent-chat-controls"' in _HTML
    assert "data-agent-approve" in _HTML
    assert "data-agent-cancel" in _HTML
    assert "data-agent-resume" in _HTML
    assert "data-agent-canvas" in _HTML
    assert 'id="agent-type-select"' in _HTML
    assert "hermes" not in _HTML.lower() or "hermes" not in _HTML.split('id="agent-type-select"')[1][:400]
    assert "/static/js/agents.js" in _HTML


def test_model_picker_markup_unchanged():
    assert 'id="model-picker-wrap"' in _HTML
    assert 'id="model-picker-btn"' in _HTML
    assert 'id="model-picker-label"' in _HTML


def test_agents_js_has_no_query_gate_or_second_composer():
    assert ".has('agents')" not in _JS
    assert "data-agent-input" not in _JS
    assert "data-agent-launch" not in _JS
    assert "initAgentPlatform" in _JS


def test_chat_binds_execution_sse_and_posts_profile():
    assert "agent_profile_id" in _CHAT
    assert "__odysseusBindAgentExecution" in _CHAT
    assert "pending_confirmation" in _CHAT


def test_approve_hidden_unless_pending_confirmation():
    assert "approve.hidden = !execution.pending_confirmation" in _JS


def test_chat_skips_agent_bind_when_background():
    assert "if (!_isBg && typeof window.__odysseusBindAgentExecution === 'function')" in _CHAT


def test_init_loads_agent_platform_module():
    assert "agents.js" in _INIT
    assert "initAgentPlatform" in _INIT


def test_global_401_preserves_current_query_on_login():
    assert "encodeURIComponent" in _APP
    assert "/login?next=" in _APP
    assert "window.location.href = '/login';" not in _APP
