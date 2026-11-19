"""Persistence contract for streaming generation-budget warnings (COOKP1-1).

The live SSE ``generation_budget`` notice must also be persisted with the
saved streamed reply, so history renders the identical requested/effective
report (chatRenderer already consumes ``metadata.generation_budget``).

The main paths drive the REAL ``llm_core.stream_llm_with_fallback`` budget
fit and SSE emission against an ``httpx.MockTransport`` provider fixture, and
the REAL ``/api/chat_stream`` chat-mode generator. No inference, no model
loads, no downloads. Disconnect/latest-report paths use synthetic SSE chunks.

Endpoint harness adapted from tests/test_foreground_model_routing.py
(``_chat_stream_endpoint``), with a session ``max_tokens_override`` so the
budget fit actually runs.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

import routes.chat_routes as chat_routes
import routes.chat_helpers as chat_helpers
import src.foreground_model_routing as foreground_model_routing
import src.llm_core as llm_core
from src.generation_budget import context_safety_margin


BUDGET_FIELDS = (
    "requested_max_tokens",
    "effective_max_tokens",
    "context_length",
    "estimated_input_tokens",
    "safety_margin_tokens",
    "available_output_tokens",
    "safe_to_generate",
    "warning",
)


def _budget_event(requested=4096, effective=120, *, candidate_index=0,
                  model="budget-unit-fixture", warning="fixture warning"):
    return {
        "type": "generation_budget",
        "candidate_index": candidate_index,
        "model": model,
        "requested_max_tokens": requested,
        "effective_max_tokens": effective,
        "context_length": 512,
        "estimated_input_tokens": 300,
        "safety_margin_tokens": 64,
        "available_output_tokens": effective,
        "safe_to_generate": effective > 0,
        "warning": warning,
    }


def _strip_envelope(event):
    return {k: v for k, v in event.items()
            if k not in ("type", "candidate_index", "model")}


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
    def __init__(self):
        self.headers = {}
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=None))
        self.state = SimpleNamespace(current_user="alice")
        self._form = {
            "message": "hello",
            "session": "session-1",
            "mode": "chat",
            "compare_mode": "true",
            "no_documents": "true",
            "no_memory": "true",
            "use_rag": "false",
        }

    async def form(self):
        return self._form


def _endpoint(monkeypatch, captured, *, max_tokens_override,
              context_length=512, transport_events=None, chat_chunks=None):
    """Build the real /api/chat_stream endpoint with fixture-only deps.

    transport_events: SSE event dicts served by the MockTransport provider
    (the real stream_llm_with_fallback budget fit + emission runs above it).
    chat_chunks: synthetic SSE chunks bypassing llm_core entirely (used for
    disconnect and latest-report-wins contracts).
    """
    def add_message(message):
        captured.setdefault("added_messages", []).append(message)

    session = SimpleNamespace(
        endpoint_url="http://127.0.0.1:18101/v1",
        model="budget-unit-fixture",
        headers={},
        name="test",
        history=[],
        add_message=add_message,
        max_tokens_override=max_tokens_override,
        temperature_override=None,
    )
    session_manager = SimpleNamespace(
        get_session=lambda session_id: session,
        save_sessions=lambda: None,
    )
    context = SimpleNamespace(
        user="alice",
        messages=[{"role": "user", "content": "hello"}],
        route_messages=[{"role": "user", "content": "hello"}],
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
        captured.setdefault("chat_candidates", []).append(candidates)
        for chunk in chat_chunks or []:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

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
    monkeypatch.setattr(chat_routes, "save_assistant_response",
                        lambda *args, **kwargs: captured.setdefault("saved", []).append((args, kwargs)))
    monkeypatch.setattr(chat_routes, "run_post_response_tasks",
                        lambda *args, **kwargs: captured.setdefault("post_processed", []).append((args, kwargs)))
    monkeypatch.setattr(chat_routes, "estimate_tokens", lambda messages: 10)
    monkeypatch.setattr(chat_routes, "accumulate_token_usage", lambda *args, **kwargs: None)
    # Strict single-candidate foreground policy: no fallback chain.
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner=None: {})

    import src.settings as settings
    monkeypatch.setattr(settings, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(settings, "get_user_setting", lambda key, owner="", default=None: default)

    if chat_chunks is not None:
        monkeypatch.setattr(chat_routes, "stream_llm_with_fallback", fake_chat_stream)
    else:
        # Real budget fit + real SSE emission; only the provider transport is fake.
        monkeypatch.setattr(chat_routes, "stream_llm_with_fallback", llm_core.stream_llm_with_fallback)
        monkeypatch.setattr(llm_core, "get_context_length", lambda *args: context_length)
        def _frame(event):
            if event.get("_done"):
                return "data: [DONE]\n\n"
            payload = {k: v for k, v in event.items() if k not in ("_error", "_done")}
            prefix = "event: error\n" if event.get("_error") else ""
            return prefix + f"data: {json.dumps(payload)}\n\n"

        body = "".join(_frame(event) for event in (transport_events or []))

        def handler(request):
            captured.setdefault("transport_payloads", []).append(json.loads(request.content))
            return httpx.Response(200, text=body)

        async def fake_stream_llm(url, model, messages, **kwargs):
            captured.setdefault("stream_kwargs", []).append(dict(kwargs))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                async with client.stream("POST", url, json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": kwargs.get("max_tokens"),
                    "stream": True,
                }) as response:
                    frame = []
                    async for line in response.aiter_lines():
                        if line == "":
                            if frame:
                                yield "\n".join(frame) + "\n\n"
                                frame = []
                        else:
                            frame.append(line)
                    if frame:
                        yield "\n".join(frame) + "\n\n"

        monkeypatch.setattr(llm_core, "stream_llm", fake_stream_llm)

    router = chat_routes.setup_chat_routes(
        session_manager,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    return next(route.endpoint for route in router.routes if route.path == "/api/chat_stream")


def _drive(endpoint):
    async def _run():
        response = await endpoint(_RouteRequest())
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        return chunks

    return asyncio.run(_run())


def _data_events(chunks):
    events = []
    for chunk in chunks:
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                events.append(json.loads(chunk[6:]))
            except json.JSONDecodeError:
                pass
    return events


def _saved_metrics(captured):
    assert captured.get("saved"), "expected the streamed reply to be saved"
    args, kwargs = captured["saved"][0]
    # save_assistant_response(sess, session_manager, session_id, content, metrics, ...)
    return args[3], args[4]


def test_stream_budget_notice_is_persisted_with_saved_reply(monkeypatch):
    captured = {}
    endpoint = _endpoint(
        monkeypatch, captured,
        max_tokens_override=4096,
        context_length=512,
        transport_events=[{"delta": "unit-fixture-only"}, {"_done": True}],
    )
    chunks = _drive(endpoint)
    events = _data_events(chunks)
    notices = [e for e in events if e.get("type") == "generation_budget"]
    assert len(notices) == 1, "live stream must still forward exactly one budget notice"
    notice = notices[0]
    assert notice["requested_max_tokens"] == 4096
    assert 0 < notice["effective_max_tokens"] < 4096
    assert notice["safe_to_generate"] is True
    assert notice["warning"] and "larger context" in notice["warning"]

    # The provider request was actually clamped to the effective budget.
    payload = captured["transport_payloads"][0]
    assert payload["max_tokens"] == notice["effective_max_tokens"]

    content, metrics = _saved_metrics(captured)
    assert content == "unit-fixture-only"
    saved = metrics.get("generation_budget")
    assert saved is not None, "saved reply must carry the budget metadata"
    assert saved == _strip_envelope(notice)
    # Every field the history renderer's notice needs is present and identical.
    for field in BUDGET_FIELDS:
        assert saved[field] == notice[field]


def test_stream_without_budget_keeps_saved_metadata_clean(monkeypatch):
    captured = {}
    endpoint = _endpoint(
        monkeypatch, captured,
        max_tokens_override=0,
        transport_events=[{"delta": "unit-fixture-only"}, {"_done": True}],
    )
    chunks = _drive(endpoint)
    assert not [e for e in _data_events(chunks) if e.get("type") == "generation_budget"]
    content, metrics = _saved_metrics(captured)
    assert content == "unit-fixture-only"
    assert "generation_budget" not in metrics
    assert captured["stream_kwargs"][0].get("max_tokens") == 0


def test_terminal_error_save_keeps_budget_metadata(monkeypatch):
    captured = {}
    endpoint = _endpoint(
        monkeypatch, captured,
        max_tokens_override=4096,
        context_length=512,
        transport_events=[
            {"delta": "partial answer"},
            {"_error": True, "status": 500, "text": "fixture backend failure"},
        ],
    )
    chunks = _drive(endpoint)
    events = _data_events(chunks)
    notices = [e for e in events if e.get("type") == "generation_budget"]
    assert len(notices) == 1
    assert any(e.get("type") == "chat_terminal" for e in events)
    content, metrics = _saved_metrics(captured)
    assert "partial answer" in content
    assert metrics.get("failed") is True
    assert metrics.get("generation_budget") == _strip_envelope(notices[0])


def test_disconnect_partial_save_keeps_budget_metadata(monkeypatch):
    captured = {}
    notice = _budget_event()
    endpoint = _endpoint(
        monkeypatch, captured,
        max_tokens_override=4096,
        chat_chunks=[
            f"data: {json.dumps(notice)}\n\n",
            'data: {"delta": "partial answer"}\n\n',
            asyncio.CancelledError(),
        ],
    )
    with pytest.raises(asyncio.CancelledError):
        _drive(endpoint)
    assert captured.get("added_messages"), "disconnect must save the partial reply"
    saved_md = captured["added_messages"][0].metadata
    assert saved_md.get("stopped") is True
    assert saved_md.get("generation_budget") == _strip_envelope(notice)


def test_latest_budget_report_wins_for_persistence(monkeypatch):
    captured = {}
    constrained = _budget_event(requested=4096, effective=120)
    fitting = _budget_event(requested=4096, effective=4096, warning=None,
                            candidate_index=1, model="fallback-fixture")
    endpoint = _endpoint(
        monkeypatch, captured,
        max_tokens_override=4096,
        chat_chunks=[
            f"data: {json.dumps(constrained)}\n\n",
            f"data: {json.dumps(fitting)}\n\n",
            'data: {"delta": "unit-fixture-only"}\n\n',
            "data: [DONE]\n\n",
        ],
    )
    chunks = _drive(endpoint)
    notices = [e for e in _data_events(chunks) if e.get("type") == "generation_budget"]
    assert notices == [constrained, fitting], "both live notices are forwarded unchanged"
    content, metrics = _saved_metrics(captured)
    assert metrics.get("generation_budget") == _strip_envelope(fitting)


def test_saved_report_matches_nonstreaming_shape():
    # The persisted streaming report must use the same keys as the
    # non-streaming /api/chat metadata (src/generation_budget.py details),
    # which the history renderer already consumes.
    notice = _budget_event()
    saved = _strip_envelope(notice)
    details = {}
    from src.generation_budget import fit_output_token_budget
    fit_output_token_budget(4096, 512, [{"role": "user", "content": "x" * 1200}],
                            details=details)
    assert set(saved) == set(details) == set(BUDGET_FIELDS)
    assert context_safety_margin(512) == 64
