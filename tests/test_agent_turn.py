import asyncio
import json

from src.agent_tools import ToolBlock
from src.agent_turn import (
    ModelTurn,
    ModelTurnRequest,
    ToolTurn,
    ToolTurnRequest,
    add_auto_document_tool,
    select_turn_tool_schemas,
)


def _collect(gen):
    async def _run():
        return [chunk async for chunk in gen]

    return asyncio.run(_run())


def _events(chunks):
    out = []
    for chunk in chunks:
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            out.append(json.loads(chunk[6:]))
    return out


def test_select_turn_tool_schemas_filters_relevant_and_disabled_tools():
    schemas = select_turn_tool_schemas(
        force_answer=False,
        is_api_model=True,
        relevant_tools={"bash", "web_search"},
        mcp_schemas=[],
        needs_admin=True,
        disabled_tools={"bash"},
        last_user="search this",
        mcp_keywords=set(),
    )
    names = {schema["function"]["name"] for schema in schemas}
    assert "web_search" in names
    assert "bash" not in names


def test_force_answer_sends_no_tool_schemas():
    schemas = select_turn_tool_schemas(
        force_answer=True,
        is_api_model=True,
        relevant_tools={"bash"},
        mcp_schemas=[],
        needs_admin=True,
        disabled_tools=set(),
        last_user="run this",
        mcp_keywords=set(),
    )
    assert schemas == []


def test_model_turn_accumulates_text_usage_and_tool_blocks():
    async def stream_llm(*_args, **_kwargs):
        yield 'data: {"delta": "Checking. "}\n\n'
        yield 'data: {"delta": "```bash\\necho hi\\n```"}\n\n'
        yield 'data: {"type": "usage", "data": {"model": "actual", "input_tokens": 12, "output_tokens": 5, "gen_tps": 40}}\n\n'
        yield "data: [DONE]\n\n"

    turn = ModelTurn(ModelTurnRequest(
        round_num=1,
        candidates=[("http://x/v1", "m", {})],
        messages=[{"role": "user", "content": "run"}],
        temperature=0.3,
        max_tokens=100,
        prompt_type=None,
        tool_schemas=[],
        timeout=30,
        deadline=9999999999,
        requested_model="requested",
        actual_model="requested",
        total_start=0,
        first_token_received=False,
        tool_policy=None,
        stream_llm=stream_llm,
    ))

    chunks = _collect(turn.stream())
    assert any("Checking." in chunk for chunk in chunks)
    assert turn.result.round_response == "Checking. ```bash\necho hi\n```"
    assert turn.result.tool_blocks == [ToolBlock("bash", "echo hi")]
    assert turn.result.usage.actual_model == "actual"
    assert turn.result.usage.input_tokens == 12
    assert turn.result.usage.output_tokens == 5
    assert turn.result.usage.backend_gen_tps == 40


def test_tool_turn_emits_ask_user_question_before_choice_card():
    async def execute_tool(block, **_kwargs):
        assert block.tool_type == "ask_user"
        return "ask_user: pick", {
            "ask_user": {
                "question": "Which path?",
                "options": [{"label": "A"}, {"label": "B"}],
                "multi": False,
            },
            "output": "Asked",
            "exit_code": 0,
        }

    turn = ToolTurn(ToolTurnRequest(
        tool_blocks=[ToolBlock("ask_user", "{}")],
        round_num=1,
        total_tool_calls=0,
        max_tool_calls=0,
        session_id="s",
        disabled_tools=set(),
        tool_policy=None,
        owner=None,
        workspace=None,
        full_response_so_far="",
        effectful_tools=set(),
        doc_stream_started=False,
        execute_tool=execute_tool,
        format_tool_result=lambda desc, result: f"{desc}: {result['output']}",
    ))

    events = _events(_collect(turn.stream()))
    assert [event.get("type", "delta") for event in events] == [
        "tool_start",
        "delta",
        "ask_user",
        "tool_output",
    ]
    assert events[1]["delta"] == "Which path?"
    assert turn.result.awaiting_user is True
    assert turn.result.response_text == "Which path?"
    assert turn.result.tool_results == ["ask_user: pick: Asked"]


def test_auto_document_tool_wraps_large_code_block():
    code = "\n".join(f"line {i}" for i in range(31))
    blocks, events = add_auto_document_tool(
        [],
        [],
        f"Here:\n```\n{code}\n```",
        session_id="s",
        disabled_tools=set(),
    )
    assert blocks == [ToolBlock("create_document", f"Code (text)\ntext\n{code}")]
    assert events == [
        {"type": "doc_stream_open", "title": "Code (text)", "language": "text"},
        {"type": "doc_stream_delta", "content": code},
    ]
