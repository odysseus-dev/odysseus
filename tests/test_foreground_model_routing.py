"""Regression coverage for strict foreground model selection."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
import core.database as database
import src.agent_loop as agent_loop
import src.endpoint_resolver as endpoint_resolver
import src.foreground_model_routing as foreground_model_routing
import src.llm_core as llm_core
import routes.chat_routes as chat_routes
import routes.chat_helpers as chat_helpers
import routes.prefs_routes as prefs_routes
from src.request_models import ChatRequest
from src.tool_approvals import document_content_digest
from src.foreground_model_routing import (
    FOREGROUND_AVAILABILITY_STATUSES,
    MAX_FOREGROUND_FALLBACKS,
    ForegroundModelPolicy,
    build_foreground_model_candidates,
    resolve_foreground_model_policy,
)


def _collect(gen):
    async def _run():
        return [chunk async for chunk in gen]

    return asyncio.run(_run())


class _EmptyQuery:
    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return None


class _EmptyDb:
    def query(self, *args, **kwargs):
        return _EmptyQuery()

    def close(self):
        return None


class _RouteRequest:
    def __init__(self, mode, privileges=None):
        self.headers = {}
        auth_manager = None
        if privileges is not None:
            auth_manager = SimpleNamespace(get_privileges=lambda user: privileges)
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager))
        self.state = SimpleNamespace(current_user="alice")
        self._form = {
            "message": "hello",
            "session": "session-1",
            "mode": mode,
            "compare_mode": "true",
        }

    async def form(self):
        return self._form


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, 429),
        ("503", 503),
        (429.9, None),
        (True, None),
        ("429.9", None),
    ],
)
def test_stream_failure_status_uses_exact_http_statuses(status, expected):
    chunk = f'event: error\ndata: {json.dumps({"status": status})}\n\n'
    assert chat_routes._stream_failure_status(chunk) == expected


def _chat_stream_endpoint(
    monkeypatch,
    mode,
    captured,
    *,
    agent_chunks=None,
    chat_chunks=None,
    capture_completion=False,
    capture_context=False,
    endpoint_url="https://selected.example/v1",
):
    def add_message(message):
        captured.setdefault("added_messages", []).append(message)

    session = SimpleNamespace(
        endpoint_url=endpoint_url,
        model="selected-model",
        headers={"Authorization": "Bearer selected"},
        name="test",
        history=[],
        add_message=add_message,
    )
    session_manager = SimpleNamespace(
        get_session=lambda session_id: session,
        save_sessions=lambda: None,
    )
    context = SimpleNamespace(
        user="alice",
        messages=[{"role": "user", "content": "hello"}],
        route_messages=[
            {"role": "user", "content": "old one"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "hello"},
        ],
        preprocessed=SimpleNamespace(attachment_meta=[]),
        auto_opened_docs=[],
        rag_sources=[],
        web_sources=[],
        used_memories=[],
        uploaded_files=[],
        uprefs={},
        was_compacted=False,
        context_trimmed=False,
        context_length=4096,
        context_messages_before_trim=1,
        context_messages_after_trim=1,
        context_tokens_before_trim=10,
        context_tokens_after_trim=10,
        preset=SimpleNamespace(temperature=0.2, max_tokens=128, character_name=None),
    )

    async def fake_build_context(*args, **kwargs):
        if capture_context:
            captured["build_context"] = kwargs
        return context

    async def fake_chat_stream(candidates, messages, **kwargs):
        captured["chat"] = candidates
        if chat_chunks is not None:
            for chunk in chat_chunks:
                if isinstance(chunk, BaseException):
                    raise chunk
                yield chunk
            return
        yield f'data: {json.dumps({"delta": "done"})}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_agent_stream(endpoint_url, model, messages, **kwargs):
        captured["agent"] = {
            "primary": (endpoint_url, model, kwargs.get("headers")),
            "fallbacks": kwargs.get("fallbacks"),
        }
        if kwargs.get("external_untrusted_context_seen"):
            captured["agent_external_untrusted_context_seen"] = True
        if kwargs.get("exact_approval") is not None:
            captured["exact_approval"] = kwargs["exact_approval"]
            captured["approval_disabled_tools"] = set(
                kwargs.get("disabled_tools") or ()
            )
        if agent_chunks is not None:
            for chunk in agent_chunks:
                if isinstance(chunk, BaseException):
                    raise chunk
                yield chunk
            return
        yield f'data: {json.dumps({"delta": "done"})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "coerce_message_and_session", lambda *args, **kwargs: ("hello", "session-1"))
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "effective_user", lambda request: "alice")
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "resolve_session_auth", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "get_session_mode", lambda session_id: "chat")
    monkeypatch.setattr(chat_routes, "set_session_mode", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "build_chat_context", fake_build_context)
    monkeypatch.setattr(chat_routes, "SessionLocal", _EmptyDb)
    monkeypatch.setattr(chat_routes, "_is_image_generation_session", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "stream_llm_with_fallback", fake_chat_stream)
    monkeypatch.setattr(chat_routes, "stream_agent_loop", fake_agent_stream)
    if capture_completion:
        monkeypatch.setattr(
            chat_routes,
            "save_assistant_response",
            lambda *args, **kwargs: captured.setdefault("saved", []).append((args, kwargs)),
        )
        monkeypatch.setattr(
            chat_routes,
            "run_post_response_tasks",
            lambda *args, **kwargs: captured.setdefault("post_processed", []).append((args, kwargs)),
        )
    else:
        monkeypatch.setattr(chat_routes, "save_assistant_response", lambda *args, **kwargs: None)
        monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "estimate_tokens", lambda messages: 10)
    monkeypatch.setattr(
        chat_routes,
        "accumulate_token_usage",
        lambda *args, **kwargs: captured.setdefault("accumulated_usage", []).append((args, kwargs)),
    )
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner=None: {
        "default_model_fallbacks": [
            {"endpoint_id": "legacy", "model": "legacy-model"},
        ],
    })

    import src.settings as settings

    monkeypatch.setattr(
        settings,
        "get_setting",
        lambda key, default=None: default,
    )
    monkeypatch.setattr(
        settings,
        "get_user_setting",
        lambda key, owner="", default=None: (
            [{"endpoint_id": "legacy", "model": "legacy-model"}]
            if key == "default_model_fallbacks"
            else default
        ),
    )

    router = chat_routes.setup_chat_routes(
        session_manager,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    return next(route.endpoint for route in router.routes if route.path == "/api/chat_stream")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "agent"])
async def test_chat_stream_route_keeps_selected_model_strict_with_legacy_data(monkeypatch, mode):
    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, mode, captured)

    response = await endpoint(_RouteRequest(mode))
    async for _ in response.body_iterator:
        pass

    selected = (
        "https://selected.example/v1",
        "selected-model",
        {"Authorization": "Bearer selected"},
    )
    if mode == "chat":
        assert captured == {"chat": [selected]}
    else:
        assert captured == {"agent": {"primary": selected, "fallbacks": []}}


@pytest.mark.asyncio
async def test_chat_stream_consumes_exact_tool_approval_for_own_session(monkeypatch):
    from src.tool_capabilities import capabilities_for_action

    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "agent", captured)
    tool_content = '{"content":"replacement"}'
    pending = chat_routes.tool_approval_store.create(
        owner="alice",
        session_id="session-1",
        origin_run_id="run-1",
        tool_name="update_document",
        content=tool_content,
        workspace=None,
        document_id="document-7",
        document_version=4,
        document_digest=document_content_digest("original"),
        external_untrusted_context_seen=True,
        capabilities=capabilities_for_action("update_document", tool_content),
    )
    request = _RouteRequest("agent")
    request._form.update(
        {
            "tool_approval_id": pending.approval_id,
            "tool_approval_decision": "approve",
            "active_doc_id": "document-changed-in-browser",
            "compare_mode": "false",
        }
    )

    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass

    grant = captured["exact_approval"]
    assert grant.pending == pending
    assert chat_routes.tool_approval_store.peek(pending.approval_id) is None
    assert grant.matches(
        owner="alice",
        session_id="session-1",
        tool_name="update_document",
        content=tool_content,
        workspace=None,
    )
    assert "update_document" not in captured["approval_disabled_tools"]


@pytest.mark.asyncio
async def test_chat_stream_approval_restores_exact_shell_turn_toggle(monkeypatch):
    from src.tool_capabilities import capabilities_for_action

    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "agent", captured)
    pending = chat_routes.tool_approval_store.create(
        owner="alice",
        session_id="session-1",
        origin_run_id="run-1",
        tool_name="bash",
        content="printf exact",
        workspace=None,
        external_untrusted_context_seen=True,
        capabilities=capabilities_for_action("bash", "printf exact"),
    )
    request = _RouteRequest("chat")
    request._form.update(
        {
            "allow_bash": "false",
            "tool_approval_id": pending.approval_id,
            "tool_approval_decision": "approve",
        }
    )

    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass

    assert captured["exact_approval"].pending == pending
    assert "bash" not in captured["approval_disabled_tools"]


@pytest.mark.asyncio
async def test_chat_stream_denial_returns_control_resolution(monkeypatch):
    from src.tool_capabilities import capabilities_for_action

    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "agent", captured)
    pending = chat_routes.tool_approval_store.create(
        owner="alice",
        session_id="session-1",
        origin_run_id="run-1",
        tool_name="bash",
        content="printf retry",
        workspace=None,
        external_untrusted_context_seen=True,
        capabilities=capabilities_for_action("bash", "printf retry"),
    )
    request = _RouteRequest("agent")
    request._form.update(
        {
            "tool_approval_id": pending.approval_id,
            "tool_approval_decision": "deny",
        }
    )

    response = await endpoint(request)
    chunks = [chunk async for chunk in response.body_iterator]

    event = json.loads(chunks[0][len("data: "):])
    assert event == {"type": "tool_approval_resolved", "decision": "deny"}
    assert chunks[-1] == "data: [DONE]\n\n"
    assert "agent" not in captured
    assert "exact_approval" not in captured
    assert chat_routes.tool_approval_store.peek(pending.approval_id) is None


@pytest.mark.asyncio
async def test_chat_stream_normal_reply_retires_pending_action_but_keeps_taint(
    monkeypatch,
):
    from src.tool_capabilities import capabilities_for_action

    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "agent", captured)
    pending = chat_routes.tool_approval_store.create(
        owner="alice",
        session_id="session-1",
        origin_run_id="run-1",
        tool_name="bash",
        content="printf retry",
        workspace=None,
        external_untrusted_context_seen=True,
        capabilities=capabilities_for_action("bash", "printf retry"),
    )

    response = await endpoint(_RouteRequest("agent"))
    async for _ in response.body_iterator:
        pass

    assert "exact_approval" not in captured
    assert captured["agent_external_untrusted_context_seen"] is True
    assert chat_routes.tool_approval_store.peek(pending.approval_id) is None


@pytest.mark.asyncio
async def test_chat_stream_approval_ignores_research_and_new_attachments(monkeypatch):
    from src.tool_capabilities import capabilities_for_action

    captured = {}
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        "agent",
        captured,
        capture_context=True,
    )
    monkeypatch.setattr(chat_routes, "get_session_mode", lambda _session_id: "research_pending")
    pending = chat_routes.tool_approval_store.create(
        owner="alice",
        session_id="session-1",
        origin_run_id="run-1",
        tool_name="bash",
        content="printf exact",
        workspace=None,
        external_untrusted_context_seen=True,
        capabilities=capabilities_for_action("bash", "printf exact"),
    )
    request = _RouteRequest("agent")
    request._form.update(
        {
            "attachments": '["unrelated-upload"]',
            "use_research": "true",
            "tool_approval_id": pending.approval_id,
            "tool_approval_decision": "approve",
        }
    )

    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass

    assert captured["exact_approval"].pending == pending
    assert captured["build_context"]["att_ids"] == []
    assert "agent" in captured
    assert "chat" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "agent"])
@pytest.mark.parametrize("endpoint_url", ["", None])
async def test_chat_stream_rejects_missing_selected_endpoint_before_fallback(
    monkeypatch,
    mode,
    endpoint_url,
):
    captured = {}
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        mode,
        captured,
        endpoint_url=endpoint_url,
    )

    with pytest.raises(HTTPException) as exc:
        await endpoint(_RouteRequest(mode))

    assert exc.value.status_code == 400
    assert "not configured" in str(exc.value.detail)
    assert captured == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "agent"])
async def test_chat_stream_route_uses_only_new_explicit_fallback_policy(monkeypatch, mode):
    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, mode, captured)
    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner=None: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": [
                {"endpoint_id": "backup", "model": "backup-model"},
            ],
            "default_model_fallbacks": [
                {"endpoint_id": "legacy", "model": "legacy-model"},
            ],
        },
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda entries, owner=None, require_exact_model=False: [
            ("https://backup.example/v1", "backup-model", {"Authorization": "Bearer backup"}),
        ],
    )

    response = await endpoint(_RouteRequest(mode))
    async for _ in response.body_iterator:
        pass

    selected = (
        "https://selected.example/v1",
        "selected-model",
        {"Authorization": "Bearer selected"},
    )
    backup = (
        "https://backup.example/v1",
        "backup-model",
        {"Authorization": "Bearer backup"},
    )
    if mode == "chat":
        assert captured == {"chat": [selected, backup]}
    else:
        assert captured == {"agent": {"primary": selected, "fallbacks": [backup]}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary_context", "backup_context", "expected_counts"),
    [
        (100, 1000, (1, 3)),
        (1000, 100, (3, 1)),
    ],
)
async def test_streaming_chat_shapes_each_candidate_from_route_neutral_history(
    monkeypatch,
    primary_context,
    backup_context,
    expected_counts,
):
    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "chat", captured)
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner=None: {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "backup", "model": "backup-model"},
        ],
    })
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: [("https://backup.example/v1", "backup-model", {})],
    )
    async def fake_compact(
        session, url, model, messages, headers=None, owner=None, **kwargs
    ):
        return (
            list(messages),
            backup_context if model == "backup-model" else primary_context,
            False,
        )

    monkeypatch.setattr(chat_routes, "maybe_compact", fake_compact)
    monkeypatch.setattr(
        chat_routes,
        "trim_for_context",
        lambda messages, budget: list(messages) if budget >= 1000 else list(messages[-1:]),
    )

    async def fake_stream(candidates, messages, **kwargs):
        factory = kwargs["candidate_request_factory"]
        requests = [
            await factory(index, *candidate)
            for index, candidate in enumerate(candidates)
        ]
        captured["request_counts"] = tuple(
            len(request["messages"]) for request in requests
        )
        yield 'data: {"type": "fallback", "candidate_index": 1, "selected_model": "selected-model", "answered_by": "backup-model"}\n\n'
        yield 'data: {"delta": "backup"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_llm_with_fallback", fake_stream)

    response = await endpoint(_RouteRequest("chat"))
    async for _chunk in response.body_iterator:
        pass

    assert captured["request_counts"] == expected_counts


@pytest.mark.asyncio
async def test_streaming_chat_persists_only_answering_route_compaction(monkeypatch):
    captured = {}
    applied = []
    endpoint = _chat_stream_endpoint(monkeypatch, "chat", captured)
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner=None: {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "backup", "model": "backup-model"},
        ],
    })
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: [("https://backup.example/v1", "backup-model", {})],
    )

    async def fake_compact(
        session, url, model, messages, headers=None, owner=None,
        *, persist=True, compaction_state=None,
    ):
        assert persist is False
        compaction_state.update({"route": model, "applied": False})
        return ([{"role": "system", "content": f"summary for {model}"}, *messages], 1000, True)

    def fake_apply(session, state):
        if not state or state.get("applied"):
            return False
        state["applied"] = True
        applied.append(state["route"])
        return True

    async def fake_stream(candidates, messages, **kwargs):
        factory = kwargs["candidate_request_factory"]
        for index, candidate in enumerate(candidates):
            await factory(index, *candidate)
        yield 'data: {"type": "fallback", "candidate_index": 1, "selected_model": "selected-model", "answered_by": "backup-model"}\n\n'
        yield 'data: {"delta": "backup"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "maybe_compact", fake_compact)
    monkeypatch.setattr(chat_routes, "apply_compaction_state", fake_apply)
    monkeypatch.setattr(chat_routes, "stream_llm_with_fallback", fake_stream)

    response = await endpoint(_RouteRequest("chat"))
    chunks = [chunk async for chunk in response.body_iterator]

    assert applied == ["backup-model"]
    assert any('"type": "compacted"' in chunk for chunk in chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selected_url", "selected_cost_tracked", "backup_url", "expected_cost_tracked"),
    [
        ("http://localhost:11434/v1", False, "https://backup.example/v1", True),
        ("https://selected.example/v1", True, "http://localhost:11434/v1", False),
    ],
)
async def test_streaming_chat_cost_uses_answering_route_classification(
    monkeypatch,
    selected_url,
    selected_cost_tracked,
    backup_url,
    expected_cost_tracked,
):
    captured = {}
    chunks = [
        'data: {"type": "fallback", "candidate_index": 1, "selected_model": "selected-model", "answered_by": "backup-model"}\n\n',
        'data: {"type": "usage", "data": {"model": "backup-model", "input_tokens": 20, "output_tokens": 5}}\n\n',
        'data: {"delta": "backup answer"}\n\n',
        "data: [DONE]\n\n",
    ]
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        "chat",
        captured,
        chat_chunks=chunks,
        capture_completion=True,
        endpoint_url=selected_url,
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner=None: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": [
                {"endpoint_id": "backup", "model": "backup-model"},
            ],
        },
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: [
            (backup_url, "backup-model", {}),
        ],
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_route_descriptor",
        lambda *args, **kwargs: {
            "endpoint_id": "selected",
            "endpoint_label": "Selected local endpoint",
            "endpoint_cost_tracked": selected_cost_tracked,
        },
    )

    response = await endpoint(_RouteRequest("chat"))
    emitted = [chunk async for chunk in response.body_iterator]

    metrics = json.loads(next(
        chunk for chunk in emitted if '"type": "metrics"' in chunk
    )[6:])["data"]
    assert metrics["endpoint_id"] == "backup"
    assert metrics["endpoint_cost_tracked"] is expected_cost_tracked
    saved_args, _saved_kwargs = captured["saved"][0]
    assert saved_args[4]["endpoint_cost_tracked"] is expected_cost_tracked


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_cost_tracked", [False, True])
async def test_streaming_chat_persists_selected_route_cost_classification(
    monkeypatch,
    selected_cost_tracked,
):
    captured = {}
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        "chat",
        captured,
        chat_chunks=[
            'data: {"type": "usage", "data": {"model": "selected-model", "input_tokens": 20, "output_tokens": 5}}\n\n',
            'data: {"delta": "selected answer"}\n\n',
            "data: [DONE]\n\n",
        ],
        capture_completion=True,
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_route_descriptor",
        lambda *args, **kwargs: {
            "endpoint_id": "selected",
            "endpoint_label": "Selected endpoint",
            "endpoint_cost_tracked": selected_cost_tracked,
        },
    )

    response = await endpoint(_RouteRequest("chat"))
    emitted = [chunk async for chunk in response.body_iterator]

    metrics = json.loads(next(
        chunk for chunk in emitted if '"type": "metrics"' in chunk
    )[6:])["data"]
    assert metrics["endpoint_cost_tracked"] is selected_cost_tracked
    saved_args, _saved_kwargs = captured["saved"][0]
    assert saved_args[4]["endpoint_cost_tracked"] is selected_cost_tracked


@pytest.mark.asyncio
async def test_chat_stream_route_does_not_save_or_postprocess_terminal_agent_error(monkeypatch):
    captured = {}
    error_chunk = 'event: error\ndata: {"status": 401, "error": "invalid key"}\n\n'
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        "agent",
        captured,
        agent_chunks=[error_chunk],
        capture_completion=True,
    )

    response = await endpoint(_RouteRequest("agent"))
    chunks = [chunk async for chunk in response.body_iterator]

    assert error_chunk in chunks
    assert "saved" not in captured
    assert "post_processed" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_status", "expected_status", "expected_message"),
    [
        (401, 401, "Model request failed (HTTP 401)"),
        (429.9, None, "Model request failed"),
    ],
)
async def test_chat_stream_persists_completed_tools_before_later_terminal_error(
    monkeypatch,
    provider_status,
    expected_status,
    expected_message,
):
    captured = {}
    terminal_metadata = {
        "failed": True,
        "failure": {
            "status": provider_status,
            "message": "credential-shaped provider detail",
        },
        "model": "backup-model",
        "requested_model": "selected-model",
        "endpoint_id": "backup-endpoint",
        "endpoint_label": "Backup endpoint",
        "tool_events": [
            {"round": 1, "tool": "bash", "output": "created", "exit_code": 0},
        ],
        "round_texts": ["partial answer"],
        "round_models": ["backup-model"],
        "round_endpoint_ids": ["backup-endpoint"],
        "round_endpoint_labels": ["Backup endpoint"],
        "input_tokens": 75,
        "output_tokens": 15,
        "usage_source": "real",
        "endpoint_cost_tracked": True,
    }
    chunks = [
        'data: {"delta": "partial answer"}\n\n',
        f'data: {json.dumps({"type": "agent_terminal", "data": terminal_metadata})}\n\n',
        f'event: error\ndata: {json.dumps({"status": provider_status, "error": "invalid key"})}\n\n',
    ]
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        "agent",
        captured,
        agent_chunks=chunks,
        capture_completion=True,
    )

    response = await endpoint(_RouteRequest("agent"))
    emitted = [chunk async for chunk in response.body_iterator]

    assert any(chunk.startswith("event: error") for chunk in emitted)
    assert not any(chunk == "data: [DONE]\n\n" for chunk in emitted)
    assert len(captured["saved"]) == 1
    saved_args, _saved_kwargs = captured["saved"][0]
    assert "partial answer" in saved_args[3]
    assert f"Agent stopped: {expected_message}" in saved_args[3]
    assert "credential-shaped provider detail" not in saved_args[3]
    assert saved_args[4]["failure"] == {
        "status": expected_status,
        "message": expected_message,
    }
    assert saved_args[4]["failed"] is True
    assert saved_args[4]["tool_events"][0]["output"] == "created"
    assert captured["accumulated_usage"][0][0][1] == saved_args[4]
    assert saved_args[4]["input_tokens"] == 75
    assert saved_args[4]["output_tokens"] == 15
    assert "post_processed" not in captured
    assert all(chunk != "data: [DONE]\n\n" for chunk in chunks)


@pytest.mark.asyncio
async def test_chat_stream_persists_partial_terminal_error_with_route_provenance(monkeypatch):
    captured = {}
    chunks = [
        'data: {"type": "fallback", "candidate_index": 1, "selected_model": "selected-model", "answered_by": "backup-model", "answered_by_endpoint_id": "backup", "answered_by_endpoint_label": "Backup endpoint"}\n\n',
        'data: {"delta": "visible partial"}\n\n',
        'event: error\ndata: {"status": 503, "error": "credential-shaped provider detail"}\n\n',
        "data: [DONE]\n\n",
    ]
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        "chat",
        captured,
        chat_chunks=chunks,
        capture_completion=True,
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner=None: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": [
                {"endpoint_id": "backup", "model": "backup-model"},
            ],
        },
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: [
            ("https://backup.example/v1", "backup-model", {"Authorization": "Bearer backup"}),
        ],
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_route_descriptor",
        lambda *args, **kwargs: {
            "endpoint_id": "selected",
            "endpoint_label": "Selected endpoint",
        },
    )

    response = await endpoint(_RouteRequest("chat"))
    emitted = [chunk async for chunk in response.body_iterator]

    assert any(chunk.startswith("event: error") for chunk in emitted)
    assert not any(chunk == "data: [DONE]\n\n" for chunk in emitted)
    assert len(captured["saved"]) == 1
    saved_args, _saved_kwargs = captured["saved"][0]
    assert saved_args[3] == (
        "visible partial\n\n"
        "[Response stopped: Model request failed (HTTP 503)]"
    )
    assert "credential-shaped provider detail" not in str(saved_args)
    assert saved_args[4]["failure"] == {
        "status": 503,
        "message": "Model request failed (HTTP 503)",
    }
    assert saved_args[4]["model"] == "backup-model"
    assert saved_args[4]["requested_model"] == "selected-model"
    assert saved_args[4]["endpoint_id"] == "backup"
    assert saved_args[4]["endpoint_label"] == "backup"
    assert saved_args[4]["requested_endpoint_id"] == "selected"
    assert saved_args[4]["requested_endpoint_label"] == "Selected endpoint"
    assert saved_args[4]["endpoint_cost_tracked"] is True
    assert saved_args[4]["input_tokens"] == 10
    assert saved_args[4]["output_tokens"] == len("visible partial") // 4
    assert saved_args[4]["usage_source"] == "estimated"
    assert captured["accumulated_usage"][0][0][1] == saved_args[4]
    chat_terminal = json.loads(next(
        chunk for chunk in emitted if '"type": "chat_terminal"' in chunk
    )[6:])["data"]
    assert chat_terminal == saved_args[4]
    assert "post_processed" not in captured


@pytest.mark.asyncio
async def test_chat_terminal_preserves_real_usage_and_accumulates_once(monkeypatch):
    captured = {}
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        "chat",
        captured,
        chat_chunks=[
            'data: {"type": "usage", "data": {"model": "selected-model", "input_tokens": 123, "output_tokens": 17, "usage_source": "real"}}\n\n',
            'data: {"delta": "visible partial"}\n\n',
            'event: error\ndata: {"status": 503, "error": "provider detail"}\n\n',
        ],
        capture_completion=True,
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_route_descriptor",
        lambda *args, **kwargs: {
            "endpoint_id": "selected",
            "endpoint_label": "Selected paid endpoint",
            "endpoint_cost_tracked": True,
        },
    )

    response = await endpoint(_RouteRequest("chat"))
    emitted = [chunk async for chunk in response.body_iterator]

    saved_metrics = captured["saved"][0][0][4]
    assert saved_metrics["input_tokens"] == 123
    assert saved_metrics["output_tokens"] == 17
    assert saved_metrics["usage_source"] == "real"
    assert saved_metrics["endpoint_cost_tracked"] is True
    assert saved_metrics["failed"] is True
    assert len(captured["accumulated_usage"]) == 1
    assert captured["accumulated_usage"][0][0][1] == saved_metrics
    assert len([
        chunk for chunk in emitted if '"type": "metrics"' in chunk
    ]) == 1
    assert len([
        chunk for chunk in emitted if '"type": "chat_terminal"' in chunk
    ]) == 1
    assert "post_processed" not in captured


@pytest.mark.asyncio
async def test_cancelled_agent_fallback_saves_endpoint_and_round_provenance(monkeypatch):
    captured = {}
    chunks = [
        'data: {"type": "model_actual", "round": 1, "model": "selected-provider-alias"}\n\n',
        'data: {"type": "agent_step", "round": 2}\n\n',
        'data: {"type": "fallback", "round": 2, "selected_model": "selected-model", "answered_by": "backup-model", "answered_by_endpoint_id": "account-two", "answered_by_endpoint_label": "Account two"}\n\n',
        'data: {"delta": "partial answer"}\n\n',
        asyncio.CancelledError(),
    ]
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        "agent",
        captured,
        agent_chunks=chunks,
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner=None: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": [
                {"endpoint_id": "account-two", "model": "selected-model"},
            ],
        },
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: [
            ("https://backup.example/v1", "selected-model", {"Authorization": "Bearer two"}),
        ],
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_route_descriptor",
        lambda *args, **kwargs: {
            "endpoint_id": "account-one",
            "endpoint_label": "Account one",
        },
    )

    response = await endpoint(_RouteRequest("agent"))
    with pytest.raises(asyncio.CancelledError):
        async for _chunk in response.body_iterator:
            pass

    saved = captured["added_messages"][-1]
    assert saved.metadata["requested_endpoint_id"] == "account-one"
    assert saved.metadata["endpoint_id"] == "account-two"
    assert saved.metadata["model"] == "backup-model"
    assert saved.metadata["round_models"] == ["selected-provider-alias", "backup-model"]
    assert saved.metadata["round_endpoint_ids"] == ["account-one", "account-two"]
    assert saved.metadata["round_endpoint_labels"] == ["Account one", "Account two"]


@pytest.mark.asyncio
async def test_cancelled_chat_fallback_saves_same_model_endpoint_provenance(monkeypatch):
    captured = {}
    chunks = [
        'data: {"type": "fallback", "candidate_index": 1, "selected_model": "selected-model", "answered_by": "selected-model", "answered_by_endpoint_id": "account-two", "answered_by_endpoint_label": "Account two"}\n\n',
        'data: {"delta": "partial answer"}\n\n',
        asyncio.CancelledError(),
    ]
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        "chat",
        captured,
        chat_chunks=chunks,
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner=None: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": [
                {"endpoint_id": "account-two", "model": "selected-model"},
            ],
        },
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: [
            ("https://backup.example/v1", "selected-model", {"Authorization": "Bearer two"}),
        ],
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_route_descriptor",
        lambda *args, **kwargs: {
            "endpoint_id": "account-one",
            "endpoint_label": "Account one",
        },
    )

    response = await endpoint(_RouteRequest("chat"))
    with pytest.raises(asyncio.CancelledError):
        async for _chunk in response.body_iterator:
            pass

    saved = captured["added_messages"][-1]
    assert saved.metadata["requested_endpoint_id"] == "account-one"
    assert saved.metadata["endpoint_id"] == "account-two"
    assert saved.metadata["requested_endpoint_label"] == "Account one"
    assert saved.metadata["endpoint_label"] == "account-two"


@pytest.mark.asyncio
async def test_chat_stream_route_excludes_fallback_outside_non_admin_allowlist(monkeypatch):
    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "chat", captured)
    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner=None: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": [
                {"endpoint_id": "backup", "model": "blocked-model"},
            ],
        },
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: pytest.fail("unauthorized fallback reached endpoint resolution"),
    )

    response = await endpoint(_RouteRequest("chat", privileges={
        "allowed_models": ["selected-model"],
        "allowed_models_restricted": True,
        "max_messages_per_day": 0,
    }))
    async for _ in response.body_iterator:
        pass

    assert captured == {"chat": [(
        "https://selected.example/v1",
        "selected-model",
        {"Authorization": "Bearer selected"},
    )]}


class _NonStreamChatHandler:
    async def handle_memory_command(self, sess, message):
        return None


def _chat_endpoint(
    monkeypatch,
    *,
    owner="alice",
    endpoint_url="https://selected.example/v1",
):
    saved = []
    session = SimpleNamespace(
        endpoint_url=endpoint_url,
        model="selected-model",
        headers={"Authorization": "Bearer selected"},
        history=[],
        add_message=saved.append,
    )
    session_manager = SimpleNamespace(
        get_session=lambda session_id: session,
        save_sessions=lambda: None,
    )
    context = SimpleNamespace(
        user=owner,
        messages=[{"role": "user", "content": "hello"}],
        route_messages=[
            {"role": "user", "content": "old one"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "hello"},
        ],
        context_length=100,
        uprefs={},
        preset=SimpleNamespace(
            temperature=0.2,
            max_tokens=128,
            character_name=None,
        ),
    )

    async def fake_build_context(*args, **kwargs):
        return context

    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "effective_user", lambda request: owner)
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "build_chat_context", fake_build_context)
    monkeypatch.setattr(chat_routes, "clean_thinking_for_save", lambda reply, metadata: (reply, metadata))
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *args, **kwargs: None)

    import core.database as database

    monkeypatch.setattr(database, "update_session_last_accessed", lambda session_id: None)

    router = chat_routes.setup_chat_routes(
        session_manager,
        _NonStreamChatHandler(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/chat")
    return endpoint, saved


@pytest.mark.asyncio
async def test_nonstream_chat_is_strict_by_default_and_reports_selected_route(monkeypatch):
    calls = []
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner: {})
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: pytest.fail("strict non-stream Chat resolved fallback entries"),
    )

    async def fake_call(url, model, messages, **kwargs):
        calls.append((url, model, kwargs.get("headers")))
        return "selected answer"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    endpoint, saved = _chat_endpoint(monkeypatch)

    response = await endpoint(
        _RouteRequest("chat"),
        ChatRequest(message="hello", session="session-1"),
    )

    assert calls == [(
        "https://selected.example/v1",
        "selected-model",
        {"Authorization": "Bearer selected"},
    )]
    assert response == {
        "response": "selected answer",
        "requested_model": "selected-model",
        "model": "selected-model",
        "requested_endpoint_id": None,
        "requested_endpoint_label": "Selected route",
        "endpoint_id": None,
        "endpoint_label": "Selected route",
    }
    assert saved[-1].metadata == {
        "model": "selected-model",
        "requested_model": "selected-model",
        "endpoint_id": None,
        "endpoint_label": "Selected route",
        "requested_endpoint_id": None,
        "requested_endpoint_label": "Selected route",
        "context_length": 100,
        "context_trimmed": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint_url", ["", None])
async def test_nonstream_chat_rejects_missing_selected_endpoint_before_fallback(
    monkeypatch,
    endpoint_url,
):
    endpoint, saved = _chat_endpoint(monkeypatch, endpoint_url=endpoint_url)

    with pytest.raises(HTTPException) as exc:
        await endpoint(
            _RouteRequest("chat"),
            ChatRequest(message="hello", session="session-1"),
        )

    assert exc.value.status_code == 400
    assert "not configured" in str(exc.value.detail)
    assert saved == []


@pytest.mark.asyncio
async def test_nonstream_chat_opt_in_advances_only_on_eligible_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner: {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "backup", "model": "backup-model"},
        ],
    })
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda entries, owner=None, require_exact_model=False: [
            ("https://backup.example/v1", "backup-model", {"Authorization": "Bearer backup"}),
        ],
    )

    async def fake_call(url, model, messages, **kwargs):
        calls.append((url, model, kwargs.get("headers")))
        if model == "selected-model":
            raise HTTPException(503, "selected unavailable")
        return "backup answer"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    endpoint, saved = _chat_endpoint(monkeypatch)

    response = await endpoint(
        _RouteRequest("chat"),
        ChatRequest(message="hello", session="session-1"),
    )

    assert [call[1] for call in calls] == ["selected-model", "backup-model"]
    assert response == {
        "response": "backup answer",
        "requested_model": "selected-model",
        "model": "backup-model",
        "requested_endpoint_id": None,
        "requested_endpoint_label": "Selected route",
        "endpoint_id": "backup",
        "endpoint_label": "backup",
    }
    assert saved[-1].metadata == {
        "model": "backup-model",
        "requested_model": "selected-model",
        "endpoint_id": "backup",
        "endpoint_label": "backup",
        "requested_endpoint_id": None,
        "requested_endpoint_label": "Selected route",
        "context_length": 128000,
        "context_trimmed": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary_context", "backup_context", "expected_counts"),
    [
        (100, 1000, (1, 3)),
        (1000, 100, (3, 1)),
    ],
)
async def test_nonstream_chat_shapes_each_candidate_from_route_neutral_history(
    monkeypatch,
    primary_context,
    backup_context,
    expected_counts,
):
    calls = []
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner: {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "backup", "model": "backup-model"},
        ],
    })
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: [("https://backup.example/v1", "backup-model", {})],
    )
    async def fake_compact(
        session, url, model, messages, headers=None, owner=None, **kwargs
    ):
        return (
            list(messages),
            backup_context if model == "backup-model" else primary_context,
            False,
        )

    monkeypatch.setattr(chat_routes, "maybe_compact", fake_compact)
    monkeypatch.setattr(
        chat_routes,
        "trim_for_context",
        lambda messages, budget: list(messages) if budget >= 1000 else list(messages[-1:]),
    )

    async def fake_call(url, model, messages, **kwargs):
        calls.append((model, list(messages)))
        if model == "selected-model":
            raise HTTPException(503, "selected unavailable")
        return "backup answer"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    endpoint, _saved = _chat_endpoint(monkeypatch)

    response = await endpoint(
        _RouteRequest("chat"),
        ChatRequest(message="hello", session="session-1"),
    )

    assert tuple(len(messages) for _model, messages in calls) == expected_counts
    assert response["model"] == "backup-model"


@pytest.mark.asyncio
async def test_nonstream_same_model_fallback_persists_endpoint_identity(monkeypatch):
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner: {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "account-two", "model": "selected-model"},
        ],
    })
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: [(
            "https://selected.example/v1",
            "selected-model",
            {"Authorization": "Bearer account-two"},
        )],
    )

    async def fake_call(url, model, messages, **kwargs):
        if kwargs.get("headers", {}).get("Authorization") == "Bearer selected":
            raise HTTPException(429, "rate limited")
        return "second account answer"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    endpoint, saved = _chat_endpoint(monkeypatch)

    response = await endpoint(
        _RouteRequest("chat"),
        ChatRequest(message="hello", session="session-1"),
    )

    assert response["requested_model"] == response["model"] == "selected-model"
    assert response["endpoint_id"] == "account-two"
    assert saved[-1].metadata["endpoint_id"] == "account-two"


@pytest.mark.asyncio
async def test_nonstream_chat_does_not_fallback_on_ineligible_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner: {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "backup", "model": "backup-model"},
        ],
    })
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda entries, owner=None, require_exact_model=False: [
            ("https://backup.example/v1", "backup-model", {}),
        ],
    )

    async def fake_call(url, model, messages, **kwargs):
        calls.append(model)
        raise HTTPException(401, "invalid key")

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    endpoint, _saved = _chat_endpoint(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await endpoint(
            _RouteRequest("chat"),
            ChatRequest(message="hello", session="session-1"),
        )

    assert exc.value.status_code == 401
    assert calls == ["selected-model"]


@pytest.mark.asyncio
async def test_nonstream_chat_does_not_fallback_on_endpoint_configuration_error(monkeypatch):
    calls = []
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner: {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "backup", "model": "backup-model"},
        ],
    })
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: [("https://backup.example/v1", "backup-model", {})],
    )

    async def fake_post(client, url, headers, **kwargs):
        calls.append(url)
        raise httpx.UnsupportedProtocol("unsupported protocol")

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: None)
    endpoint, saved = _chat_endpoint(monkeypatch, endpoint_url="ftp://selected.example")

    with pytest.raises(HTTPException) as exc:
        await endpoint(
            _RouteRequest("chat"),
            ChatRequest(message="hello", session="session-1"),
        )

    assert exc.value.status_code == 502
    assert len(calls) == 1
    assert saved == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary_body", "expected_status"),
    [
        ({"error": {"type": "invalid_request_error", "message": "unsupported model"}}, 400),
        ({"unexpected": "successful but malformed provider body"}, 502),
    ],
)
async def test_nonstream_chat_real_parser_never_falls_back_on_provider_or_schema_error(
    monkeypatch,
    primary_body,
    expected_status,
):
    calls = []
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner: {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "backup", "model": "backup-model"},
        ],
    })
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda entries, owner=None, require_exact_model=False: [
            ("https://backup.example/v1", "backup-model", {}),
        ],
    )

    class _Response:
        is_success = True
        status_code = 200
        text = ""

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    async def fake_post(_client, target_url, _headers, **kwargs):
        calls.append(target_url)
        if "selected.example" in target_url:
            return _Response(primary_body)
        return _Response({"choices": [{"message": {"content": "backup answer"}}]})

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: object())
    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: None)
    monkeypatch.setattr(llm_core, "_set_cached_response", lambda *args, **kwargs: None)
    endpoint, saved = _chat_endpoint(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await endpoint(
            _RouteRequest("chat"),
            ChatRequest(message="hello", session="session-1"),
        )

    assert exc.value.status_code == expected_status
    assert len(calls) == 1
    assert "selected.example" in calls[0]
    assert saved == []


@pytest.mark.asyncio
@pytest.mark.parametrize("use_fallback", [False, True])
async def test_nonstream_chat_real_parser_persists_provider_model_alias(
    monkeypatch,
    use_fallback,
):
    calls = []
    if use_fallback:
        monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": [
                {"endpoint_id": "backup", "model": "backup-model"},
            ],
        })
        monkeypatch.setattr(
            foreground_model_routing,
            "resolve_fallback_entries",
            lambda *args, **kwargs: [
                ("https://backup.example/v1", "backup-model", {}),
            ],
        )
    else:
        monkeypatch.setattr(
            foreground_model_routing,
            "_load_policy_preferences",
            lambda owner: {},
        )

    class _Response:
        is_success = True
        status_code = 200
        text = ""

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    async def fake_post(_client, target_url, _headers, **kwargs):
        calls.append(target_url)
        if use_fallback and "selected.example" in target_url:
            return _Response({
                "error": {
                    "status": 503,
                    "message": "selected unavailable",
                },
            })
        return _Response({
            "model": "provider-backup-alias" if use_fallback else "provider-selected-alias",
            "choices": [{"message": {"content": "provider answer"}}],
        })

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: object())
    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: None)
    monkeypatch.setattr(llm_core, "_set_cached_response", lambda *args, **kwargs: None)
    endpoint, saved = _chat_endpoint(monkeypatch)

    response = await endpoint(
        _RouteRequest("chat"),
        ChatRequest(message="hello", session="session-1"),
    )

    expected_model = (
        "provider-backup-alias" if use_fallback else "provider-selected-alias"
    )
    assert response["response"] == "provider answer"
    assert response["model"] == expected_model
    assert saved[-1].metadata["model"] == expected_model
    assert response["endpoint_id"] == ("backup" if use_fallback else None)
    assert len(calls) == (2 if use_fallback else 1)


@pytest.mark.asyncio
async def test_nonstream_chat_does_not_treat_empty_response_as_unavailability(monkeypatch):
    calls = []
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner: {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "backup", "model": "backup-model"},
        ],
    })
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda entries, owner=None, require_exact_model=False: [
            ("https://backup.example/v1", "backup-model", {}),
        ],
    )

    async def fake_call(url, model, messages, **kwargs):
        calls.append(model)
        return ""

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    endpoint, _saved = _chat_endpoint(monkeypatch)

    response = await endpoint(
        _RouteRequest("chat"),
        ChatRequest(message="hello", session="session-1"),
    )

    assert calls == ["selected-model"]
    assert response["model"] == "selected-model"


@pytest.mark.asyncio
async def test_nonstream_named_owner_does_not_inherit_flat_opt_in(monkeypatch):
    calls = []
    monkeypatch.setattr(prefs_routes, "_load", lambda: {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "shared", "model": "shared-model"},
        ],
    })
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: pytest.fail("flat opt-in resolved a candidate for bob"),
    )

    async def fake_call(url, model, messages, **kwargs):
        calls.append(model)
        raise HTTPException(503, "selected unavailable")

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    endpoint, _saved = _chat_endpoint(monkeypatch, owner="bob")

    with pytest.raises(HTTPException) as exc:
        await endpoint(
            _RouteRequest("chat"),
            ChatRequest(message="hello", session="session-1"),
        )

    assert exc.value.status_code == 503
    assert calls == ["selected-model"]


def test_candidate_builder_appends_only_policy_authorized_fallbacks():
    """Chat and Agent share the same candidate-building policy boundary."""

    authorized = [("https://opt-in.example/v1", "opt-in-model", {})]
    policy = ForegroundModelPolicy(enabled=True, fallback_candidates=tuple(authorized))

    assert build_foreground_model_candidates(
        "https://selected.example/v1",
        "selected-model",
        {"Authorization": "Bearer selected"},
        owner="alice",
        policy=policy,
    ) == [
        ("https://selected.example/v1", "selected-model", {"Authorization": "Bearer selected"}),
        *authorized,
    ]


def test_selected_endpoint_id_wins_over_route_equality_scan(monkeypatch):
    selected = {
        "endpoint_id": "account-two",
        "endpoint_label": "Account two",
        "endpoint_cost_tracked": True,
    }
    seen = []

    def exact_id_descriptor(endpoint_id, url, model, headers, owner=None):
        seen.append((endpoint_id, url, model, headers, owner))
        return selected

    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_route_descriptor_by_id",
        exact_id_descriptor,
        raising=False,
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_route_descriptor",
        lambda *args, **kwargs: {
            "endpoint_id": "account-one",
            "endpoint_label": "Account one",
            "endpoint_cost_tracked": True,
        },
    )

    descriptors = foreground_model_routing.build_foreground_route_descriptors(
        "https://provider.example/v1/chat/completions",
        "same-model",
        {"Authorization": "Bearer shared-key"},
        owner="alice",
        policy=ForegroundModelPolicy(),
        selected_endpoint_id="account-two",
    )

    assert descriptors == [selected]
    assert seen == [(
        "account-two",
        "https://provider.example/v1/chat/completions",
        "same-model",
        {"Authorization": "Bearer shared-key"},
        "alice",
    )]


@pytest.mark.asyncio
async def test_chat_stream_threads_form_endpoint_id_to_descriptor_builder(monkeypatch):
    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "chat", captured)
    seen = []

    def fake_descriptors(*args, selected_endpoint_id=None, **kwargs):
        seen.append(selected_endpoint_id)
        return [{
            "endpoint_id": selected_endpoint_id,
            "endpoint_label": "Selected endpoint",
            "endpoint_cost_tracked": True,
        }]

    monkeypatch.setattr(
        chat_routes,
        "build_foreground_route_descriptors",
        fake_descriptors,
    )
    request = _RouteRequest("chat")
    request._form["selected_endpoint_id"] = "account-two"

    response = await endpoint(request)
    async for _chunk in response.body_iterator:
        pass

    assert seen == ["account-two"]


@pytest.mark.asyncio
async def test_nonstream_chat_threads_request_endpoint_id_to_descriptor_builder(
    monkeypatch,
):
    seen = []

    def fake_descriptors(*args, selected_endpoint_id=None, **kwargs):
        seen.append(selected_endpoint_id)
        return [{
            "endpoint_id": selected_endpoint_id,
            "endpoint_label": "Selected endpoint",
            "endpoint_cost_tracked": True,
        }]

    async def fake_call(url, model, messages, **kwargs):
        return "selected answer"

    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner: {},
    )
    monkeypatch.setattr(
        chat_routes,
        "build_foreground_route_descriptors",
        fake_descriptors,
    )
    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    endpoint, _saved = _chat_endpoint(monkeypatch)

    await endpoint(
        _RouteRequest("chat"),
        ChatRequest(
            message="hello",
            session="session-1",
            selected_endpoint_id="account-two",
        ),
    )

    assert seen == ["account-two"]


def test_strict_policy_builds_only_the_selected_chat_candidate():
    candidates = build_foreground_model_candidates(
        "https://selected.example/v1",
        "selected-model",
        {"Authorization": "Bearer selected"},
        owner="alice",
        policy=ForegroundModelPolicy(),
    )

    assert candidates == [
        ("https://selected.example/v1", "selected-model", {"Authorization": "Bearer selected"})
    ]


def test_legacy_chat_resolver_is_disconnected():
    assert not hasattr(endpoint_resolver, "resolve_chat_fallback_candidates")


def test_retired_silent_endpoint_switcher_is_not_callable():
    assert not hasattr(chat_helpers, "try_fallback_endpoint")


@pytest.mark.parametrize(
    "prefs",
    [
        {},
        {"foreground_fallback_enabled": False, "foreground_model_fallbacks": [{"endpoint_id": "b", "model": "m"}]},
        {"foreground_fallback_enabled": "true", "foreground_model_fallbacks": [{"endpoint_id": "b", "model": "m"}]},
        {"foreground_fallback_enabled": True, "foreground_model_fallbacks": []},
        {"default_model_fallbacks": [{"endpoint_id": "legacy", "model": "legacy"}]},
    ],
)
def test_foreground_policy_fails_closed_without_explicit_complete_opt_in(monkeypatch, prefs):
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner=None: prefs)
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda entries, owner=None: pytest.fail("disabled policy resolved fallback entries"),
    )

    assert resolve_foreground_model_policy("alice") == ForegroundModelPolicy()


def test_foreground_policy_resolves_ordered_owner_scoped_entries(monkeypatch):
    entries = [
        {"endpoint_id": f"ep-{i}", "model": f"model-{i}"}
        for i in range(MAX_FOREGROUND_FALLBACKS + 2)
    ]
    seen = {}
    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner=None: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": entries,
        },
    )

    def fake_resolve(resolved_entries, owner=None, *, require_exact_model=False):
        seen["entries"] = resolved_entries
        seen["owner"] = owner
        seen["require_exact_model"] = require_exact_model
        return [("https://backup.example/v1", "backup", {"Authorization": "secret"})]

    monkeypatch.setattr(foreground_model_routing, "resolve_fallback_entries", fake_resolve)

    policy = resolve_foreground_model_policy("alice")

    assert policy.enabled is True
    assert policy.fallback_candidates == (
        ("https://backup.example/v1", "backup", {"Authorization": "secret"}),
    )
    assert policy.eligible_statuses == FOREGROUND_AVAILABILITY_STATUSES
    assert policy.fallback_on_empty is False
    assert seen == {
        "entries": entries[:MAX_FOREGROUND_FALLBACKS],
        "owner": "alice",
        "require_exact_model": True,
    }


def test_foreground_policy_filters_allowed_models_before_maximum_slice(monkeypatch):
    disallowed = [
        {"endpoint_id": f"blocked-{i}", "model": f"blocked-model-{i}"}
        for i in range(MAX_FOREGROUND_FALLBACKS)
    ]
    allowed_entry = {"endpoint_id": "allowed", "model": "allowed-model"}
    seen = {}
    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner=None: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": [*disallowed, allowed_entry],
        },
    )

    def fake_resolve(entries, owner=None, *, require_exact_model=False):
        seen["entries"] = entries
        return [("https://allowed.example/v1", "allowed-model", {})]

    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        fake_resolve,
    )

    policy = resolve_foreground_model_policy(
        "alice",
        allowed_models={"allowed-model"},
    )

    assert policy.enabled is True
    assert policy.fallback_candidates == (
        ("https://allowed.example/v1", "allowed-model", {}),
    )
    assert seen["entries"] == [allowed_entry]


def test_compatibility_resolver_keeps_descriptor_aligned_when_an_entry_is_skipped(
    monkeypatch,
):
    entries = [
        {"endpoint_id": "missing", "model": "missing-model"},
        {"endpoint_id": "backup", "model": "backup-model"},
    ]
    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner=None: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": entries,
        },
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: [
            ("https://backup.example/v1", "backup-model", {})
        ],
    )

    policy = resolve_foreground_model_policy("alice")

    assert policy.fallback_candidates == (
        ("https://backup.example/v1", "backup-model", {}),
    )
    assert policy.fallback_descriptors[0]["endpoint_id"] == "backup"


def test_foreground_policy_loads_only_the_requested_users_preferences(monkeypatch):
    by_owner = {
        "alice": {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": [{"endpoint_id": "alice-backup", "model": "alice-model"}],
        },
        "bob": {
            "foreground_fallback_enabled": False,
            "foreground_model_fallbacks": [{"endpoint_id": "bob-backup", "model": "bob-model"}],
        },
    }
    seen = []
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner=None: by_owner[owner])

    def fake_resolve(entries, owner=None, *, require_exact_model=False):
        seen.append((entries, owner))
        assert require_exact_model is True
        return [(f"https://{owner}.example/v1", f"{owner}-model", {})]

    monkeypatch.setattr(foreground_model_routing, "resolve_fallback_entries", fake_resolve)

    assert resolve_foreground_model_policy("alice").enabled is True
    assert resolve_foreground_model_policy("bob") == ForegroundModelPolicy()
    assert seen == [([{"endpoint_id": "alice-backup", "model": "alice-model"}], "alice")]


def test_named_owner_does_not_inherit_flat_single_user_fallback_consent(monkeypatch):
    monkeypatch.setattr(prefs_routes, "_load", lambda: {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "shared-backup", "model": "shared-model"},
        ],
    })
    monkeypatch.setattr(
        prefs_routes,
        "_load_for_user",
        lambda owner=None: pytest.fail("named foreground policy used flat compatibility loader"),
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: pytest.fail("flat consent resolved candidate endpoints for bob"),
    )

    assert resolve_foreground_model_policy("bob") == ForegroundModelPolicy()


def test_named_owner_unrelated_save_does_not_import_flat_fallback_consent(
    monkeypatch,
    tmp_path,
):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "single-user", "model": "single-model"},
        ],
        "default_model_fallbacks": [
            {"endpoint_id": "legacy", "model": "legacy-model"},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: pytest.fail("Bob inherited flat fallback consent"),
    )

    assert resolve_foreground_model_policy("bob") == ForegroundModelPolicy()
    bob = prefs_routes._load_for_user("bob")
    bob["theme"] = "dark"
    prefs_routes._save_for_user("bob", bob)

    assert resolve_foreground_model_policy("bob") == ForegroundModelPolicy()
    raw = prefs_routes._load()
    assert raw["_users"]["bob"] == {"theme": "dark"}
    assert raw["default_model_fallbacks"][0]["endpoint_id"] == "legacy"


def test_startup_pref_migration_does_not_transfer_flat_fallback_consent(
    monkeypatch,
    tmp_path,
):
    database_file = tmp_path / "app.db"
    database_file.touch()
    (tmp_path / "auth.json").write_text(json.dumps({
        "users": {
            "alice": {"is_admin": True},
        },
    }), encoding="utf-8")
    prefs_file = tmp_path / "user_prefs.json"
    flat_fallbacks = [
        {"endpoint_id": "single-user", "model": "single-model"},
    ]
    legacy_fallbacks = [
        {"endpoint_id": "legacy", "model": "legacy-model"},
    ]
    prefs_file.write_text(json.dumps({
        "theme": "dark",
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": flat_fallbacks,
        "default_model_fallbacks": legacy_fallbacks,
    }), encoding="utf-8")
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{database_file}")
    monkeypatch.setattr(database, "AUTH_FILE", str(tmp_path / "auth.json"))
    monkeypatch.setattr(database, "MEMORY_FILE", str(tmp_path / "memory.json"))
    monkeypatch.setattr(database, "USER_PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: pytest.fail(
            "startup migration transferred flat fallback consent"
        ),
    )

    database._migrate_assign_legacy_owner()

    raw = json.loads(prefs_file.read_text(encoding="utf-8"))
    assert raw["foreground_fallback_enabled"] is True
    assert raw["foreground_model_fallbacks"] == flat_fallbacks
    assert raw["_users"]["alice"] == {
        "theme": "dark",
        "default_model_fallbacks": legacy_fallbacks,
    }
    assert resolve_foreground_model_policy("alice") == ForegroundModelPolicy()


def test_auth_disabled_write_in_multiuser_store_does_not_grant_named_consent(
    monkeypatch,
    tmp_path,
):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({
        "_users": {
            "alice": {"theme": "dark"},
            "bob": {"theme": "light"},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: pytest.fail(
            "auth-disabled consent was written into a named owner"
        ),
    )

    ownerless = prefs_routes._load_for_user(None)
    ownerless["foreground_fallback_enabled"] = True
    ownerless["foreground_model_fallbacks"] = [
        {"endpoint_id": "single-user", "model": "single-model"},
    ]
    prefs_routes._save_for_user(None, ownerless)

    raw = prefs_routes._load()
    assert raw["foreground_fallback_enabled"] is True
    assert raw["foreground_model_fallbacks"][0]["endpoint_id"] == "single-user"
    assert raw["_users"]["alice"] == {"theme": "dark"}
    assert raw["_users"]["bob"] == {"theme": "light"}
    assert resolve_foreground_model_policy("alice") == ForegroundModelPolicy()
    assert resolve_foreground_model_policy("bob") == ForegroundModelPolicy()


def test_named_owner_resolves_only_an_actual_scoped_preferences_dict(monkeypatch):
    entry = {"endpoint_id": "alice-backup", "model": "alice-model"}
    monkeypatch.setattr(prefs_routes, "_load", lambda: {
        "_users": {
            "alice": {
                "foreground_fallback_enabled": True,
                "foreground_model_fallbacks": [entry],
            },
            "bob": ["not", "a", "preferences", "dict"],
        },
    })
    seen = []
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda entries, owner=None, require_exact_model=False: (
            seen.append((entries, owner, require_exact_model))
            or [("https://alice.example/v1", "alice-model", {})]
        ),
    )

    assert resolve_foreground_model_policy("alice").enabled is True
    assert resolve_foreground_model_policy("bob") == ForegroundModelPolicy()
    assert seen == [([entry], "alice", True)]


def test_auth_disabled_owner_none_preserves_flat_single_user_policy(monkeypatch):
    prefs = {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "single-user-backup", "model": "backup-model"},
        ],
    }
    seen = []
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda owner=None: prefs)
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda entries, owner=None, require_exact_model=False: (
            seen.append((entries, owner, require_exact_model))
            or [("https://backup.example/v1", "backup-model", {})]
        ),
    )

    policy = resolve_foreground_model_policy(None)

    assert policy.enabled is True
    assert policy.fallback_candidates == (("https://backup.example/v1", "backup-model", {}),)
    assert seen == [(
        [{"endpoint_id": "single-user-backup", "model": "backup-model"}],
        None,
        True,
    )]


def test_foreground_policy_filters_models_outside_the_callers_allowlist(monkeypatch):
    seen = []
    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner=None: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": [
                {"endpoint_id": "allowed", "model": "allowed-model"},
                {"endpoint_id": "blocked", "model": "blocked-model"},
            ],
        },
    )

    def fake_resolve(entries, owner=None, *, require_exact_model=False):
        seen.extend(entries)
        return [("https://allowed.example/v1", "allowed-model", {})]

    monkeypatch.setattr(foreground_model_routing, "resolve_fallback_entries", fake_resolve)

    policy = resolve_foreground_model_policy("alice", allowed_models={"allowed-model"})

    assert policy.enabled is True
    assert seen == [{"endpoint_id": "allowed", "model": "allowed-model"}]


def test_foreground_policy_is_strict_when_all_fallbacks_are_disallowed(monkeypatch):
    monkeypatch.setattr(
        foreground_model_routing,
        "_load_policy_preferences",
        lambda owner=None: {
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": [
                {"endpoint_id": "blocked", "model": "blocked-model"},
            ],
        },
    )
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_fallback_entries",
        lambda *args, **kwargs: pytest.fail("disallowed entry reached credential resolution"),
    )

    assert resolve_foreground_model_policy(
        "alice",
        allowed_models={"selected-model"},
    ) == ForegroundModelPolicy()


def test_utility_resolver_does_not_inherit_legacy_chat_fallbacks(monkeypatch):
    seen_keys = []

    def fake_resolve(setting_key, owner=None):
        seen_keys.append((setting_key, owner))
        return [("https://utility.example/v1", "utility-model", {})]

    monkeypatch.setattr(endpoint_resolver, "_resolve_fallback_candidates", fake_resolve)

    assert endpoint_resolver.resolve_utility_fallback_candidates(owner="alice") == [
        ("https://utility.example/v1", "utility-model", {})
    ]
    assert seen_keys == [("utility_model_fallbacks", "alice")]


def test_multi_round_agent_uses_only_selected_model(monkeypatch):
    """Every Agent round receives only the selected foreground candidate."""

    seen_candidates = []
    round_number = 0

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    async def fake_stream(candidates, messages, **kwargs):
        nonlocal round_number
        round_number += 1
        seen_candidates.append([(url, model) for url, model, _headers in candidates])
        if round_number == 1:
            call = {"name": "bash", "arguments": json.dumps({"command": "printf ok"})}
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        else:
            yield f'data: {json.dumps({"delta": "done"})}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(block, *args, **kwargs):
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    chunks = _collect(
        agent_loop.stream_agent_loop(
            "https://selected.example/v1",
            "selected-model",
            [{"role": "user", "content": "Run one tool and report back."}],
            max_rounds=3,
            relevant_tools={"bash"},
            fallbacks=[],
            _is_teacher_run=True,
        )
    )

    assert seen_candidates == [
        [("https://selected.example/v1", "selected-model")],
        [("https://selected.example/v1", "selected-model")],
    ]
    assert any('"delta": "done"' in chunk for chunk in chunks)


def test_multi_round_agent_pins_answering_fallback_for_the_run(monkeypatch):
    """A tool round must not silently switch back to the selected model."""

    seen_candidates = []
    round_number = 0
    primary = ("https://selected.example/v1", "selected-model", {})
    backup = ("https://backup.example/v1", "backup-model", {"Authorization": "backup"})
    route_modes = []

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(
        agent_loop,
        "_agent_route_tool_mode",
        lambda url, model, owner=None, headers=None: route_modes.append((url, model, owner, headers)) or (model == "selected-model", False, False),
    )

    async def fake_stream(candidates, messages, **kwargs):
        nonlocal round_number
        round_number += 1
        seen_candidates.append(candidates)
        assert kwargs["fallback_statuses"] == FOREGROUND_AVAILABILITY_STATUSES
        assert kwargs["fallback_on_empty"] is False
        if round_number == 1:
            yield f'data: {json.dumps({"type": "fallback", "selected_model": "selected-model", "answered_by": "backup-model", "candidate_index": 1})}\n\n'
            call = {"name": "bash", "arguments": json.dumps({"command": "printf ok"})}
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        else:
            yield f'data: {json.dumps({"delta": "done"})}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(block, *args, **kwargs):
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    chunks = _collect(
        agent_loop.stream_agent_loop(
            primary[0],
            primary[1],
            [{"role": "user", "content": "Run one tool and report back."}],
            max_rounds=3,
            relevant_tools={"bash"},
            headers={},
            fallbacks=[backup],
            fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
            fallback_on_empty=False,
            _is_teacher_run=True,
        )
    )

    assert seen_candidates == [[primary, backup], [backup]]
    fallback_event = next(chunk for chunk in chunks if '"type": "fallback"' in chunk)
    fallback_data = json.loads(fallback_event[6:])
    assert fallback_data["pinned_for_run"] is True
    assert fallback_data["round"] == 1
    metrics_event = next(chunk for chunk in chunks if '"type": "metrics"' in chunk)
    metrics = json.loads(metrics_event[6:])["data"]
    assert metrics["requested_model"] == "selected-model"
    assert metrics["model"] == "backup-model"
    assert metrics["round_models"] == ["backup-model", "backup-model"]
    assert metrics["tool_events"][0]["model"] == "backup-model"
    assert route_modes == [
        ("https://selected.example/v1", "selected-model", None, {}),
        ("https://backup.example/v1", "backup-model", None, {"Authorization": "backup"}),
    ]


def test_late_agent_fallback_records_each_round_and_stays_pinned(monkeypatch):
    seen_candidates = []
    round_number = 0
    primary = ("https://selected.example/v1", "selected-model", {})
    backup = ("https://backup.example/v1", "backup-model", {})

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "_agent_route_tool_mode", lambda url, model, owner=None, headers=None: (True, False, False))

    async def fake_stream(candidates, messages, **kwargs):
        nonlocal round_number
        round_number += 1
        seen_candidates.append(candidates)
        if round_number == 1:
            yield 'data: {"delta": "primary round"}\n\n'
            call = {"name": "bash", "arguments": json.dumps({"command": "printf one"})}
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        elif round_number == 2:
            yield f'data: {json.dumps({"type": "fallback", "selected_model": "selected-model", "answered_by": "backup-model", "candidate_index": 1})}\n\n'
            yield f'data: {json.dumps({"type": "model_actual", "requested_model": "backup-model", "model": "provider-backup-alias"})}\n\n'
            yield 'data: {"delta": "backup round"}\n\n'
            call = {"name": "bash", "arguments": json.dumps({"command": "printf two"})}
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        else:
            yield 'data: {"delta": "backup final"}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(block, *args, **kwargs):
        # Keep this routing-only test untainted with a content-free fixture.
        # Any model-visible shell error is workspace-derived and correctly
        # reaches the exact-approval boundary on the next action.
        return "bash", {"exit_code": 1}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    chunks = _collect(
        agent_loop.stream_agent_loop(
            primary[0],
            primary[1],
            [{"role": "user", "content": "Run two tools and report back."}],
            headers=primary[2],
            max_rounds=4,
            relevant_tools={"bash"},
            fallbacks=[backup],
            fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
            fallback_on_empty=False,
            _is_teacher_run=True,
        )
    )

    assert seen_candidates == [[primary, backup], [primary, backup], [backup]]
    fallback_data = json.loads(next(chunk for chunk in chunks if '"type": "fallback"' in chunk)[6:])
    assert fallback_data["round"] == 2
    model_actual = json.loads(next(chunk for chunk in chunks if '"type": "model_actual"' in chunk)[6:])
    assert model_actual["round"] == 2
    assert model_actual["requested_model"] == "selected-model"
    assert model_actual["model"] == "provider-backup-alias"
    metrics = json.loads(next(chunk for chunk in chunks if '"type": "metrics"' in chunk)[6:])["data"]
    assert metrics["round_models"] == ["selected-model", "provider-backup-alias", "backup-model"]
    assert [event["model"] for event in metrics["tool_events"]] == [
        "selected-model",
        "provider-backup-alias",
    ]


@pytest.mark.parametrize("status", [400, 401, 404])
def test_agent_terminal_first_round_error_has_no_success_completion(monkeypatch, status):
    calls = 0
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)

    async def fake_stream(candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        yield f'event: error\ndata: {json.dumps({"status": status, "error": "provider rejected request"})}\n\n'

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    chunks = _collect(
        agent_loop.stream_agent_loop(
            "https://selected.example/v1",
            "selected-model",
            [{"role": "user", "content": "hello"}],
            max_rounds=3,
            relevant_tools=set(),
            fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
            fallback_on_empty=False,
            _is_teacher_run=True,
        )
    )

    assert calls == 1
    assert any(chunk.startswith("event: error") for chunk in chunks)
    assert not any('"type": "metrics"' in chunk for chunk in chunks), chunks
    assert "data: [DONE]\n\n" not in chunks
    assert not any("empty response" in chunk.lower() for chunk in chunks)


@pytest.mark.parametrize(
    ("provider_status", "expected_status", "expected_message"),
    [
        (400, 400, "Model request failed (HTTP 400)"),
        (429.9, None, "Model request failed"),
    ],
)
def test_agent_terminal_later_round_error_stops_after_completed_tool(
    monkeypatch,
    provider_status,
    expected_status,
    expected_message,
):
    calls = 0
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(
        agent_loop,
        "_agent_route_tool_mode",
        lambda *args, **kwargs: (True, False, False),
    )

    async def fake_stream(candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            tool_call = {
                "name": "bash",
                "arguments": json.dumps({"command": "printf one"}),
            }
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [tool_call]})}\n\n'
            yield "data: [DONE]\n\n"
            return
        yield 'data: {"delta": "partial second-round prose"}\n\n'
        yield f'event: error\ndata: {json.dumps({"status": provider_status, "error": "unsupported model"})}\n\n'

    async def fake_execute(block, *args, **kwargs):
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    chunks = _collect(
        agent_loop.stream_agent_loop(
            "https://selected.example/v1",
            "selected-model",
            [{"role": "user", "content": "Run one tool."}],
            max_rounds=3,
            relevant_tools={"bash"},
            fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
            fallback_on_empty=False,
            _is_teacher_run=True,
        )
    )

    assert calls == 2
    assert sum('"type": "agent_step"' in chunk for chunk in chunks) == 1
    terminal = json.loads(next(
        chunk for chunk in chunks if '"type": "agent_terminal"' in chunk
    )[6:])["data"]
    assert terminal["failed"] is True
    assert terminal["failure"]["status"] == expected_status
    assert terminal["tool_events"][0]["output"] == "ok"
    assert terminal["failure"] == {
        "status": expected_status,
        "message": expected_message,
    }
    assert terminal["round_models"] == ["selected-model", "selected-model"]
    assert terminal["round_texts"][-1] == (
        "partial second-round prose\n\n"
        f"[Agent stopped: {expected_message}]"
    )
    assert any(chunk.startswith("event: error") for chunk in chunks)
    assert not any('"type": "metrics"' in chunk for chunk in chunks)
    assert "data: [DONE]\n\n" not in chunks
    assert not any("empty response" in chunk.lower() for chunk in chunks)


@pytest.mark.parametrize(
    ("provider_status", "expected_status", "expected_message"),
    [
        (503, 503, "Model request failed (HTTP 503)"),
        (429.9, None, "Model request failed"),
    ],
)
def test_direct_low_signal_partial_error_emits_terminal_history(
    monkeypatch,
    provider_status,
    expected_status,
    expected_message,
):
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(
        agent_loop,
        "_classify_agent_request",
        lambda messages, latest: {
            "low_signal": True,
            "continuation": False,
            "domains": [],
            "retrieval_query": latest,
        },
    )
    monkeypatch.setattr(agent_loop, "_is_casual_low_signal", lambda latest: True)

    async def fake_stream(candidates, messages, **kwargs):
        yield 'data: {"delta": "visible direct partial"}\n\n'
        yield f'event: error\ndata: {json.dumps({"status": provider_status, "error": "provider detail"})}\n\n'

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    chunks = _collect(agent_loop.stream_agent_loop(
        "https://selected.example/v1",
        "selected-model",
        [{"role": "user", "content": "hello"}],
        relevant_tools=set(),
        route_descriptors=[{
            "endpoint_id": "selected",
            "endpoint_label": "Selected",
            "endpoint_cost_tracked": True,
        }],
        fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
        fallback_on_empty=False,
        _is_teacher_run=True,
    ))

    terminal = json.loads(next(
        chunk for chunk in chunks if '"type": "agent_terminal"' in chunk
    )[6:])["data"]
    assert terminal["round_texts"] == [
        "visible direct partial\n\n"
        f"[Agent stopped: {expected_message}]"
    ]
    assert terminal["failure"] == {
        "status": expected_status,
        "message": expected_message,
    }
    assert terminal["endpoint_cost_tracked"] is True
    assert terminal["usage_buckets"][0]["endpoint_cost_tracked"] is True
    assert any(chunk.startswith("event: error") for chunk in chunks)
    assert "data: [DONE]\n\n" not in chunks


@pytest.mark.parametrize("terminal_error", [False, True])
def test_direct_low_signal_fallback_estimates_winning_route_prompt(
    monkeypatch,
    terminal_error,
):
    primary = ("https://selected.example/v1", "generic-model", {})
    backup = ("https://backup.example/v1", "odysseus-qwen-backup", {})
    candidate_requests = []

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(
        agent_loop,
        "_classify_agent_request",
        lambda messages, latest: {
            "low_signal": True,
            "continuation": False,
            "domains": [],
            "retrieval_query": latest,
        },
    )
    monkeypatch.setattr(agent_loop, "_is_casual_low_signal", lambda latest: True)
    monkeypatch.setattr(
        agent_loop,
        "_is_odysseus_qwen_model",
        lambda candidate_model: candidate_model == backup[1],
    )
    monkeypatch.setattr(
        agent_loop,
        "_minimal_odysseus_general_messages",
        lambda messages, include_memory=True: [
            {"role": "system", "content": "larger backup route prompt"},
            *list(messages),
        ],
    )
    monkeypatch.setattr(
        agent_loop,
        "estimate_tokens",
        lambda request_messages: (
            99
            if any(
                message.get("content") == "larger backup route prompt"
                for message in request_messages
            )
            else 7
        ),
    )

    async def fake_stream(candidates, messages, **kwargs):
        assert messages == [{"role": "user", "content": "hello"}]
        request = kwargs["candidate_request_factory"](1, *backup)
        candidate_requests.append(request["messages"])
        fallback_event = {
            "type": "fallback",
            "selected_model": primary[1],
            "answered_by": backup[1],
            "candidate_index": 1,
            "selected_endpoint_id": "selected",
            "selected_endpoint_label": "Selected",
            "selected_endpoint_cost_tracked": False,
            "answered_by_endpoint_id": "backup",
            "answered_by_endpoint_label": "Backup",
            "answered_by_endpoint_cost_tracked": True,
        }
        yield "data: " + json.dumps(fallback_event) + "\n\n"
        yield 'data: {"type": "usage", "data": {"input_tokens": null, "output_tokens": 1}}\n\n'
        yield 'data: {"delta": "backup response"}\n\n'
        if terminal_error:
            yield 'event: error\ndata: {"status": 503, "error": "unavailable"}\n\n'
        else:
            yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    chunks = _collect(agent_loop.stream_agent_loop(
        primary[0],
        primary[1],
        [{"role": "user", "content": "hello"}],
        relevant_tools=set(),
        fallbacks=[backup],
        route_descriptors=[
            {
                "endpoint_id": "selected",
                "endpoint_label": "Selected",
                "endpoint_cost_tracked": False,
            },
            {
                "endpoint_id": "backup",
                "endpoint_label": "Backup",
                "endpoint_cost_tracked": True,
            },
        ],
        fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
        fallback_on_empty=False,
        _is_teacher_run=True,
    ))

    assert candidate_requests[0][0] == {
        "role": "system",
        "content": "larger backup route prompt",
    }
    event_type = "agent_terminal" if terminal_error else "metrics"
    accounted = json.loads(next(
        chunk for chunk in chunks if f'"type": "{event_type}"' in chunk
    )[6:])["data"]
    assert accounted["model"] == backup[1]
    assert accounted["endpoint_id"] == "backup"
    assert accounted["input_tokens"] == 99
    assert accounted["usage_buckets"] == [{
        "round": 1,
        "model": backup[1],
        "endpoint_id": "backup",
        "endpoint_label": "Backup",
        "input_tokens": 99,
        "output_tokens": len("backup response") // 4,
        "usage_source": "estimated",
        "endpoint_cost_tracked": True,
    }]


def test_direct_low_signal_configuration_error_surfaces_without_fake_success(monkeypatch):
    monkeypatch.setattr(
        agent_loop,
        "get_setting",
        lambda key, default=None: (
            "not-an-int" if key == "agent_stream_timeout_seconds" else default
        ),
    )
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(
        agent_loop,
        "_classify_agent_request",
        lambda messages, latest: {
            "low_signal": True,
            "continuation": False,
            "domains": [],
            "retrieval_query": latest,
        },
    )
    monkeypatch.setattr(agent_loop, "_is_casual_low_signal", lambda latest: True)

    chunks = _collect(agent_loop.stream_agent_loop(
        "https://selected.example/v1",
        "selected-model",
        [{"role": "user", "content": "hello"}],
        relevant_tools=set(),
        fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
        fallback_on_empty=False,
        _is_teacher_run=True,
    ))

    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")
    payload = json.loads(chunks[0].split("data: ", 1)[1])
    assert payload == {
        "error": "Model request failed",
        "status": 500,
        "fallback_eligible": False,
    }
    assert not any('"delta": "Hey."' in chunk for chunk in chunks)
    assert not any('"type": "metrics"' in chunk for chunk in chunks)
    assert "data: [DONE]\n\n" not in chunks


def test_direct_low_signal_empty_completion_surfaces_without_fake_success(monkeypatch):
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(
        agent_loop,
        "_classify_agent_request",
        lambda messages, latest: {
            "low_signal": True,
            "continuation": False,
            "domains": [],
            "retrieval_query": latest,
        },
    )
    monkeypatch.setattr(agent_loop, "_is_casual_low_signal", lambda latest: True)

    async def empty_stream(*args, **kwargs):
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", empty_stream)
    chunks = _collect(agent_loop.stream_agent_loop(
        "https://selected.example/v1",
        "selected-model",
        [{"role": "user", "content": "hello"}],
        relevant_tools=set(),
        fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
        fallback_on_empty=False,
        _is_teacher_run=True,
    ))

    assert len(chunks) == 1
    payload = json.loads(chunks[0].split("data: ", 1)[1])
    assert payload["error"] == "Model returned an empty response"
    assert payload["fallback_eligible"] is False
    assert not any('"delta": "Hey."' in chunk for chunk in chunks)
    assert not any('"type": "metrics"' in chunk for chunk in chunks)


def test_reasoning_only_agent_error_emits_terminal_history(monkeypatch):
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)

    async def fake_stream(candidates, messages, **kwargs):
        yield 'data: {"delta": "private reasoning partial", "thinking": true}\n\n'
        yield 'event: error\ndata: {"status": 504, "error": "provider detail"}\n\n'

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    chunks = _collect(agent_loop.stream_agent_loop(
        "https://selected.example/v1",
        "selected-model",
        [{"role": "user", "content": "Investigate this failure."}],
        relevant_tools={"bash"},
        fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
        fallback_on_empty=False,
        _is_teacher_run=True,
    ))

    terminal = json.loads(next(
        chunk for chunk in chunks if '"type": "agent_terminal"' in chunk
    )[6:])["data"]
    assert terminal["thinking"] == "private reasoning partial"
    assert terminal["round_texts"] == [
        "[Agent stopped: Model request failed (HTTP 504)]"
    ]
    assert any(chunk.startswith("event: error") for chunk in chunks)
    assert "data: [DONE]\n\n" not in chunks


def test_toolless_multi_round_agent_persists_round_route_provenance(monkeypatch):
    calls = 0
    primary = ("https://selected.example/v1", "selected-model", {})
    backup = ("https://backup.example/v1", "backup-model", {})
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)

    async def fake_stream(candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield 'data: {"delta": "Let me check that now"}\n\n'
        else:
            yield 'data: {"type": "fallback", "selected_model": "selected-model", "answered_by": "backup-model", "candidate_index": 1, "selected_endpoint_id": "selected-ep", "selected_endpoint_label": "Selected endpoint", "selected_endpoint_cost_tracked": false, "answered_by_endpoint_id": "backup-ep", "answered_by_endpoint_label": "Backup endpoint", "answered_by_endpoint_cost_tracked": true}\n\n'
            yield 'data: {"delta": "final answer"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    chunks = _collect(agent_loop.stream_agent_loop(
        primary[0],
        primary[1],
        [{"role": "user", "content": "Please investigate."}],
        headers=primary[2],
        max_rounds=3,
        relevant_tools=set(),
        fallbacks=[backup],
        route_descriptors=[
            {"endpoint_id": "selected-ep", "endpoint_label": "Selected endpoint", "endpoint_cost_tracked": False},
            {"endpoint_id": "backup-ep", "endpoint_label": "Backup endpoint", "endpoint_cost_tracked": True},
        ],
        fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
        fallback_on_empty=False,
        _is_teacher_run=True,
    ))

    metrics = json.loads(next(
        chunk for chunk in chunks if '"type": "metrics"' in chunk
    )[6:])["data"]
    assert metrics["round_texts"] == ["Let me check that now", "final answer"]
    assert metrics["round_models"] == ["selected-model", "backup-model"]
    assert metrics["round_endpoint_ids"] == ["selected-ep", "backup-ep"]
    assert metrics["endpoint_id"] == "backup-ep"
    assert metrics["requested_endpoint_id"] == "selected-ep"
    assert metrics["endpoint_cost_tracked"] is True
    assert "tool_events" not in metrics


def test_agent_metrics_attribute_usage_to_each_answering_route(monkeypatch):
    calls = 0
    primary = ("https://paid.example/v1", "selected-model", {})
    backup = ("http://localhost:11434/v1", "backup-model", {})
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(
        agent_loop,
        "_agent_route_tool_mode",
        lambda *args, **kwargs: (True, False, False),
    )

    async def fake_stream(candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield 'data: {"type": "model_actual", "model": "selected-alias"}\n\n'
            yield 'data: {"type": "usage", "data": {"model": "selected-alias", "input_tokens": 100, "output_tokens": 10}}\n\n'
            tool_call = {
                "name": "bash",
                "arguments": json.dumps({"command": "printf one"}),
            }
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [tool_call]})}\n\n'
        else:
            yield 'data: {"type": "fallback", "selected_model": "selected-model", "answered_by": "backup-model", "candidate_index": 1, "selected_endpoint_id": "paid", "selected_endpoint_label": "Paid", "selected_endpoint_cost_tracked": true, "answered_by_endpoint_id": "local", "answered_by_endpoint_label": "Local", "answered_by_endpoint_cost_tracked": false}\n\n'
            yield 'data: {"type": "model_actual", "model": "backup-alias"}\n\n'
            yield 'data: {"type": "usage", "data": {"model": "backup-alias", "input_tokens": 200, "output_tokens": 20}}\n\n'
            yield 'data: {"delta": "done"}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(block, *args, **kwargs):
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    chunks = _collect(agent_loop.stream_agent_loop(
        primary[0],
        primary[1],
        [{"role": "user", "content": "Run one tool."}],
        headers=primary[2],
        max_rounds=3,
        relevant_tools={"bash"},
        fallbacks=[backup],
        route_descriptors=[
            {"endpoint_id": "paid", "endpoint_label": "Paid", "endpoint_cost_tracked": True},
            {"endpoint_id": "local", "endpoint_label": "Local", "endpoint_cost_tracked": False},
        ],
        fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
        fallback_on_empty=False,
        _is_teacher_run=True,
    ))

    metrics = json.loads(next(
        chunk for chunk in chunks if '"type": "metrics"' in chunk
    )[6:])["data"]
    assert metrics["input_tokens"] == 300
    assert metrics["output_tokens"] == 30
    assert metrics["usage_source"] == "real"
    assert metrics["usage_buckets"] == [
        {
            "round": 1,
            "model": "selected-alias",
            "endpoint_id": "paid",
            "endpoint_label": "Paid",
            "input_tokens": 100,
            "output_tokens": 10,
            "usage_source": "real",
            "endpoint_cost_tracked": True,
        },
        {
            "round": 2,
            "model": "backup-alias",
            "endpoint_id": "local",
            "endpoint_label": "Local",
            "input_tokens": 200,
            "output_tokens": 20,
            "usage_source": "real",
            "endpoint_cost_tracked": False,
        },
    ]


@pytest.mark.parametrize("malformed_input", [None, "bad", 10**1000])
def test_agent_round_ignores_malformed_usage_and_uses_estimate(
    monkeypatch,
    malformed_input,
):
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(
        agent_loop,
        "_agent_route_tool_mode",
        lambda *args, **kwargs: (True, False, False),
    )

    async def fake_stream(candidates, messages, **kwargs):
        usage_event = {
            "type": "usage",
            "data": {
                "input_tokens": malformed_input,
                "output_tokens": 1,
            },
        }
        yield "data: " + json.dumps(usage_event) + "\n\n"
        yield 'data: {"delta": "valid answer"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    chunks = _collect(agent_loop.stream_agent_loop(
        "https://selected.example/v1",
        "selected-model",
        [{"role": "user", "content": "Run a detailed investigation."}],
        max_rounds=1,
        relevant_tools={"bash"},
        route_descriptors=[{
            "endpoint_id": "selected",
            "endpoint_label": "Selected",
            "endpoint_cost_tracked": True,
        }],
        fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
        fallback_on_empty=False,
        _is_teacher_run=True,
    ))

    metrics = json.loads(next(
        chunk for chunk in chunks if '"type": "metrics"' in chunk
    )[6:])["data"]
    assert metrics["usage_source"] == "estimated"
    assert metrics["input_tokens"] == 10
    assert metrics["usage_buckets"][0]["input_tokens"] == 10
    assert metrics["usage_buckets"][0]["output_tokens"] == len("valid answer") // 4


@pytest.mark.parametrize(
    ("synthesis_result", "expected_answer"),
    [
        ("Recovered final answer.", "Recovered final answer."),
        (
            "",
            "I gathered some search results but couldn't pull a clean answer together. "
            "Want me to try a more specific question, or summarize what I did find?",
        ),
    ],
)
def test_force_answer_recovery_persists_and_bills_pinned_fallback_route(
    monkeypatch,
    synthesis_result,
    expected_answer,
):
    primary = ("https://selected.example/v1", "selected-model", {})
    backup = (
        "https://backup.example/v1",
        "backup-model",
        {"Authorization": "Bearer backup"},
    )
    requests_by_round = []
    synthesis_calls = []

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(
        agent_loop,
        "_agent_route_tool_mode",
        lambda *args, **kwargs: (True, False, False),
    )

    async def fake_compact(
        session, url, model, messages, headers=None, owner=None,
        *, persist=True, compaction_state=None,
    ):
        return (list(messages), 4096, False)

    async def fake_stream(candidates, messages, **kwargs):
        round_index = len(requests_by_round)
        requests_by_round.append([(url, model) for url, model, _ in candidates])
        factory = kwargs["candidate_request_factory"]
        for index, candidate in enumerate(candidates):
            await factory(index, *candidate)
        if round_index == 0:
            fallback_event = {
                "type": "fallback",
                "selected_model": primary[1],
                "answered_by": backup[1],
                "candidate_index": 1,
                "selected_endpoint_id": "selected-ep",
                "selected_endpoint_label": "Selected",
                "selected_endpoint_cost_tracked": False,
                "answered_by_endpoint_id": "backup-ep",
                "answered_by_endpoint_label": "Backup",
                "answered_by_endpoint_cost_tracked": True,
            }
            yield "data: " + json.dumps(fallback_event) + "\n\n"
        tool_call = {
            "name": "bash",
            "arguments": json.dumps({"command": "printf repeated"}),
        }
        yield f'data: {json.dumps({"type": "tool_calls", "calls": [tool_call]})}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(block, *args, **kwargs):
        # The repeated-call recovery is the subject here, not provenance. Use
        # a content-free failure; model-visible shell errors correctly arm the
        # exact-approval gate.
        return "bash", {"exit_code": 1}

    async def fake_synthesis(**kwargs):
        synthesis_calls.append(kwargs)
        return synthesis_result

    monkeypatch.setattr(agent_loop, "maybe_compact", fake_compact)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_synthesis)

    chunks = _collect(agent_loop.stream_agent_loop(
        primary[0],
        primary[1],
        [{"role": "user", "content": "Keep checking until you can answer."}],
        headers=primary[2],
        max_rounds=6,
        relevant_tools={"bash"},
        fallbacks=[backup],
        route_descriptors=[
            {
                "endpoint_id": "selected-ep",
                "endpoint_label": "Selected",
                "endpoint_cost_tracked": False,
            },
            {
                "endpoint_id": "backup-ep",
                "endpoint_label": "Backup",
                "endpoint_cost_tracked": True,
            },
        ],
        fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
        fallback_on_empty=False,
        _is_teacher_run=True,
    ))

    assert requests_by_round[0] == [
        (primary[0], primary[1]),
        (backup[0], backup[1]),
    ]
    assert requests_by_round[1:] == [[(backup[0], backup[1])]] * 5
    assert len(synthesis_calls) == 1
    assert synthesis_calls[0]["url"] == backup[0]
    assert synthesis_calls[0]["model"] == backup[1]
    assert synthesis_calls[0]["headers"] == backup[2]

    metrics = json.loads(next(
        chunk for chunk in chunks if '"type": "metrics"' in chunk
    )[6:])["data"]
    assert metrics["round_texts"][-1] == expected_answer
    assert metrics["round_models"][-1] == backup[1]
    assert metrics["round_endpoint_ids"][-1] == "backup-ep"
    assert metrics["usage_buckets"][-1] == {
        "round": 6,
        "model": backup[1],
        "endpoint_id": "backup-ep",
        "endpoint_label": "Backup",
        "input_tokens": 10,
        "output_tokens": len(synthesis_result) // 4,
        "usage_source": "estimated",
        "endpoint_cost_tracked": True,
    }
    assert len(metrics["usage_buckets"]) == 7


def test_agent_terminal_retains_completed_paid_fallback_usage(monkeypatch):
    calls = 0
    primary = ("http://localhost:11434/v1", "selected-model", {})
    backup = ("https://paid.example/v1", "backup-model", {})
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(
        agent_loop,
        "_agent_route_tool_mode",
        lambda *args, **kwargs: (True, False, False),
    )

    async def fake_stream(candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield 'data: {"type": "fallback", "selected_model": "selected-model", "answered_by": "backup-model", "candidate_index": 1, "selected_endpoint_id": "local", "selected_endpoint_label": "Local", "selected_endpoint_cost_tracked": false, "answered_by_endpoint_id": "paid", "answered_by_endpoint_label": "Paid", "answered_by_endpoint_cost_tracked": true}\n\n'
            yield 'data: {"type": "usage", "data": {"model": "backup-model", "input_tokens": 125, "output_tokens": 25}}\n\n'
            tool_call = {
                "name": "bash",
                "arguments": json.dumps({"command": "printf one"}),
            }
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [tool_call]})}\n\n'
            yield "data: [DONE]\n\n"
            return
        yield 'event: error\ndata: {"status": 400, "error": "unsupported model"}\n\n'

    async def fake_execute(block, *args, **kwargs):
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    chunks = _collect(agent_loop.stream_agent_loop(
        primary[0],
        primary[1],
        [{"role": "user", "content": "Run one tool."}],
        headers=primary[2],
        max_rounds=3,
        relevant_tools={"bash"},
        fallbacks=[backup],
        route_descriptors=[
            {"endpoint_id": "local", "endpoint_label": "Local", "endpoint_cost_tracked": False},
            {"endpoint_id": "paid", "endpoint_label": "Paid", "endpoint_cost_tracked": True},
        ],
        fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
        fallback_on_empty=False,
        _is_teacher_run=True,
    ))

    terminal = json.loads(next(
        chunk for chunk in chunks if '"type": "agent_terminal"' in chunk
    )[6:])["data"]
    assert terminal["input_tokens"] == 125
    assert terminal["output_tokens"] == 25
    assert terminal["usage_source"] == "real"
    assert terminal["usage_buckets"] == [{
        "round": 1,
        "model": "backup-model",
        "endpoint_id": "paid",
        "endpoint_label": "Paid",
        "input_tokens": 125,
        "output_tokens": 25,
        "usage_source": "real",
        "endpoint_cost_tracked": True,
    }]
    assert not any('"type": "metrics"' in chunk for chunk in chunks)


def test_agent_builds_backup_prompt_and_tool_transport_before_attempt(monkeypatch):
    requests = []
    primary = ("https://selected.example/v1", "selected-model", {})
    backup = ("http://localhost:11434/api/chat", "backup-model", {})

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(
        agent_loop,
        "_agent_route_tool_mode",
        lambda url, model, owner=None, headers=None: (model == "selected-model", model == "backup-model", False),
    )

    def fake_build(messages, model, *args, **kwargs):
        return (
            list(messages) + [{
                "role": "system",
                "content": f"route prompt for {model}",
                "_agent_injected": "prompt",
            }],
            [],
        )

    monkeypatch.setattr(agent_loop, "_build_system_prompt", fake_build)

    async def fake_stream(candidates, messages, **kwargs):
        factory = kwargs["candidate_request_factory"]
        requests.extend([
            await factory(index, *candidate)
            for index, candidate in enumerate(candidates)
        ])
        yield f'data: {json.dumps({"type": "fallback", "selected_model": primary[1], "answered_by": backup[1], "candidate_index": 1})}\n\n'
        yield 'data: {"delta": "backup answer"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    chunks = _collect(
        agent_loop.stream_agent_loop(
            primary[0],
            primary[1],
            [{"role": "user", "content": "Use bash if needed."}],
            headers=primary[2],
            max_rounds=1,
            relevant_tools={"bash"},
            fallbacks=[backup],
            fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
            fallback_on_empty=False,
            _is_teacher_run=True,
        )
    )

    assert requests[0]["kwargs"]["tools"]
    assert requests[1]["kwargs"]["tools"] is None
    backup_contents = [message.get("content") for message in requests[1]["messages"]]
    assert "route prompt for backup-model" in backup_contents
    assert "route prompt for selected-model" not in backup_contents
    assert any('"delta": "backup answer"' in chunk for chunk in chunks)


@pytest.mark.parametrize(
    ("primary_context", "backup_context", "expected_fallback_message_count"),
    [
        (1000, 100, 2),
        (100, 1000, 22),
    ],
)
def test_agent_fallback_request_uses_candidate_context_budget(
    monkeypatch,
    primary_context,
    backup_context,
    expected_fallback_message_count,
):
    requests_by_round = []
    context_lookups = []
    trim_budgets = []
    round_number = 0
    primary = ("https://selected.example/v1", "selected-model", {})
    backup = ("https://backup.example/v1", "backup-model", {})
    latest_user = "LATEST USER TURN MUST SURVIVE"
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"history-{index}"}
        for index in range(20)
    ] + [{"role": "user", "content": latest_user}]

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda messages: len(messages) * 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(
        agent_loop,
        "_agent_route_tool_mode",
        lambda *args, **kwargs: (True, False, False),
    )

    def fake_build(messages, model, *args, **kwargs):
        return ([{
            "role": "system",
            "content": f"route prompt for {model}",
            "_agent_injected": "prompt",
        }] + list(messages), [])

    monkeypatch.setattr(agent_loop, "_build_system_prompt", fake_build)

    import src.context_budget as context_budget
    import src.context_compactor as context_compactor
    import src.model_context as model_context

    def fake_context(candidate_url, candidate_model, fallback=0):
        context_lookups.append((candidate_url, candidate_model, fallback))
        return backup_context if candidate_model == "backup-model" else primary_context

    def fake_compute(soft_budget, candidate_context, explicit, hard_max=None):
        return candidate_context

    def fake_trim(messages, effective_budget, reserve_tokens=0):
        trim_budgets.append(effective_budget)
        if effective_budget != 100:
            return list(messages)
        route_prompt = next(
            message for message in messages
            if message.get("_agent_injected") == "prompt"
        )
        current_user = next(
            message for message in reversed(messages)
            if message.get("role") == "user"
        )
        return [route_prompt, current_user]

    monkeypatch.setattr(model_context, "budget_context_for_model", fake_context)
    monkeypatch.setattr(context_budget, "compute_input_token_budget", fake_compute)
    monkeypatch.setattr(context_budget, "budget_is_explicit", lambda value: False)
    monkeypatch.setattr(context_compactor, "trim_for_context", fake_trim)

    async def fake_stream(candidates, messages, **kwargs):
        nonlocal round_number
        round_number += 1
        factory = kwargs["candidate_request_factory"]
        requests = [
            await factory(index, *candidate)
            for index, candidate in enumerate(candidates)
        ]
        requests_by_round.append(requests)
        if round_number == 1:
            yield f'data: {json.dumps({"type": "fallback", "selected_model": primary[1], "answered_by": backup[1], "candidate_index": 1})}\n\n'
            tool_call = {
                "name": "bash",
                "arguments": json.dumps({"command": "printf one"}),
            }
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [tool_call]})}\n\n'
        else:
            yield 'data: {"delta": "pinned backup answer"}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(block, *args, **kwargs):
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    chunks = _collect(
        agent_loop.stream_agent_loop(
            primary[0],
            primary[1],
            history,
            headers=primary[2],
            max_rounds=2,
            relevant_tools={"bash"},
            fallbacks=[backup],
            fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
            fallback_on_empty=False,
            context_length=4096,
            _is_teacher_run=True,
        )
    )

    assert [(url, model) for url, model, _fallback in context_lookups] == [
        (primary[0], primary[1]),
        (backup[0], backup[1]),
        (backup[0], backup[1]),
    ]
    assert trim_budgets == [primary_context, backup_context, backup_context]
    fallback_messages = requests_by_round[0][1]["messages"]
    assert len(fallback_messages) == expected_fallback_message_count
    assert fallback_messages[0]["content"] == "route prompt for backup-model"
    assert any(
        message == {"role": "user", "content": latest_user}
        for message in fallback_messages
    )
    assert all("selected-model" not in str(message) for message in fallback_messages)
    pinned_messages = requests_by_round[1][0]["messages"]
    assert any(
        message == {"role": "user", "content": latest_user}
        for message in pinned_messages
    )
    assert pinned_messages[0]["content"] == "route prompt for backup-model"
    metrics = json.loads(next(
        chunk for chunk in chunks if '"type": "metrics"' in chunk
    )[6:])["data"]
    assert metrics["context_length"] == backup_context


def test_agent_persists_only_answering_route_compaction(monkeypatch):
    primary = ("https://selected.example/v1", "selected-model", {})
    backup = ("https://backup.example/v1", "backup-model", {})
    applied = []

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())

    async def fake_compact(
        session, url, model, messages, headers=None, owner=None,
        *, persist=True, compaction_state=None,
    ):
        assert persist is False
        compaction_state.update({"route": model, "applied": False})
        return (list(messages), 1000, True)

    def fake_apply(session, state):
        if not state or state.get("applied"):
            return False
        state["applied"] = True
        applied.append(state["route"])
        return True

    async def fake_stream(candidates, messages, **kwargs):
        factory = kwargs["candidate_request_factory"]
        for index, candidate in enumerate(candidates):
            await factory(index, *candidate)
        yield f'data: {json.dumps({"type": "fallback", "selected_model": primary[1], "answered_by": backup[1], "candidate_index": 1})}\n\n'
        yield 'data: {"delta": "backup answer"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "maybe_compact", fake_compact)
    monkeypatch.setattr(agent_loop, "apply_compaction_state", fake_apply)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    _collect(
        agent_loop.stream_agent_loop(
            primary[0],
            primary[1],
            [{"role": "user", "content": "Run a command after checking the route."}],
            headers=primary[2],
            history_session=object(),
            max_rounds=1,
            relevant_tools={"bash"},
            fallbacks=[backup],
            fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
            fallback_on_empty=False,
            _is_teacher_run=True,
        )
    )

    assert applied == ["backup-model"]


def test_agent_deferred_compaction_survives_duplicate_primary_fallback(monkeypatch):
    primary = ("https://selected.example/v1", "selected-model", {})
    compacted_routes = []

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())

    async def fake_compact(
        session, url, model, messages, headers=None, owner=None,
        *, persist=True, compaction_state=None,
    ):
        assert persist is False
        compacted_routes.append((url, model))
        return (list(messages), 1000, False)

    async def fake_stream(candidates, messages, **kwargs):
        assert candidates == [primary]
        request = await kwargs["candidate_request_factory"](0, *primary)
        assert request["messages"]
        yield 'data: {"delta": "answer"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "maybe_compact", fake_compact)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    chunks = _collect(agent_loop.stream_agent_loop(
        primary[0],
        primary[1],
        [{"role": "user", "content": "Investigate this."}],
        headers=primary[2],
        relevant_tools={"bash"},
        fallbacks=[primary],
        defer_context_shaping=True,
        max_rounds=1,
        fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
        fallback_on_empty=False,
        _is_teacher_run=True,
    ))

    assert compacted_routes == [(primary[0], primary[1])]
    assert any('"delta": "answer"' in chunk for chunk in chunks)


def test_skill_activation_reaches_later_fallback_request_and_pinned_round(monkeypatch):
    requests_by_round = []
    round_number = 0
    primary = ("https://selected.example/v1", "selected-model", {})
    backup = ("https://backup.example/v1", "odysseus-qwen-backup", {})

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(
        agent_loop,
        "_is_odysseus_qwen_model",
        lambda model: model == backup[1],
    )
    monkeypatch.setattr(
        agent_loop,
        "_agent_route_tool_mode",
        lambda url, model, owner=None, headers=None: (
            model == "selected-model",
            False,
            False,
        ),
    )

    def fake_build(messages, model, *args, **kwargs):
        route_tools = sorted(kwargs.get("relevant_tools") or [])
        return (
            list(messages) + [{
                "role": "system",
                "content": f"route={model}; tools={','.join(route_tools)}",
                "_agent_injected": "prompt",
            }],
            [],
        )

    monkeypatch.setattr(agent_loop, "_build_system_prompt", fake_build)

    import services.memory.skills as skills_module
    import src.tool_policy as tool_policy

    class FakeSkillsManager:
        def __init__(self, data_dir):
            pass

        def load(self, owner=None):
            return [{
                "name": "runtime-skill",
                "requires_toolsets": ["grep"],
            }]

        def get_relevant_skills(self, *args, **kwargs):
            return []

    monkeypatch.setattr(skills_module, "SkillsManager", FakeSkillsManager)
    monkeypatch.setattr(tool_policy, "known_tool_names", lambda: {"manage_skills", "grep"})

    async def fake_stream(candidates, messages, **kwargs):
        nonlocal round_number
        round_number += 1
        factory = kwargs["candidate_request_factory"]
        requests = [
            await factory(index, *candidate)
            for index, candidate in enumerate(candidates)
        ]
        requests_by_round.append((list(candidates), requests))

        if round_number == 1:
            call = {
                "name": "manage_skills",
                "arguments": json.dumps({"action": "view", "name": "runtime-skill"}),
            }
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        elif round_number == 2:
            yield f'data: {json.dumps({"type": "fallback", "selected_model": primary[1], "answered_by": backup[1], "candidate_index": 1})}\n\n'
            call = {
                "name": "grep",
                "arguments": json.dumps({"pattern": "needle", "path": "."}),
            }
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        else:
            yield 'data: {"delta": "pinned backup answer"}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(block, *args, **kwargs):
        return block.tool_type, {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    chunks = _collect(
        agent_loop.stream_agent_loop(
            primary[0],
            primary[1],
            [{"role": "user", "content": "Load runtime-skill, then use it."}],
            headers=primary[2],
            max_rounds=3,
            relevant_tools={"manage_skills"},
            fallbacks=[backup],
            fallback_statuses=FOREGROUND_AVAILABILITY_STATUSES,
            fallback_on_empty=False,
            _is_teacher_run=True,
        )
    )

    round_two_candidates, round_two_requests = requests_by_round[1]
    assert round_two_candidates == [primary, backup]
    primary_schema_names = {
        schema["function"]["name"]
        for schema in round_two_requests[0]["kwargs"]["tools"]
    }
    assert "grep" in primary_schema_names
    assert round_two_requests[1]["kwargs"]["tools"] is None
    assert any(
        "route=odysseus-qwen-backup; tools=grep,manage_skills" in (message.get("content") or "")
        for message in round_two_requests[1]["messages"]
    )

    round_three_candidates, round_three_requests = requests_by_round[2]
    assert round_three_candidates == [backup]
    assert any(
        "route=odysseus-qwen-backup; tools=grep,manage_skills" in (message.get("content") or "")
        for message in round_three_requests[0]["messages"]
    )
    assert any('"delta": "pinned backup answer"' in chunk for chunk in chunks)
