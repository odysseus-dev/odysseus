from pathlib import Path


def test_agent_and_research_sessions_have_visible_prefixes():
    source = Path("static/js/sessions.js").read_text()

    assert "s.mode === 'agent' ? '[AGENT] '" in source
    assert "s.mode === 'research' ? '[RESEARCH] '" in source
    assert "modePrefix + chatTitle" in source
