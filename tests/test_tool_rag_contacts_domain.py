"""Regression: deterministic additive hints surface contact tools."""

from src.agent_loop import (
    _DOMAIN_TOOL_MAP,
    _DOMAIN_RULES,
    _domain_rules_for_tools,
)
from src.tool_index import ToolIndex


def _hints(text):
    return ToolIndex.get_additive_hints(text)


def test_contact_lookup_requests_add_contact_tools():
    prompts = [
        "What is Massimo's contact?",
        "What's John's phone number?",
        "Show me my contacts",
        "Look up Kevin's contact info",
        "Find Alice's phone number",
    ]
    for p in prompts:
        assert {"resolve_contact", "manage_contact"} <= _hints(p), p


def test_contact_management_requests_add_contact_tools():
    for p in ("add a new contact", "update Bob's phone number", "delete that contact",
              "save this person to contacts"):
        assert "manage_contact" in _hints(p), p


def test_contact_selection_does_not_remove_other_candidates():
    index = object.__new__(ToolIndex)
    index.retrieve = lambda query, k=8: {"manage_memory"}

    selected = index.get_tools_for_query("save her phone number")

    assert {"manage_memory", "manage_contact"} <= selected


def test_contacts_domain_seeds_resolve_and_manage_contact():
    """The domain must seed the actual contacts tools so they are offered even
    when semantic retrieval misses."""
    assert _DOMAIN_TOOL_MAP["contacts"] == {"resolve_contact", "manage_contact"}


def test_contacts_domain_has_a_rule_pack():
    """Every domain in _DOMAIN_TOOL_MAP needs a matching _DOMAIN_RULES entry,
    otherwise _domain_rules_for_tools raises KeyError when the tools are selected."""
    assert "contacts" in _DOMAIN_RULES
    rules = _domain_rules_for_tools({"resolve_contact"})
    assert any("Contacts rules" in r for r in rules)


def test_non_contact_requests_do_not_add_contact_tools():
    for prompt in (
        "what is the capital of France",
        "reply to the latest email in my inbox",
        "generate an image of a sunset",
        "what's 2 plus 2",
    ):
        assert "manage_contact" not in _hints(prompt)
