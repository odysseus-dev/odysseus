"""Regression: the agent tool-RAG domain classifier had no contacts domain,
so contact-lookup requests matched no domain, were flagged low_signal, and had
tool retrieval SKIPPED entirely — the model only received ALWAYS_AVAILABLE tools
(manage_memory, ask_user, update_plan) and never `resolve_contact`/`manage_contact`,
so it could not look up contacts from the CardDAV address book (it looped on
manage_memory instead).

Root cause: `_classify_agent_request` in src/agent_loop.py sets
`low_signal = not continuation and not domains`; with no `contacts` domain,
prompts like "What is Massimo's contact?" matched nothing → low_signal →
retrieval skipped.

The classifier is deterministic string matching (no embeddings / no DB), so it
can be exercised directly.
"""

from src.agent_loop import (
    _classify_agent_request,
    _DOMAIN_TOOL_MAP,
    _DOMAIN_RULES,
    _domain_rules_for_tools,
)


def _classify(text):
    return _classify_agent_request([{"role": "user", "content": text}], text)


def test_contact_lookup_requests_get_contacts_domain():
    """Contact-lookup phrasings must match the `contacts` domain and NOT be
    treated as low-signal (which would skip tool retrieval)."""
    prompts = [
        "What is Massimo's contact?",
        "What's John's phone number?",
        "Show me my contacts",
        "Look up Kevin's contact info",
        "Find Alice's phone number",
    ]
    for p in prompts:
        intent = _classify(p)
        assert "contacts" in intent["domains"], f"expected contacts domain for: {p!r}"
        assert intent["low_signal"] is False, f"must not be low_signal: {p!r}"


def test_contact_management_requests_get_contacts_domain():
    """Add/update/delete contact phrasings also resolve to the contacts domain."""
    for p in ("add a new contact", "update Bob's phone number", "delete that contact",
              "save this person to contacts"):
        intent = _classify(p)
        assert "contacts" in intent["domains"], f"expected contacts domain for: {p!r}"


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


def test_non_contact_requests_do_not_match_contacts_domain():
    """Guard against over-triggering: ordinary prompts must not be flagged contacts."""
    assert "contacts" not in _classify("what is the capital of France")["domains"]
    assert "contacts" not in _classify("reply to the latest email in my inbox")["domains"]
    assert "contacts" not in _classify("generate an image of a sunset")["domains"]
    assert "contacts" not in _classify("what's 2 plus 2")["domains"]
