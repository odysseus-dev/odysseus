from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_saved_agent_history_uses_final_answer_with_collapsed_process():
    source = _read("static/js/chatRenderer.js")

    assert "getAgentFinalResponse(textRaw, metadata)" in source
    assert "const agentProcessPanel = _hasAgentProcess(metadata)" in source
    assert "role === 'assistant' && agentProcessPanel" in source
    assert "b.insertBefore(agentProcessPanel, b.firstChild)" in source
    assert "!agentProcessPanel && role === 'assistant'" in source


def test_live_agent_completion_collapses_process_after_done():
    source = _read("static/js/chat.js")

    assert "let footerTarget =" in source
    assert "metrics?.tool_events?.length" in source
    assert "chatRenderer.collapseAgentProcessAfterStream" in source
    assert "json.type === 'agent_process'" in source
    assert "json.type === 'agent_final'" in source
    assert "holder._agentFinalText" in source
    assert "const _actionText = (metrics?.tool_events?.length" in source
    assert "chatRenderer.getAgentFinalResponse(accumulated, metrics)" in source
    renderer = _read("static/js/chatRenderer.js")
    assert "metadata.agent_limits" in renderer
    assert "agent-process-meta" in renderer
    assert "limits.workspace_label || limits.workspace_path" in renderer
    assert "workspace policy" in renderer
    assert "body.innerHTML = sourcesPrefix" in renderer
    assert "body.insertBefore(panel, body.firstChild)" in renderer
    assert "markdownModule.processWithThinking(markdownModule.squashOutsideCode(finalText))" in renderer


def test_agent_process_has_collapsed_panel_styles():
    source = _read("static/style.css")

    assert ".agent-process {" in source
    assert "margin: 0 0 10px;" in source
    assert ".agent-process-summary" in source
    assert ".agent-process-meta" in source
    assert ".agent-process > summary::before" in source
    assert ".agent-process[open] > .agent-process-summary::after" in source
    assert ".stopped-detail" in source
