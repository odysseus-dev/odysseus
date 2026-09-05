"""Regression: api_call reaches selection for API-integration requests."""
import pytest

agent_loop = pytest.importorskip("src.agent_loop")
from src.tool_index import ToolIndex

REPRO = "Use the api_call tool to call Home Assistant GET /api/states"


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
def test_integration_prompts_add_api_call(prompt):
    assert "api_call" in ToolIndex.get_additive_hints(prompt)


def test_repro_selects_and_sends_api_call_schema():
    selected = ToolIndex.get_additive_hints(REPRO)
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
    assert "api_call" not in ToolIndex.get_additive_hints("hey there, how are you")
