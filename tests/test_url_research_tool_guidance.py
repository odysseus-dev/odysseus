from src.agent_loop import _AGENT_RULES, _API_AGENT_RULES, _DOMAIN_RULES, TOOL_SECTIONS


def test_url_research_guidance_prefers_web_fetch_before_open_research():
    expected = "concrete URL and asks to research/analyze/report"

    assert expected in _AGENT_RULES
    assert expected in _API_AGENT_RULES
    assert expected in _DOMAIN_RULES["web"]
    assert expected in TOOL_SECTIONS["web_fetch"]
