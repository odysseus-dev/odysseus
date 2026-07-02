"""Regression: api_call reaches the model for API-integration intent (#3794).

The repro prompt — "Use the api_call tool to call Home Assistant GET
/api/states" — matched no domain in ``_classify_agent_request``, so it was
treated as low-signal. The agent loop then skipped retrieval and the function
schema filter sent only the always-available tools (manage_memory / ask_user /
update_plan); ``api_call`` was never advertised to the model even though the
ToolIndex description existed. Adding the registry description alone did not fix
runtime selection.

These tests drive the real path the agent uses — classifier -> domain tool map
(relevant tools) -> FUNCTION_TOOL_SCHEMAS filter — using the actual functions and
constants, so they would fail on the pre-fix code (empty domains -> low-signal ->
no api_call). They skip locally when the agent's heavy deps (httpx/embeddings)
are absent, and run in CI where they are installed.
"""
import pytest

agent_loop = pytest.importorskip("src.agent_loop")

REPRO = "Use the api_call tool to call Home Assistant GET /api/states"


def _selected_tools(domains):
    """Mirror agent_loop's deterministic domain seeding (see the loop over
    `_intent['domains']` that updates `_relevant_tools` from `_DOMAIN_TOOL_MAP`)."""
    tools = set()
    for domain in domains:
        tools |= agent_loop._DOMAIN_TOOL_MAP.get(domain, set())
    return tools


def _schema_names_sent(tools):
    """Mirror the api-model schema filter that keeps only selected tools."""
    return {
        s.get("function", {}).get("name")
        for s in agent_loop.FUNCTION_TOOL_SCHEMAS
        if s.get("function", {}).get("name") in tools
    }


@pytest.mark.parametrize(
    "prompt",
    [
        REPRO,
        "check my home assistant lights",
        "fetch the latest unread from miniflux via the api_call tool",
        "call my gitea integration to list repos",
    ],
)
def test_integration_prompts_are_not_low_signal(prompt):
    intent = agent_loop._classify_agent_request([], prompt)
    assert intent["low_signal"] is False, intent
    assert "integrations" in intent["domains"], intent


def test_repro_selects_and_sends_api_call_schema():
    intent = agent_loop._classify_agent_request([], REPRO)
    selected = _selected_tools(intent["domains"])
    assert "api_call" in selected, selected
    # The schema filter must actually advertise api_call to the model.
    assert "api_call" in _schema_names_sent(selected), "api_call schema must reach the model"


def test_integrations_domain_has_a_rule_pack():
    # _domain_rules_for_tools indexes _DOMAIN_RULES[domain] directly, so a domain
    # present in _DOMAIN_TOOL_MAP without a _DOMAIN_RULES entry would KeyError the
    # moment api_call is selected.
    rules = agent_loop._domain_rules_for_tools({"api_call"})
    assert any("api_call" in r for r in rules), rules


def test_plain_greeting_does_not_pull_api_call():
    # Guard against over-matching: an unrelated message stays low-signal and must
    # not drag the integration tool into context.
    intent = agent_loop._classify_agent_request([], "hey there, how are you")
    assert "integrations" not in intent["domains"], intent
    assert "api_call" not in _selected_tools(intent["domains"])
