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


_TOOLS = [{"type": "function", "function": {"name": "a", "description": "x", "parameters": {}}}]


def _all_breakpoints(p):
    n = sum("cache_control" in b for b in p.get("system", []))
    n += sum("cache_control" in t for t in p.get("tools", []))
    for m in p["messages"]:
        if isinstance(m["content"], list):
            n += sum(isinstance(b, dict) and "cache_control" in b for b in m["content"])
    return n


def test_agentic_round_caches_conversation_tail():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "t1", "function": {"name": "a", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "result"},
    ]
    p = llm_core._build_anthropic_payload("claude", messages, 0.0, 1000, stream=True, tools=_TOOLS)
    last = p["messages"][-1]["content"]
    assert last[-1]["type"] == "tool_result"
    assert last[-1]["cache_control"] == {"type": "ephemeral"}
    assert p["messages"][0]["content"] == "do it"
    assert _all_breakpoints(p) == 3  # system + tools + conversation, within the API's 4


def test_multi_turn_chat_caches_last_user_text():
    messages = [
        {"role": "user", "content": "My name is Alex."},
        {"role": "assistant", "content": "Hi Alex."},
        {"role": "user", "content": "What is my name?"},
    ]
    p = llm_core._build_anthropic_payload("claude", messages, 0.0, 1000)
    assert p["messages"][-1]["content"] == [
        {"type": "text", "text": "What is my name?", "cache_control": {"type": "ephemeral"}}
    ]
    assert p["messages"][0]["content"] == "My name is Alex."


def test_one_shot_call_leaves_messages_uncached():
    p = _payload(system="hi", user="hello", tools=None)
    assert p["messages"][-1]["content"] == "hello"


def test_breakpoint_does_not_leak_into_caller_history():
    block = {"type": "text", "text": "look"}
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": [block]},
    ]
    llm_core._build_anthropic_payload("claude", messages, 0.0, 1000)
    llm_core._build_anthropic_payload("claude", messages, 0.0, 1000)
    assert "cache_control" not in block
    assert messages[-1]["content"] == [{"type": "text", "text": "look"}]


def test_skips_trailing_empty_text_and_existing_breakpoint():
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": [{"type": "text", "text": "real"}, {"type": "text", "text": ""}]},
    ]
    p = llm_core._build_anthropic_payload("claude", messages, 0.0, 1000)
    tail = p["messages"][-1]["content"]
    assert tail[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in tail[1]

    preset = [
        {"role": "user", "content": [{"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}}]},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    p = llm_core._build_anthropic_payload("claude", preset, 0.0, 1000)
    assert p["messages"][-1]["content"] == "c"


def _agent_round():
    """A mid-run agent conversation: user ask, assistant tool call, tool result."""
    return [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "find it"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "a", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "t1", "content": "result text"},
    ]


def _count_message_breakpoints(payload):
    return sum(
        "cache_control" in b
        for m in payload["messages"] if isinstance(m["content"], list)
        for b in m["content"]
    )


def test_agentic_caches_newest_conversation_block():
    p = llm_core._build_anthropic_payload("claude", _agent_round(), 0.0, 1000, stream=True, tools=_TOOLS)
    newest = p["messages"][-1]["content"][-1]
    assert newest["type"] == "tool_result"
    assert newest.get("cache_control") == {"type": "ephemeral"}
    assert _count_message_breakpoints(p) == 1, "only the newest block is a message breakpoint"
    total = (sum("cache_control" in b for b in p["system"]) + sum("cache_control" in t for t in p["tools"])
             + _count_message_breakpoints(p))
    assert total <= 4, "Anthropic rejects more than 4 breakpoints"


def test_conversation_breakpoint_does_not_mutate_caller_messages():
    messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    p = llm_core._build_anthropic_payload("claude", messages, 0.0, 1000, stream=True, tools=_TOOLS)
    assert p["messages"][-1]["content"][-1].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in messages[0]["content"][0]


def test_string_content_becomes_a_cached_text_block():
    p = llm_core._build_anthropic_payload(
        "claude", [{"role": "user", "content": "hello"}], 0.0, 1000, stream=True, tools=_TOOLS)
    assert p["messages"][-1]["content"] == [
        {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}]


def test_skips_thinking_and_empty_text_blocks():
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "real"},
        {"type": "text", "text": "   "},
        {"type": "thinking", "thinking": "x"},
    ]}]
    p = llm_core._build_anthropic_payload("claude", messages, 0.0, 1000, stream=True, tools=_TOOLS)
    blocks = p["messages"][-1]["content"]
    assert blocks[0].get("cache_control") == {"type": "ephemeral"}
    assert all("cache_control" not in b for b in blocks[1:])


def test_no_conversation_breakpoint_for_short_call_without_tools():
    # Without tools, only chats of 3+ messages get the conversation breakpoint;
    # a short exchange has no later request to reuse the cache write.
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    p = llm_core._build_anthropic_payload("claude", messages, 0.0, 1000, stream=True, tools=None)
    assert _count_message_breakpoints(p) == 0


def test_caller_placed_breakpoint_is_left_alone():
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "b"},
    ]}]
    p = llm_core._build_anthropic_payload("claude", messages, 0.0, 1000, stream=True, tools=_TOOLS)
    assert _count_message_breakpoints(p) == 1
    assert "cache_control" not in p["messages"][-1]["content"][-1]
