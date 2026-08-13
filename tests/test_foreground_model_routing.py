"""Regression coverage for strict foreground model selection."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import src.agent_loop as agent_loop
import src.endpoint_resolver as endpoint_resolver
import src.foreground_model_routing as foreground_model_routing
import routes.chat_routes as chat_routes
from src.foreground_model_routing import (
    build_foreground_model_candidates,
    resolve_foreground_fallback_candidates,
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
    def __init__(self, mode):
        self.headers = {}
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=None))
        self._form = {
            "message": "hello",
            "session": "session-1",
            "mode": mode,
            "compare_mode": "true",
        }

    async def form(self):
        return self._form


def _chat_stream_endpoint(monkeypatch, mode, captured):
    session = SimpleNamespace(
        endpoint_url="https://selected.example/v1",
        model="selected-model",
        headers={"Authorization": "Bearer selected"},
        name="test",
        history=[],
        add_message=lambda message: None,
    )
    session_manager = SimpleNamespace(
        get_session=lambda session_id: session,
        save_sessions=lambda: None,
    )
    context = SimpleNamespace(
        user="alice",
        messages=[{"role": "user", "content": "hello"}],
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
        return context

    async def fake_chat_stream(candidates, messages, **kwargs):
        captured["chat"] = candidates
        yield f'data: {json.dumps({"delta": "done"})}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_agent_stream(endpoint_url, model, messages, **kwargs):
        captured["agent"] = {
            "primary": (endpoint_url, model, kwargs.get("headers")),
            "fallbacks": kwargs.get("fallbacks"),
        }
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
    monkeypatch.setattr(chat_routes, "save_assistant_response", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "estimate_tokens", lambda messages: 10)
    monkeypatch.setattr(
        endpoint_resolver,
        "resolve_chat_fallback_candidates",
        lambda owner=None: [("https://legacy.example/v1", "legacy-model", {})],
    )

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


def test_candidate_builder_appends_only_policy_authorized_fallbacks(monkeypatch):
    """Chat and Agent share the same candidate-building policy boundary."""

    authorized = [("https://opt-in.example/v1", "opt-in-model", {})]
    monkeypatch.setattr(
        foreground_model_routing,
        "resolve_foreground_fallback_candidates",
        lambda owner=None: authorized,
    )

    assert build_foreground_model_candidates(
        "https://selected.example/v1",
        "selected-model",
        {"Authorization": "Bearer selected"},
        owner="alice",
    ) == [
        ("https://selected.example/v1", "selected-model", {"Authorization": "Bearer selected"}),
        *authorized,
    ]


def test_strict_policy_builds_only_the_selected_chat_candidate():
    candidates = build_foreground_model_candidates(
        "https://selected.example/v1",
        "selected-model",
        {"Authorization": "Bearer selected"},
        owner="alice",
    )

    assert candidates == [
        ("https://selected.example/v1", "selected-model", {"Authorization": "Bearer selected"})
    ]


def test_legacy_chat_resolver_is_disconnected():
    assert endpoint_resolver.resolve_chat_fallback_candidates(owner="alice") == []


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

    fallbacks = resolve_foreground_fallback_candidates(owner="alice")
    chunks = _collect(
        agent_loop.stream_agent_loop(
            "https://selected.example/v1",
            "selected-model",
            [{"role": "user", "content": "Run one tool and report back."}],
            max_rounds=3,
            relevant_tools={"bash"},
            fallbacks=fallbacks,
            _is_teacher_run=True,
        )
    )

    assert seen_candidates == [
        [("https://selected.example/v1", "selected-model")],
        [("https://selected.example/v1", "selected-model")],
    ]
    assert any('"delta": "done"' in chunk for chunk in chunks)
