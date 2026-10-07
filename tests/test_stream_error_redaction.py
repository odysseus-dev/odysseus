"""Exception details stay in server logs across both chat SSE transports."""

import json
import logging
from uuid import uuid4

import jsonschema
import pytest
from starlette.responses import StreamingResponse

from src import agent_runs
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract
from tests.runtime_evidence_helpers import authoritative_executor


SENSITIVE = (
    "TAKEOVER_SECRET_827_828 /srv/private/credentials.json "
    "https://internal.example/debug?token=PRIVATE_TOKEN "
    '{"request_body":"PRIVATE_BODY"}'
)


async def _client_chunks(generator, detached):
    session = "redaction-" + uuid4().hex
    run = None
    try:
        if detached:
            run = agent_runs.start(session, generator)
            await run.task
            generator = agent_runs.subscribe(session, run)
        response = StreamingResponse(generator, media_type="text/event-stream")
        return [chunk async for chunk in response.body_iterator]
    finally:
        if run is not None:
            if run.evict_task:
                run.evict_task.cancel()
            agent_runs._RUNS.pop(session, None)


def _events(chunks):
    return [
        json.loads(chunk.split("data: ", 1)[1])
        for chunk in chunks if "data: " in chunk and "[DONE]" not in chunk
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("detached", [False, True], ids=["direct-827", "detached-828"])
@pytest.mark.parametrize("boundary", ["calendar", "explicit"])
async def test_native_preemptive_exception_detail_stays_server_side(
    monkeypatch, caplog, detached, boundary,
):
    import src.agent_loop as module

    monkeypatch.setattr(module, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(module, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(module, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(module, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(module, "_agent_route_tool_mode", lambda *args, **kwargs: (True, False, False))
    monkeypatch.setattr(module, "_build_system_prompt", lambda messages, *args, **kwargs: (list(messages), []))
    monkeypatch.setattr(module, "_required_safe_read_operation", lambda contract: None)

    async def execute(*args, **kwargs):
        raise RuntimeError(SENSITIVE)

    async def stream(*args, **kwargs):
        yield 'data: {"delta":"The requested tool failed."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(module, "execute_tool_block", authoritative_executor(execute))
    monkeypatch.setattr(module, "stream_llm_with_fallback", stream)
    tool = "manage_calendar" if boundary == "calendar" else "list_sessions"
    if boundary == "calendar":
        instruction = "What meetings do I have tomorrow?"
        fallback = "preemptive_calendar_lookup"
    else:
        monkeypatch.setattr(module, "_parse_simple_calendar_tool_request", lambda *args: None)
        instruction = "List all chats."
        fallback = "preemptive_explicit_admin_session"
    caplog.set_level(logging.WARNING)
    chunks = await _client_chunks(module.stream_agent_loop(
        "http://model.test/v1", "test-model",
        [{"role": "user", "content": instruction}],
        relevant_tools={tool}, owner="fixture", max_rounds=1, _is_teacher_run=True,
    ), detached)
    tool_event = next((e for e in _events(chunks) if e.get("type") == "tool_output"), None)
    assert tool_event is not None, _events(chunks)
    assert tool_event["fallback"] == fallback
    assert tool_event["exit_code"] == 1
    assert tool_event["output"]
    assert "TAKEOVER_SECRET" not in "".join(chunks)
    assert "/srv/private" not in "".join(chunks)
    assert "PRIVATE_TOKEN" not in "".join(chunks)
    assert "PRIVATE_BODY" not in "".join(chunks)
    assert tool_event["error_category"] == "tool_execution_error"
    assert SENSITIVE in caplog.text


def _preview_provider(monkeypatch, payloads):
    import src.clean_agent_preview as module
    replies = iter(payloads)

    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield "data: " + json.dumps(next(replies))
            yield "data: [DONE]"

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    return module


def _preview_generator(module, schemas=()):
    policy = ToolPolicy()
    contract = resolve_full_inventory_contract(schemas=list(schemas), policy=policy)
    return module.stream_preview(
        endpoint_url="http://model.test", model="test", headers={},
        messages=[{"role": "user", "content": "List my notes."}],
        turn_contract=contract, session_id="fixture-redaction", owner="fixture",
        disabled_tools=set(), tool_policy=policy,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("detached", [False, True], ids=["direct-827", "detached-828"])
@pytest.mark.parametrize("shape", ["string", "message", "detail"])
async def test_preview_provider_detail_stays_server_side(monkeypatch, caplog, detached, shape):
    error = SENSITIVE if shape == "string" else {shape: SENSITIVE}
    module = _preview_provider(monkeypatch, [{"error": error}])
    caplog.set_level(logging.WARNING)
    chunks = await _client_chunks(_preview_generator(module), detached)
    assert chunks[-1].startswith("event: error\n")
    payload = _events(chunks)[-1]
    assert payload["status"] == 502
    assert "provider" in payload["error"]
    assert SENSITIVE not in "".join(chunks)
    assert "/srv/private" not in "".join(chunks)
    assert "PRIVATE_TOKEN" not in "".join(chunks)
    assert payload["error_category"] == "provider_stream_error"
    assert SENSITIVE in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("detached", [False, True], ids=["direct-827", "detached-828"])
@pytest.mark.parametrize("failure", ["execution", "schema", "json", "arguments"])
async def test_preview_tool_exception_detail_stays_server_side(
    monkeypatch, caplog, detached, failure,
):
    module = _preview_provider(monkeypatch, [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {
            "name": "manage_notes", "arguments": '{"action":"list"}',
        }}]}}]},
        {"choices": [{"delta": {"content": "The requested call failed."}}]},
    ] * 8)

    async def execute(*args, **kwargs):
        raise ValueError(SENSITIVE)

    def fail(*args, **kwargs):
        if failure == "schema":
            raise jsonschema.ValidationError(SENSITIVE, instance={"private": SENSITIVE})
        if failure == "json":
            raise json.JSONDecodeError(SENSITIVE, SENSITIVE, 0)
        raise ValueError(SENSITIVE)

    if failure == "execution":
        monkeypatch.setattr(module, "execute_tool_block", execute)
    elif failure == "schema":
        monkeypatch.setattr(module.jsonschema, "validate", fail)
    else:
        monkeypatch.setattr(module, "normalize_preview_call_args", fail)
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == "manage_notes")
    caplog.set_level(logging.WARNING)
    chunks = await _client_chunks(_preview_generator(module, [schema]), detached)
    tool_event = next(e for e in _events(chunks) if e.get("type") == "tool_output")
    assert tool_event["error"] is True
    assert tool_event["exit_code"] == 1
    assert tool_event["output"]
    assert "TAKEOVER_SECRET" not in "".join(chunks)
    assert "/srv/private" not in "".join(chunks)
    assert "PRIVATE_TOKEN" not in "".join(chunks)
    assert "PRIVATE_BODY" not in "".join(chunks)
    assert tool_event["error_category"] == (
        "tool_execution_error" if failure == "execution" else "invalid_tool_arguments"
    )
    assert SENSITIVE in caplog.text


@pytest.mark.parametrize("message", [
    "Tool is not offered or permitted.",
    "Tool arguments must be a JSON object.",
    "This operation is outside the preview safety policy. No change was made.",
    "Resolve the named recipient with resolve_contact before drafting. Never invent an email address.",
    "The calendar read has not succeeded yet. Obtain the requested calendar evidence before creating the dependent email draft.",
])
def test_curated_domain_errors_remain_useful(message):
    from src.clean_agent_preview import _public_preview_tool_error
    assert _public_preview_tool_error(ValueError(message)) == message
    assert message not in _public_preview_tool_error(ValueError(message), execution_attempted=True)


@pytest.mark.parametrize("detail", [
    SENSITIVE,
    "Tool is not offered or permitted.\n" + SENSITIVE,
    "Shell access to credential variable " + SENSITIVE,
    "Artifact completion Python must reference the required " + SENSITIVE,
])
def test_untrusted_validation_detail_cannot_masquerade_as_curated_guidance(detail):
    from src.clean_agent_preview import _public_preview_tool_error
    public = _public_preview_tool_error(ValueError(detail))
    assert public
    assert "TAKEOVER_SECRET" not in public
    assert "/srv/private" not in public
    assert "PRIVATE_TOKEN" not in public
