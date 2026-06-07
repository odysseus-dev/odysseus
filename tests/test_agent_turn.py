import asyncio
import json

from src.agent_tools import ToolBlock
from src.agent_turn import (
    DocumentStream,
    ModelTurn,
    ModelTurnRequest,
    ToolTurn,
    ToolTurnRequest,
    add_auto_document_tool,
    prestream_document_tool,
    select_turn_tool_schemas,
    tool_output_event,
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


def test_document_stream_decodes_native_document_delta():
    stream = DocumentStream()

    events = stream.handle_native_delta({
        "arg_delta": '{"title":"Draft","language":"markdown","content":"Hello',
    })
    assert events == [
        {"type": "doc_stream_open", "title": "Draft", "language": "markdown"},
        {"type": "doc_stream_delta", "content": "Hello"},
    ]

    events = stream.handle_native_delta({"arg_delta": '\\nworld"}'})
    assert events == [
        {"type": "doc_stream_delta", "content": "Hello\nworld"},
    ]
    assert stream.started is True


def test_document_stream_tracks_multiple_fenced_document_blocks():
    stream = DocumentStream()

    first = "```create_document\nOne\nmarkdown\nAlpha\n```"
    events = stream.handle_fenced_delta(first)
    assert events == [
        {"type": "doc_stream_open", "title": "One", "language": "markdown"},
        {"type": "doc_stream_delta", "content": "Alpha"},
    ]

    second = first + "\ntext\n```create_document\nTwo\nmarkdown\nBeta\n```"
    events = stream.handle_fenced_delta(second)
    assert events == [
        {"type": "doc_stream_open", "title": "Two", "language": "markdown"},
        {"type": "doc_stream_delta", "content": "Beta"},
    ]


def test_prestream_document_tool_emits_update_document_content():
    events = prestream_document_tool(
        [ToolBlock("update_document", "replacement body")],
        round_num=2,
        doc_stream_started=False,
        tool_policy=None,
    )
    assert events == [
        {"type": "doc_stream_open", "title": "", "language": ""},
        {"type": "doc_stream_delta", "content": "replacement body"},
    ]


def test_tool_turn_stops_before_executing_when_budget_is_exhausted():
    called = False

    async def execute_tool(*_args, **_kwargs):
        nonlocal called
        called = True
        return "bash", {"output": "ran", "exit_code": 0}

    turn = ToolTurn(ToolTurnRequest(
        tool_blocks=[ToolBlock("bash", "echo hi")],
        round_num=3,
        total_tool_calls=1,
        max_tool_calls=1,
        session_id="s",
        disabled_tools=set(),
        tool_policy=None,
        owner=None,
        workspace=None,
        full_response_so_far="",
        effectful_tools={"bash"},
        doc_stream_started=False,
        execute_tool=execute_tool,
        format_tool_result=lambda desc, result: desc,
    ))

    events = _events(_collect(turn.stream()))
    assert called is False
    assert events == [{"type": "budget_exceeded", "limit": 1, "used": 1}]
    assert turn.result.budget_hit is True
    assert turn.result.total_tool_calls == 1


def test_tool_output_event_forwards_images_screenshots_and_diff():
    event = tool_output_event(
        ToolBlock("generate_image", "prompt"),
        {
            "output": "done",
            "exit_code": 0,
            "image_url": "/generated/x.png",
            "image_prompt": "prompt",
            "images": [{"mimeType": "image/png", "data": "abc"}],
            "diff": "--- before\n+++ after",
        },
        "prompt",
    )
    assert event["type"] == "tool_output"
    assert event["tool"] == "generate_image"
    assert event["output"] == "done"
    assert event["image_url"] == "/generated/x.png"
    assert event["image_prompt"] == "prompt"
    assert event["screenshot"] == "data:image/png;base64,abc"
    assert event["diff"] == "--- before\n+++ after"
