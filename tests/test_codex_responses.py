"""Tests for the codex_responses transport, stream decoder, registry/spec wiring,
and the additive engine guards (OAuth response-cache bypass, sync-path refusal).

The transport/decoder are pure (no DB), so these import cleanly. End-to-end tests
drive the REAL `src.llm_core` (which imports only httpx/fastapi/stdlib + providers,
so it is immune to the sys.modules stubbing other test files install) by injecting
an `httpx.MockTransport` at `llm_core._get_http_client` (every async egress funnels
through it) plus a monkeypatched `auth.resolve` — no DB, no real OpenAI calls, and
no network-mocking dependency (MockTransport ships with httpx).

Golden stream/request shapes cribbed from pi's `openai-codex-stream.test.ts` +
`openai-responses-shared.ts`.
"""
import json

import httpx
import pytest

import src.llm_core as llm_core
from src.providers import auth, registry
from src.providers.auth.credentials import Credential
from src.providers.codex_responses import (
    CodexResponsesTransport,
    CodexStreamDecoder,
    ORIGINATOR,
    USER_AGENT,
)
from src.providers.endpoint_ref import EndpointRef
from src.providers.spec import CODEX_MODELS, OPENAI_CODEX_SPEC

CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

T = CodexResponsesTransport()


# ── SSE helpers (mirror tests/test_llm_core.py) ──────────────────────────────

def _sse(*events: dict) -> bytes:
    return ("".join(f"data: {json.dumps(e)}\n\n" for e in events)).encode()


def _data(chunk: str) -> dict:
    assert chunk.startswith("data: "), chunk
    return json.loads(chunk[len("data: "):].strip())


async def _collect(agen):
    return [c async for c in agen]


def _codex_ref(endpoint_id="ep-1", owner="alice"):
    return EndpointRef(url=CODEX_URL, model="gpt-5.4", endpoint_id=endpoint_id,
                       owner=owner, provider_id="openai-codex", auth_type="oauth")


def _fake_creds():
    return Credential(headers={"Authorization": "Bearer tok-xyz", "chatgpt-account-id": "acct-1"})


class _MockHTTP:
    """Routes httpx traffic to queued responses via ``httpx.MockTransport`` — a
    built-in httpx primitive, so the e2e tests carry no respx/network-mock dep.

    Captures every outgoing request (for header/body assertions); per-URL queues
    allow sequencing (mirrors respx's ``side_effect=[...]``). A single URL with one
    queued response repeats it (mirrors ``return_value=``)."""

    def __init__(self):
        self.calls = []          # list[httpx.Request], in send order
        self._routes = {}        # str(url) -> list[httpx.Response]

    def route(self, url, *responses):
        self._routes.setdefault(str(url), []).extend(responses)
        return self

    def _handle(self, request):
        self.calls.append(request)
        queue = self._routes.get(str(request.url))
        if not queue:
            return httpx.Response(500, json={"error": f"unrouted {request.url}"})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def new_client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def count(self, url):
        return sum(1 for r in self.calls if str(r.url) == str(url))

    @property
    def last(self):
        return self.calls[-1]


# ── Stream event builders (golden shapes) ────────────────────────────────────

def _ev_msg_added(mid="msg_1"):
    return {"type": "response.output_item.added",
            "item": {"type": "message", "id": mid, "role": "assistant", "status": "in_progress", "content": []}}

def _ev_part_added():
    return {"type": "response.content_part.added", "part": {"type": "output_text", "text": ""}}

def _ev_text_delta(text):
    return {"type": "response.output_text.delta", "delta": text}

def _ev_msg_done(text, mid="msg_1"):
    return {"type": "response.output_item.done",
            "item": {"type": "message", "id": mid, "role": "assistant", "status": "completed",
                     "content": [{"type": "output_text", "text": text}]}}

def _ev_completed(status="completed", inp=5, out=3):
    return {"type": "response.completed" if status == "completed" else f"response.{status}",
            "response": {"status": status,
                         "usage": {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out,
                                   "input_tokens_details": {"cached_tokens": 0}}}}


# ══════════════════════════════════════════════════════════════════════════════
# Spec + registry wiring
# ══════════════════════════════════════════════════════════════════════════════

class TestSpecAndRegistry:
    def test_detect_codex_by_url(self):
        spec = registry.detect_spec(CODEX_URL)
        assert spec.id == "openai-codex" and spec.transport == "codex_responses" and spec.auth_type == "oauth"

    def test_codex_base_url_also_detected(self):
        assert registry.detect_spec("https://chatgpt.com/backend-api/codex").id == "openai-codex"

    def test_openai_still_default(self):
        assert registry.detect_spec(OPENAI_URL).id == "openai"

    def test_transport_resolves(self):
        assert isinstance(registry.get_transport(OPENAI_CODEX_SPEC), CodexResponsesTransport)

    def test_codex_models_nonempty(self):
        assert CODEX_MODELS and all(isinstance(m, str) for m in CODEX_MODELS)


# ══════════════════════════════════════════════════════════════════════════════
# Pure transport shaping
# ══════════════════════════════════════════════════════════════════════════════

class TestTargetUrl:
    def test_base_appends_responses(self):
        assert T.target_url("https://chatgpt.com/backend-api/codex") == CODEX_URL

    def test_full_url_unchanged(self):
        assert T.target_url(CODEX_URL) == CODEX_URL

    def test_trailing_slash_normalized(self):
        assert T.target_url("https://chatgpt.com/backend-api/codex/") == CODEX_URL


class TestBuildPayload:
    def _msgs(self):
        return [{"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hi"}]

    def test_core_shape(self):
        p = T.build_payload("gpt-5.4", self._msgs(), 0.7, 4096, stream=True)
        assert p["store"] is False and p["stream"] is True
        assert p["include"] == ["reasoning.encrypted_content"]
        assert p["tool_choice"] == "auto" and p["parallel_tool_calls"] is True
        assert p["text"] == {"verbosity": "low"}
        assert p["instructions"] == "You are helpful."

    def test_omits_max_tokens(self):
        p = T.build_payload("gpt-5.4", self._msgs(), 0.7, 4096)
        assert "max_tokens" not in p and "max_completion_tokens" not in p and "max_output_tokens" not in p

    def test_uses_input_not_messages(self):
        p = T.build_payload("gpt-5.4", self._msgs(), 0.7, 0)
        assert "messages" not in p and "input" in p
        assert p["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]

    def test_stream_defaults_false(self):
        assert T.build_payload("gpt-5.4", self._msgs(), 0.7, 0)["stream"] is False

    def test_multimodal_user_content(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]}]
        p = T.build_payload("gpt-5.4", msgs, 0.7, 0)
        assert p["input"][0]["content"] == [
            {"type": "input_text", "text": "what is this"},
            {"type": "input_image", "detail": "auto", "image_url": "data:image/png;base64,AAAA"},
        ]

    def test_tool_transcript_conversion(self):
        msgs = [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "get_weather", "arguments": '{"city":"NYC"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "72F"},
        ]
        p = T.build_payload("gpt-5.4", msgs, 0.7, 0)
        # function_call carries call_id + args string; result pairs by call_id.
        fc = [i for i in p["input"] if i.get("type") == "function_call"][0]
        fco = [i for i in p["input"] if i.get("type") == "function_call_output"][0]
        assert fc == {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": '{"city":"NYC"}'}
        assert fco == {"type": "function_call_output", "call_id": "call_1", "output": "72F"}

    def test_assistant_text_becomes_output_message(self):
        msgs = [{"role": "assistant", "content": "prior answer"}]
        p = T.build_payload("gpt-5.4", msgs, 0.7, 0)
        assert p["input"][0] == {"type": "message", "role": "assistant", "status": "completed",
                                 "content": [{"type": "output_text", "text": "prior answer", "annotations": []}]}

    def test_tools_converted_flat(self):
        tools = [{"type": "function", "function": {"name": "foo", "description": "d", "parameters": {"type": "object"}}}]
        p = T.build_payload("gpt-5.4", self._msgs(), 0.7, 0, stream=True, tools=tools)
        assert p["tools"] == [{"type": "function", "name": "foo", "description": "d",
                               "parameters": {"type": "object"}, "strict": None}]

    def test_omits_temperature(self):
        # codex Responses rejects temperature (reasoning models) — mirror max_tokens.
        assert "temperature" not in T.build_payload("gpt-5.4", self._msgs(), 0.55, 0)

    def test_no_system_no_instructions(self):
        p = T.build_payload("gpt-5.4", [{"role": "user", "content": "hi"}], 0.7, 0)
        assert "instructions" not in p


class TestBuildHeaders:
    def test_wire_headers_present(self):
        h = T.build_headers(None)
        assert h["OpenAI-Beta"] == "responses=experimental"
        assert h["originator"] == ORIGINATOR == "codex_cli_rs"
        assert h["User-Agent"] == USER_AGENT
        assert h["Accept"] == "text/event-stream"

    def test_credentials_merged(self):
        h = T.build_headers({"Authorization": "Bearer X", "chatgpt-account-id": "acct"})
        assert h["Authorization"] == "Bearer X" and h["chatgpt-account-id"] == "acct"
        assert h["originator"] == "codex_cli_rs"  # creds don't clobber wire headers


class TestParseResponse:
    def test_output_message(self):
        data = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]}
        assert T.parse_response(data) == "Hello"

    def test_output_text_convenience_field(self):
        assert T.parse_response({"output_text": "Hi"}) == "Hi"

    def test_skips_reasoning_items(self):
        data = {"output": [{"type": "reasoning", "summary": []},
                           {"type": "message", "content": [{"type": "output_text", "text": "A"}]}]}
        assert T.parse_response(data) == "A"


# ══════════════════════════════════════════════════════════════════════════════
# Stream decoder (golden sequences)
# ══════════════════════════════════════════════════════════════════════════════

def _run_decoder(events, model="gpt-5.4"):
    d = CodexStreamDecoder(model)
    out, stopped = [], False
    for ev in events:
        chunks, stop = d.decode_line(f"data: {json.dumps(ev)}")
        out.extend(chunks)
        if stop:
            stopped = True
            break
    if not stopped:
        out.extend(d.finalize())
    return out


class TestCodexStreamDecoder:
    def test_golden_text_stream(self):
        out = _run_decoder([_ev_msg_added(), _ev_part_added(), _ev_text_delta("Hello"),
                            _ev_msg_done("Hello"), _ev_completed()])
        assert _data(out[0]) == {"delta": "Hello"}
        assert _data(out[1]) == {"type": "usage", "data": {"input_tokens": 5, "output_tokens": 3}}
        assert out[-1].strip() == "data: [DONE]"

    def test_text_streamed_via_deltas_not_doubled(self):
        out = _run_decoder([_ev_msg_added(), _ev_part_added(),
                            _ev_text_delta("Hel"), _ev_text_delta("lo"),
                            _ev_msg_done("Hello"), _ev_completed()])
        deltas = [_data(c)["delta"] for c in out if c.startswith("data: {") and "delta" in _data(c)]
        assert deltas == ["Hel", "lo"]  # message.done did NOT re-emit "Hello"

    def test_message_done_without_deltas_emits_once(self):
        # Defensive: if a backend skips deltas, the finished text is emitted once.
        out = _run_decoder([_ev_msg_added(), _ev_msg_done("Solo"), _ev_completed()])
        deltas = [_data(c)["delta"] for c in out if c.startswith("data: {") and "delta" in _data(c)]
        assert deltas == ["Solo"]

    def test_tool_call_accumulation(self):
        events = [
            {"type": "response.output_item.added",
             "item": {"type": "function_call", "id": "fc_1", "call_id": "call_abc", "name": "get_weather", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "delta": '{"city":'},
            {"type": "response.function_call_arguments.delta", "delta": '"NYC"}'},
            {"type": "response.function_call_arguments.done", "arguments": '{"city":"NYC"}'},
            {"type": "response.output_item.done",
             "item": {"type": "function_call", "id": "fc_1", "call_id": "call_abc", "name": "get_weather", "arguments": '{"city":"NYC"}'}},
            _ev_completed(),
        ]
        out = _run_decoder(events)
        tc = [_data(c) for c in out if c.startswith("data: {") and _data(c).get("type") == "tool_calls"][0]
        assert tc["calls"] == [{"id": "call_abc", "name": "get_weather", "arguments": '{"city":"NYC"}'}]
        assert out[-1].strip() == "data: [DONE]"

    def test_doc_tool_arg_deltas_streamed(self):
        events = [
            {"type": "response.output_item.added",
             "item": {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "create_document", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "delta": '{"title":"x"}'},
            {"type": "response.output_item.done",
             "item": {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "create_document", "arguments": '{"title":"x"}'}},
            _ev_completed(),
        ]
        out = _run_decoder(events)
        deltas = [_data(c) for c in out if c.startswith("data: {") and _data(c).get("type") == "tool_call_delta"]
        assert deltas and deltas[0]["name"] == "create_document" and deltas[0]["arg_delta"] == '{"title":"x"}'

    def test_reasoning_summary_streams_as_thinking(self):
        events = [
            {"type": "response.output_item.added", "item": {"type": "reasoning", "id": "rs_1"}},
            {"type": "response.reasoning_summary_text.delta", "delta": "pondering"},
            _ev_msg_added("msg_2"), _ev_text_delta("Done"), _ev_completed(),
        ]
        out = _run_decoder(events)
        assert _data(out[0]) == {"delta": "pondering", "thinking": True}
        assert _data(out[1]) == {"delta": "Done"}

    def test_incomplete_is_terminal_with_usage(self):
        out = _run_decoder([_ev_msg_added(), _ev_text_delta("Hi"), _ev_completed(status="incomplete", inp=1, out=1)])
        assert _data(out[0]) == {"delta": "Hi"}
        assert _data(out[1]) == {"type": "usage", "data": {"input_tokens": 1, "output_tokens": 1}}
        assert out[-1].strip() == "data: [DONE]"

    def test_explicit_done_marker(self):
        d = CodexStreamDecoder("gpt-5.4")
        chunks, stop = d.decode_line("data: [DONE]")
        assert stop is True and chunks[-1].strip() == "data: [DONE]"

    def test_error_event_emits_error_and_stops(self):
        d = CodexStreamDecoder("gpt-5.4")
        chunks, stop = d.decode_line(f'data: {json.dumps({"type": "error", "code": "rate_limit", "message": "too many"})}')
        assert stop is True
        assert chunks[0].startswith("event: error")
        assert "too many" in chunks[0]

    def test_response_failed_emits_error(self):
        d = CodexStreamDecoder("gpt-5.4")
        chunks, stop = d.decode_line(f'data: {json.dumps({"type": "response.failed", "response": {"error": {"message": "boom"}}})}')
        assert stop is True and "boom" in chunks[0]

    def test_non_data_lines_ignored(self):
        d = CodexStreamDecoder("gpt-5.4")
        assert d.decode_line("event: foo") == ([], False)
        assert d.decode_line("") == ([], False)
        assert d.decode_line("data: not-json") == ([], False)


# ══════════════════════════════════════════════════════════════════════════════
# End-to-end through llm_core (httpx.MockTransport-injected codex backend)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    llm_core._response_cache.clear()
    llm_core._dead_hosts.clear()
    llm_core._host_fails.clear()
    llm_core._model_activity.clear()
    yield
    llm_core._response_cache.clear()


@pytest.fixture
def codex_http(monkeypatch):
    """Inject a MockTransport client at llm_core's single async egress seam
    (``_get_http_client``) — every codex/openai async call funnels through it."""
    mock = _MockHTTP()
    client = mock.new_client()
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    return mock


def _patch_oauth_resolve(monkeypatch):
    async def _fake(ref, spec):
        return _fake_creds()
    monkeypatch.setattr(auth, "resolve", _fake)


class TestEndToEndStream:
    async def test_stream_text_through_llm_core(self, monkeypatch, codex_http):
        _patch_oauth_resolve(monkeypatch)
        sse = _sse(_ev_msg_added(), _ev_part_added(), _ev_text_delta("Hello"),
                   _ev_msg_done("Hello"), _ev_completed())
        codex_http.route(CODEX_URL, httpx.Response(200, content=sse))
        chunks = await _collect(llm_core.stream_llm(CODEX_URL, "gpt-5.4",
                                                    [{"role": "user", "content": "hi"}], ref=_codex_ref()))
        # codex wire headers reached the backend
        sent = codex_http.last
        assert sent.headers["originator"] == "codex_cli_rs"
        assert sent.headers["authorization"] == "Bearer tok-xyz"
        assert json.loads(sent.content)["store"] is False
        # normalized chunks
        text = [_data(c)["delta"] for c in chunks if c.startswith("data: {") and "delta" in _data(c)]
        assert text == ["Hello"]
        assert chunks[-1].strip() == "data: [DONE]"

    async def test_stream_tool_call_through_llm_core(self, monkeypatch, codex_http):
        _patch_oauth_resolve(monkeypatch)
        sse = _sse(
            {"type": "response.output_item.added",
             "item": {"type": "function_call", "id": "fc_1", "call_id": "c9", "name": "search", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "delta": '{"q":"x"}'},
            {"type": "response.output_item.done",
             "item": {"type": "function_call", "id": "fc_1", "call_id": "c9", "name": "search", "arguments": '{"q":"x"}'}},
            _ev_completed(),
        )
        codex_http.route(CODEX_URL, httpx.Response(200, content=sse))
        chunks = await _collect(llm_core.stream_llm(CODEX_URL, "gpt-5.4",
                                                    [{"role": "user", "content": "go"}], ref=_codex_ref()))
        tc = [_data(c) for c in chunks if c.startswith("data: {") and _data(c).get("type") == "tool_calls"][0]
        assert tc["calls"][0]["name"] == "search" and tc["calls"][0]["id"] == "c9"


class TestOAuthCacheBypass:
    """The one engine guard: OAuth endpoints share url+model across users, so the
    shared response cache must never cross-serve (BLOCKER fixed additively in 2b)."""

    async def test_oauth_call_bypasses_cache(self, monkeypatch, codex_http):
        _patch_oauth_resolve(monkeypatch)
        ref = _codex_ref(endpoint_id="ep-alice", owner="alice")
        codex_http.route(
            CODEX_URL,
            httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": "FIRST"}]}]}),
            httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": "SECOND"}]}]}),
        )
        r1 = await llm_core.llm_call_async(CODEX_URL, "gpt-5.4", [{"role": "user", "content": "same"}], ref=ref)
        r2 = await llm_core.llm_call_async(CODEX_URL, "gpt-5.4", [{"role": "user", "content": "same"}], ref=ref)
        assert r1 == "FIRST" and r2 == "SECOND"   # not cross-served from cache
        assert codex_http.count(CODEX_URL) == 2
        assert len(llm_core._response_cache) == 0  # nothing cached for oauth

    async def test_non_oauth_call_still_cached(self, codex_http):
        # Control: api_key providers keep using the cache (guard is scoped to oauth).
        codex_http.route(OPENAI_URL, httpx.Response(
            200, json={"choices": [{"message": {"content": "CACHED"}}]}))
        r1 = await llm_core.llm_call_async(OPENAI_URL, "gpt-4o", [{"role": "user", "content": "same"}],
                                           headers={"Authorization": "Bearer k"})
        r2 = await llm_core.llm_call_async(OPENAI_URL, "gpt-4o", [{"role": "user", "content": "same"}],
                                           headers={"Authorization": "Bearer k"})
        assert r1 == r2 == "CACHED"
        assert codex_http.count(OPENAI_URL) == 1   # second served from cache


class TestSyncPathGuard:
    """Codex endpoints are async-only (credential resolution refreshes tokens) —
    the sync llm_call must refuse them loudly, before cache or egress, instead of
    POSTing an unauthenticated chat/completions payload upstream."""

    def test_sync_llm_call_rejects_codex_url(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            llm_core.llm_call(CODEX_URL, "gpt-5.4", [{"role": "user", "content": "hi"}])
        assert exc.value.status_code == 501


# ══════════════════════════════════════════════════════════════════════════════
# Fallback-candidate exclusion (multi-user safety; codex tuples can't refresh)
# ══════════════════════════════════════════════════════════════════════════════

class TestFallbackExclusion:
    def test_codex_excluded_from_fallback_candidates(self, monkeypatch):
        import importlib
        import sys
        # Other test files stub src.endpoint_resolver as a MagicMock at collection;
        # re-import the real module for this test, then restore the stub.
        snapshot = sys.modules.get("src.endpoint_resolver")
        sys.modules.pop("src.endpoint_resolver", None)
        try:
            er = importlib.import_module("src.endpoint_resolver")
            codex = (CODEX_URL, "gpt-5.4", {})
            openai = (OPENAI_URL, "gpt-4o", {"Authorization": "Bearer k"})

            monkeypatch.setattr(er, "resolve_endpoint_by_id",
                                lambda eid, model, owner=None: {"codex": codex, "oai": openai}.get(eid))
            import src.settings as settings_mod
            monkeypatch.setattr(settings_mod, "load_settings",
                                lambda: {"default_model_fallbacks": [{"endpoint_id": "codex", "model": "gpt-5.4"},
                                                                     {"endpoint_id": "oai", "model": "gpt-4o"}]})
            monkeypatch.setattr(settings_mod, "get_user_setting",
                                lambda key, owner, default: default)

            out = er._resolve_fallback_candidates("default_model_fallbacks")
            assert openai in out          # api_key endpoint kept
            assert codex not in out       # oauth endpoint excluded
        finally:
            if snapshot is not None:
                sys.modules["src.endpoint_resolver"] = snapshot
            else:
                sys.modules.pop("src.endpoint_resolver", None)
