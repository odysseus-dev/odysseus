"""Regression: memory intent reaches the agent instead of the no-tools path.

The repro prompts — "Store this fact in memory: my favorite tea is green tea"
/ "Remember that I live in Stockholm" — matched no domain in
``_classify_agent_request``, so the turn was treated as low-signal and took the
direct no-tools chat path. There the model *claims* it saved a memory although
no tool ran (observed with mistral-small and qwen2.5-coder). Same failure class
as the api_call case (#3794).

These tests drive the real path — classifier -> domain tool map -> schema
filter — using the actual functions and constants, so they fail on pre-fix
code (empty domains -> low-signal -> direct path).
"""
import pytest

agent_loop = pytest.importorskip("src.agent_loop")


def _selected_tools(domains):
    tools = set()
    for domain in domains:
        tools |= agent_loop._DOMAIN_TOOL_MAP.get(domain, set())
    return tools


@pytest.mark.parametrize(
    "prompt",
    [
        "Store this fact in memory: my favorite tea is green tea.",
        "Remember that I live in Stockholm",
        "use the manage_memory tool to add this",
        "what do you have in your memories about me?",
        "memorize this: I prefer concise replies",
        "forget that I said I like coffee",
    ],
)
def test_memory_prompts_are_not_low_signal(prompt):
    intent = agent_loop._classify_agent_request([], prompt)
    assert intent["low_signal"] is False, intent
    assert "memory" in intent["domains"], intent


def test_memory_domain_seeds_manage_memory():
    intent = agent_loop._classify_agent_request(
        [], "Store this fact in memory: my favorite tea is green tea."
    )
    assert "manage_memory" in _selected_tools(intent["domains"])


def test_memory_domain_has_rules_pack():
    # _domain_rules_for_tools indexes _DOMAIN_RULES by every domain whose tools
    # intersect the selection — a map entry without a rules entry is a KeyError.
    rules = agent_loop._domain_rules_for_tools({"manage_memory"})
    assert any("manage_memory" in r for r in rules)


@pytest.mark.parametrize(
    "prompt",
    [
        "yo",
        "thanks!",
        "hey man",
    ],
)
def test_casual_messages_stay_low_signal(prompt):
    intent = agent_loop._classify_agent_request([], prompt)
    assert intent["low_signal"] is True, intent
