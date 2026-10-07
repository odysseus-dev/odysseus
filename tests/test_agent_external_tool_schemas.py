from __future__ import annotations

import asyncio

from src import agent_loop
from src.tool_execution import AgentExecutionBridge, bind_execution_bridge
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks


def _collect(stream):
    async def run():
        return [chunk async for chunk in stream]

    return asyncio.run(run())


def test_external_tool_schema_is_scoped_into_model_request(monkeypatch):
    observed_tools = []
    observed_tool_values = []
    observed_messages = []
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())

    async def fake_stream(candidates, messages, **kwargs):
        observed_messages.extend(messages)
        observed_tool_values.append(kwargs.get("tools"))
        observed_tools.extend(kwargs.get("tools") or [])
        yield 'data: {"delta": "complete"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    _collect(agent_loop.stream_agent_loop(
        "https://api.openai.com/v1",
        "policy-model",
        [
            {
                "role": "assistant",
                "content": "Prior visible response.",
                "reasoning_content": "transport-private scratchpad",
            },
            {"role": "user", "content": "Inspect the current state."},
        ],
        max_rounds=1,
        owner="pewds",
        relevant_tools={"inspect_state"},
        forced_tools={"inspect_state"},
        fallbacks=[],
        fallback_on_empty=False,
        external_tool_schemas=[{
            "type": "function",
            "function": {
                "name": "inspect_state",
                "description": "Return the current synthetic state.",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        _is_teacher_run=True,
    ))

    matching = [
        schema for schema in observed_tools
        if schema.get("function", {}).get("name") == "inspect_state"
    ]
    assert len(matching) == 1, [
        schema.get("function", {}).get("name")
        for value in observed_tool_values
        for schema in (value or [])
    ]
    assert matching[0]["function"]["description"] == "Return the current synthetic state."
    system_text = next(message["content"] for message in observed_messages if message["role"] == "system")
    assert "request-scoped environment" in system_text
    assert "local-machine mode" not in system_text
    assert len(system_text) < 1000
    assert all("reasoning_content" not in message for message in observed_messages)


def test_local_qwen_external_tool_route_preserves_assistant_reasoning_continuity(monkeypatch):
    """Qwen's reasoning parser needs the prior tool-call reasoning on replay."""
    observed_messages = []
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())

    async def fake_stream(candidates, messages, **kwargs):
        observed_messages.extend(messages)
        yield 'data: {"delta": "complete"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    _collect(agent_loop.stream_agent_loop(
        "http://127.0.0.1:19200/v1/chat/completions",
        "odysseus-qwen3.5-9b-preheretic",
        [
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "choose the declared lookup",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "inspect_state", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "available"},
            {"role": "user", "content": "Summarize the observed state."},
        ],
        max_rounds=1,
        owner="pewds",
        relevant_tools={"inspect_state"},
        forced_tools={"inspect_state"},
        fallbacks=[],
        fallback_on_empty=False,
        external_tool_schemas=[{
            "type": "function",
            "function": {
                "name": "inspect_state",
                "description": "Return current state.",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        _is_teacher_run=True,
    ))

    replayed = [m for m in observed_messages if m.get("role") == "assistant"]
    assert replayed[0]["reasoning_content"] == "choose the declared lookup"


def test_external_tool_schema_can_use_textual_transport_from_first_request(monkeypatch):
    observed_tools = []
    observed_messages = []
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())

    async def fake_stream(candidates, messages, **kwargs):
        observed_messages.extend(messages)
        observed_tools.append(kwargs.get("tools"))
        yield 'data: {"delta": "complete"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    chunks = _collect(agent_loop.stream_agent_loop(
        "https://policy.invalid/v1",
        "policy-model",
        [{"role": "user", "content": "Inspect the current state."}],
        max_rounds=1,
        owner="pewds",
        relevant_tools={"inspect_state"},
        forced_tools={"inspect_state"},
        fallbacks=[],
        fallback_on_empty=False,
        external_tool_schemas=[{
            "type": "function",
            "function": {
                "name": "inspect_state",
                "description": "Return the current synthetic state.",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        force_textual_tool_transport=True,
        _is_teacher_run=True,
    ))

    assert observed_tools == [None]
    delta_events = [
        __import__("json").loads(chunk[6:])
        for chunk in chunks
        if chunk.startswith("data: {") and '"delta"' in chunk
    ]
    assert delta_events[0]["round"] == 1
    system_text = next(message["content"] for message in observed_messages if message["role"] == "system")
    assert "inspect_state" in system_text
    assert "Environment tools declared for this turn" in system_text
    assert "fenced block" in system_text


def test_declared_textual_tool_fence_is_request_scoped():
    text = 'before\n```inspect_state\n{"scope":"active"}\n```\nafter'

    assert parse_tool_blocks(text) == []
    [block] = parse_tool_blocks(text, additional_tool_names={"inspect_state"})

    assert block.tool_type == "inspect_state"
    assert block.content == '{"scope":"active"}'
    assert strip_tool_blocks(text, additional_tool_names={"inspect_state"}) == "before\n\nafter"


def test_json_fence_can_name_only_a_request_declared_tool():
    text = '```json\ninspect_state\n{"scope":"active"}\n```'

    assert all(block.tool_type != "inspect_state" for block in parse_tool_blocks(text))
    [block] = parse_tool_blocks(text, additional_tool_names={"inspect_state"})

    assert block.tool_type == "inspect_state"
    assert block.content == '{"scope": "active"}'


def test_split_json_fence_can_name_only_a_request_declared_tool():
    text = '```json\ninspect_state\n```\n{"scope":"active"}\n```'

    assert parse_tool_blocks(text) == []
    [block] = parse_tool_blocks(text, additional_tool_names={"inspect_state"})

    assert block.tool_type == "inspect_state"
    assert block.content == '{"scope": "active"}'


def test_direct_xml_can_name_only_a_request_declared_tool():
    text = 'before\n```json\n<inspect_state>{"scope":"active"}</inspect_state>\n```\nafter'

    assert parse_tool_blocks(text) == []
    [block] = parse_tool_blocks(text, additional_tool_names={"inspect_state"})

    assert block.tool_type == "inspect_state"
    assert block.content == '{"scope": "active"}'
    assert strip_tool_blocks(text, additional_tool_names={"inspect_state"}) == "before\n\nafter"


def test_adjacent_fences_can_name_only_a_request_declared_tool():
    text = '```bash\ninspect_state\n```\n```json\n{"scope":"active"}\n```'

    [ordinary_block] = parse_tool_blocks(text)
    assert ordinary_block.tool_type == "bash"
    [block] = parse_tool_blocks(text, additional_tool_names={"inspect_state"})

    assert block.tool_type == "inspect_state"
    assert block.content == '{"scope": "active"}'


def test_adjacent_fences_can_put_declared_arguments_before_tool_name():
    text = '```json\n{"scope":"active"}\n```\n```bash\ninspect_state\n```'

    [block] = parse_tool_blocks(text, additional_tool_names={"inspect_state"})

    assert block.tool_type == "inspect_state"
    assert block.content == '{"scope": "active"}'


def test_language_fence_can_wrap_a_request_declared_tool_envelope():
    text = """```python
write_file
/workspace/output.html
<main>Complete output</main>
```"""

    [block] = parse_tool_blocks(
        text,
        additional_tool_names={"python", "write_file"},
    )

    assert block.tool_type == "write_file"
    assert block.content == "/workspace/output.html\n<main>Complete output</main>"


def test_language_fence_can_repeat_its_declared_tool_name():
    text = """```python
python
print('verified')
```"""

    [block] = parse_tool_blocks(text, additional_tool_names={"python"})

    assert block.tool_type == "python"
    assert block.content == "print('verified')"


def test_name_only_json_fence_dispatches_declared_tool_with_empty_arguments():
    text = '```json\ninspect_state\n```'

    assert parse_tool_blocks(text) == []
    [block] = parse_tool_blocks(text, additional_tool_names={"inspect_state"})

    assert block.tool_type == "inspect_state"
    assert block.content == "{}"


def test_external_textual_history_omits_nonstandard_reasoning_field():
    messages = []

    agent_loop._append_tool_results(
        messages,
        '```inspect_state\n{"scope":"active"}\n```',
        [],
        ["inspect_state: available"],
        ["inspect_state: available"],
        False,
        1,
        round_reasoning="private scratchpad",
        include_reasoning_content=False,
    )

    assert all("reasoning_content" not in message for message in messages)


def test_external_tool_images_are_threaded_as_multimodal_evidence():
    messages = []

    agent_loop._append_tool_results(
        messages,
        '```read_media\n{"path":"/workspace/clip.mp4"}\n```',
        [],
        ["read_media: frames extracted"],
        ["read_media: frames extracted"],
        False,
        1,
        tool_result_records=[{
            "tool_name": "read_media",
            "content": '{"path":"/workspace/clip.mp4"}',
            "result": {
                "output": "frames extracted",
                "images": [
                    {"mimeType": "image/png", "data": "frame-a"},
                    {"mimeType": "image/jpeg", "data": "frame-b"},
                ],
            },
        }],
    )

    evidence = messages[-1]
    assert evidence["metadata"]["trusted"] is False
    assert evidence["content"][0]["type"] == "text"
    assert [block["image_url"]["url"] for block in evidence["content"][1:]] == [
        "data:image/png;base64,frame-a",
        "data:image/jpeg;base64,frame-b",
    ]


def test_declared_external_call_reaches_scoped_bridge(monkeypatch):
    from src.agent_runtime.authority import OperationGrant, RequestAuthority
    bridge_calls = []
    round_no = 0
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())

    async def fake_stream(candidates, messages, **kwargs):
        nonlocal round_no
        round_no += 1
        if round_no == 1:
            yield 'data: {"type": "tool_calls", "calls": [{"id": "call-neutral-1", "name": "inspect_state", "arguments": "{\\"scope\\":\\"active\\"}"}]}\n\n'
        else:
            yield 'data: {"delta": "complete"}\n\n'
        yield "data: [DONE]\n\n"

    async def route(tool, content, session_id, runtime_context):
        bridge_calls.append((tool, content))
        return "inspect_state", {"output": "available", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    bridge = AgentExecutionBridge(
        route_tool=route,
        supported_tools=frozenset({"inspect_state"}),
        name="synthetic_environment",
    )

    with bind_execution_bridge(bridge):
        _collect(agent_loop.stream_agent_loop(
            "https://api.openai.com/v1",
            "policy-model",
            [{"role": "user", "content": "Perform the declared operation."}],
            max_rounds=2,
            owner="pewds",
            request_authority=RequestAuthority("declared-fixture", "pewds", "", "",
                (OperationGrant("inspect_state"),)),
            relevant_tools={"inspect_state"},
            forced_tools={"inspect_state"},
            fallbacks=[],
            fallback_on_empty=False,
            external_tool_schemas=[{
                "type": "function",
                "function": {
                    "name": "inspect_state",
                    "description": "Return the current synthetic state.",
                    "parameters": {
                        "type": "object",
                        "properties": {"scope": {"type": "string"}},
                    },
                },
            }],
            _is_teacher_run=True,
        ))

    assert bridge_calls == [("inspect_state", '{"scope": "active"}')]


def test_known_native_tool_reaches_scoped_bridge_without_redeclared_schema(monkeypatch):
    from src.agent_runtime.authority import create_request_authority
    bridge_calls = []
    round_no = 0
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(
        agent_loop,
        "blocked_tools_for_owner",
        lambda owner: {"search_emails"},
    )

    async def fake_stream(candidates, messages, **kwargs):
        nonlocal round_no
        round_no += 1
        if round_no == 1:
            offered = {
                schema["function"]["name"]
                for schema in kwargs.get("tools") or []
            }
            assert "search_emails" in offered
            yield 'data: {"type": "tool_calls", "calls": [{"id": "call-search-1", "name": "search_emails", "arguments": "{\\"query\\":\\"Project Alpha\\"}"}]}\n\n'
        else:
            yield 'data: {"delta": "complete"}\n\n'
        yield "data: [DONE]\n\n"

    async def route(tool, content, session_id, runtime_context):
        bridge_calls.append((tool, content))
        return "search_emails", {"output": "matching messages", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    bridge = AgentExecutionBridge(
        route_tool=route,
        supported_tools=frozenset({
            "search_emails",
            "mcp__email__search_emails",
        }),
        name="native_environment",
    )

    from dataclasses import replace
    with bind_execution_bridge(bridge):
        authority = create_request_authority("Search email for Project Alpha.", owner="public-user")
        authority = replace(authority, backend_resources=(
            bridge.resource_identity("search_emails"),
            bridge.resource_identity("mcp__email__search_emails"),
        ))
        _collect(agent_loop.stream_agent_loop(
            "https://api.openai.com/v1",
            "policy-model",
            [{"role": "user", "content": "Search email for Project Alpha."}],
            max_rounds=2,
            owner="public-user",
            request_authority=authority,
            relevant_tools={"search_emails"},
            forced_tools={"search_emails"},
            fallbacks=[],
            fallback_on_empty=False,
            client_runtime_context={
                "surface": "odysseus-native",
                "unattended_mode": True,
            },
            _is_teacher_run=True,
        ))

    assert bridge_calls == [
        ("mcp__email__search_emails", '{"query": "Project Alpha"}'),
    ]
