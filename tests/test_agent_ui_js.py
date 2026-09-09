from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_JS = (_REPO / "static" / "js" / "agents.js").read_text(encoding="utf-8")
_HTML = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
_INIT = (_REPO / "static" / "js" / "init.js").read_text(encoding="utf-8")


def test_agents_js_exports_native_controls_and_canvas_link():
    assert "export function canvasUrl" in _JS
    assert "export function renderAgentStatus" in _JS
    assert "Open in Agent Canvas" in _JS
    assert "iframe" not in _JS.lower()
    for name in ("launchAgent", "messageAgent", "approveAgent", "cancelAgent", "resumeAgent"):
        assert f"export async function {name}" in _JS


def test_index_has_native_agent_panel_not_embedded_canvas():
    assert 'id="agent-platform-panel"' in _HTML
    assert "/static/js/agents.js" in _HTML
    assert "agent-canvas-frame" not in _HTML


def test_init_loads_agent_platform_module():
    assert "agents.js" in _INIT
    assert "initAgentPlatform" in _INIT
