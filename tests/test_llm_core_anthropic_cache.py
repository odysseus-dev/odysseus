"""Regression tests for Anthropic prompt-cache breakpoints in _build_anthropic_payload (#791)."""
from src import llm_core


def _payload(system="sys", user="hi", tools=None):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return llm_core._build_anthropic_payload("claude", messages, 0.0, 1000, stream=True, tools=tools)


def test_agentic_caches_system_and_last_tool():
    tools = [
        {"type": "function", "function": {"name": "a", "description": "x", "parameters": {}}},
        {"type": "function", "function": {"name": "b", "description": "y", "parameters": {}}},
    ]
    p = _payload(system="SYS PROMPT " * 50, tools=tools)
    assert isinstance(p["system"], list)
    assert p["system"][0].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in p["tools"][0], "only the LAST tool is a breakpoint"
    assert p["tools"][-1].get("cache_control") == {"type": "ephemeral"}
    breakpoints = sum("cache_control" in b for b in p["system"]) + sum("cache_control" in t for t in p["tools"])
    assert breakpoints == 2


def test_tiny_tool_less_prompt_not_cached():
    p = _payload(system="hi", tools=None)
    assert isinstance(p["system"], list)
    assert "cache_control" not in p["system"][0]


def test_large_system_only_is_cached():
    p = _payload(system="z" * 5000, tools=None)
    assert p["system"][0].get("cache_control") == {"type": "ephemeral"}
