import asyncio
import base64
import json
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='odysseus-codex-test-')}/app.db")

from src import llm_core
from src.endpoint_resolver import build_chat_url
from src.openai_codex import build_codex_payload, extract_account_id, CODEX_MODELS
from routes.model_routes import _probe_endpoint


def _jwt(payload):
    raw = json.dumps(payload).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"hdr.{encoded}.sig"


def test_extract_account_id_from_chatgpt_claim():
    token = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"}})
    assert extract_account_id(token) == "acct_123"


def test_codex_endpoint_url_and_static_models():
    assert build_chat_url("https://chatgpt.com/backend-api") == "https://chatgpt.com/backend-api/codex/responses"
    assert _probe_endpoint("https://chatgpt.com/backend-api", timeout=0.01) == CODEX_MODELS


def test_codex_payload_uses_responses_shape_and_cache_key():
    payload = build_codex_payload(
        "gpt-5.2-codex",
        [
            {"role": "system", "content": "System A"},
            {"role": "user", "content": "Hello"},
        ],
        temperature=0.2,
        max_tokens=123,
        tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
        session_id="sess-1",
    )
    assert payload["store"] is False
    assert payload["stream"] is True
    assert payload["instructions"] == "System A"
    assert payload["input"] == [{"role": "user", "content": "Hello"}]
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["prompt_cache_key"] == "sess-1"
    assert payload["tools"]


def test_codex_payload_converts_tool_round_trip_to_typed_items():
    # Mirrors what src/agent_loop.py:_append_tool_results builds: an assistant
    # turn carrying nested function tool_calls (content=None), followed by a
    # role:"tool" result. Both must become Responses typed items, not messages.
    payload = build_codex_payload(
        "gpt-5.2-codex",
        [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "fetch example.com"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "web_fetch", "arguments": '{"url":"https://example.com"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_abc", "content": "page text"},
            {"role": "user", "content": "summarize"},
        ],
        temperature=0.2,
        max_tokens=0,
    )
    items = payload["input"]

    # No item may carry a `role` without a `content` field — that is the exact
    # shape that triggers "Missing required parameter: input[N].content".
    for item in items:
        if "role" in item:
            assert "content" in item, f"role item without content: {item}"

    fc = next(i for i in items if i.get("type") == "function_call")
    fco = next(i for i in items if i.get("type") == "function_call_output")
    assert fc["name"] == "web_fetch"
    assert fc["arguments"] == '{"url":"https://example.com"}'
    assert fc["call_id"] == "call_abc"
    # function_call must be paired with a matching function_call_output.
    assert fco["call_id"] == fc["call_id"]
    assert fco["output"] == "page text"
    assert items.index(fc) < items.index(fco)
    # The trailing user turn survives as a normal message.
    assert {"role": "user", "content": "summarize"} in items


def test_codex_payload_reasoning_effort_levels():
    base = [{"role": "user", "content": "hi"}]
    # Explicit levels emit a reasoning block with summary:auto.
    for level in ("minimal", "low", "medium", "high"):
        p = build_codex_payload("gpt-5.2-codex", base, 0.2, 0, reasoning_effort=level)
        assert p["reasoning"] == {"effort": level, "summary": "auto"}, level
    # Case/whitespace tolerant.
    p = build_codex_payload("gpt-5.2-codex", base, 0.2, 0, reasoning_effort="  HIGH ")
    assert p["reasoning"]["effort"] == "high"


def test_codex_payload_reasoning_effort_off_and_default_omit_block():
    base = [{"role": "user", "content": "hi"}]
    # "off" and unset both omit the reasoning block (model uses its default).
    for val in ("off", "none", "", None, "bogus"):
        p = build_codex_payload("gpt-5.2-codex", base, 0.2, 0, reasoning_effort=val)
        assert "reasoning" not in p, val
    # No reasoning_effort arg at all → omitted.
    assert "reasoning" not in build_codex_payload("gpt-5.2-codex", base, 0.2, 0)


def test_codex_sse_emits_verbatim_output_items_and_cached_tokens(monkeypatch):
    reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "ENC"}
    fc_item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "search", "arguments": "{}"}
    lines = [
        "data: " + json.dumps({"type": "response.output_item.done", "output_index": 0, "item": reasoning_item}),
        "data: " + json.dumps({"type": "response.output_item.done", "output_index": 1, "item": fc_item}),
        "data: " + json.dumps({
            "type": "response.completed",
            "response": {"usage": {"input_tokens": 100, "output_tokens": 4, "input_tokens_details": {"cached_tokens": 80}}},
        }),
    ]
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeClient(lines))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def _headers(owner, session_id=None, websocket=False):
        return {"Authorization": "Bearer token"}

    import src.openai_codex as codex
    monkeypatch.setattr(codex, "resolve_codex_headers", _headers)

    async def run():
        events = []
        async for chunk in llm_core.stream_llm(
            "https://chatgpt.com/backend-api/codex/responses",
            "gpt-5.2-codex",
            [{"role": "user", "content": "hi"}],
            headers={"_odysseus_owner": "alice"},
        ):
            for line in chunk.splitlines():
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    events.append(json.loads(line[6:]))
        return events

    events = asyncio.run(run())
    oi = next(e for e in events if e.get("type") == "codex_output_items")
    assert oi["items"] == [reasoning_item, fc_item]
    usage = next(e for e in events if e.get("type") == "usage")
    assert usage["data"]["cached_tokens"] == 80


def test_codex_payload_replays_reasoning_items_verbatim():
    # An assistant tool-call turn whose verbatim output items (reasoning +
    # function_call) are supplied via replay_items must be re-sent verbatim,
    # preserving the rs_/fc_ ids, instead of the id-less reconstruction.
    reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "ENC", "summary": []}
    fc_item = {"type": "function_call", "id": "fc_1", "call_id": "call_abc", "name": "web_fetch", "arguments": "{}"}
    messages = [
        {"role": "user", "content": "fetch"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_abc", "type": "function", "function": {"name": "web_fetch", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_abc", "content": "result"},
    ]
    p = build_codex_payload(
        "gpt-5.2-codex", messages, 0.2, 0,
        replay_items={"call_abc": [reasoning_item, fc_item]},
    )
    items = p["input"]
    # Verbatim reasoning item (with encrypted_content) is replayed, before the call.
    assert reasoning_item in items
    fc = next(i for i in items if i.get("type") == "function_call")
    assert fc["id"] == "fc_1" and fc["call_id"] == "call_abc"
    assert items.index(reasoning_item) < items.index(fc)
    # Tool result still pairs by call_id.
    fco = next(i for i in items if i.get("type") == "function_call_output")
    assert fco["call_id"] == "call_abc"


def test_codex_payload_replays_parallel_calls_verbatim_when_all_survive():
    # Parallel tool calls in one turn: when BOTH results survive, the whole
    # block (shared reasoning + both function_calls) is replayed verbatim.
    reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "ENC"}
    fc_a = {"type": "function_call", "id": "fc_1", "call_id": "call_a", "name": "a", "arguments": "{}"}
    fc_b = {"type": "function_call", "id": "fc_2", "call_id": "call_b", "name": "b", "arguments": "{}"}
    messages = [
        {"role": "user", "content": "x"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_a", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                {"id": "call_b", "type": "function", "function": {"name": "b", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "content": "ra"},
        {"role": "tool", "tool_call_id": "call_b", "content": "rb"},
    ]
    p = build_codex_payload(
        "gpt-5.2-codex", messages, 0.2, 0,
        replay_items={"call_a": [reasoning_item, fc_a, fc_b]},
    )
    items = p["input"]
    assert reasoning_item in items
    call_ids = {i.get("call_id") for i in items if i.get("type") == "function_call"}
    assert call_ids == {"call_a", "call_b"}


def test_codex_payload_replay_all_or_nothing_falls_back_when_call_trimmed():
    # If ANY call in a (parallel) turn was trimmed, replaying a partial set would
    # leave the shared reasoning item half-matched (a pairing 400). The whole
    # turn must fall back to the verified id-less reconstruction: no verbatim
    # reasoning item, and only the surviving call is emitted (reconstructed).
    reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "ENC"}
    kept = {"type": "function_call", "id": "fc_1", "call_id": "call_keep", "name": "a", "arguments": "{}"}
    orphan = {"type": "function_call", "id": "fc_2", "call_id": "call_gone", "name": "b", "arguments": "{}"}
    messages = [
        {"role": "user", "content": "x"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_keep", "type": "function", "function": {"name": "a", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_keep", "content": "r"},
    ]
    p = build_codex_payload(
        "gpt-5.2-codex", messages, 0.2, 0,
        replay_items={"call_keep": [reasoning_item, kept, orphan]},
    )
    items = p["input"]
    # Fell back to reconstruction: no verbatim reasoning, no orphan.
    assert reasoning_item not in items
    call_ids = {i.get("call_id") for i in items if i.get("type") == "function_call"}
    assert call_ids == {"call_keep"}  # id-less reconstruction of the surviving call only
    # No reasoning item leaked through.
    assert not any(i.get("type") == "reasoning" for i in items)


def test_codex_replay_map_gated_by_setting(monkeypatch):
    import src.llm_core as L
    msgs = [{
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_x", "type": "function", "function": {"name": "a", "arguments": "{}"}}],
        "_codex_output_items": [{"type": "reasoning", "id": "rs_1"}],
    }]
    import src.settings as S
    monkeypatch.setattr(S, "get_setting", lambda k, d=None: False if k == "codex_reasoning_replay" else d)
    assert L._codex_replay_map(msgs) == {}
    monkeypatch.setattr(S, "get_setting", lambda k, d=None: True if k == "codex_reasoning_replay" else d)
    assert L._codex_replay_map(msgs) == {"call_x": [{"type": "reasoning", "id": "rs_1"}]}


def test_codex_stream_threads_reasoning_effort_header_into_payload(monkeypatch):
    """End-to-end: a per-session _odysseus_reasoning_effort header must survive
    _split_internal_headers → _codex_reasoning_effort → build_codex_payload and
    land in the wire payload. Certifies the full chain, not just the builder."""
    captured = {}

    lines = [
        "data: " + json.dumps({"type": "response.output_text.delta", "delta": "ok"}),
        "data: " + json.dumps({"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 1}}}),
    ]

    class _CapturingClient:
        def stream(self, method, url, **kwargs):
            captured["json"] = kwargs["json"]
            captured["headers"] = kwargs["headers"]
            return _FakeStreamCtx(lines)

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _CapturingClient())
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def _headers(owner, session_id=None, websocket=False):
        return {"Authorization": "Bearer token"}

    import src.openai_codex as codex
    monkeypatch.setattr(codex, "resolve_codex_headers", _headers)

    async def run():
        async for _ in llm_core.stream_llm(
            "https://chatgpt.com/backend-api/codex/responses",
            "gpt-5.2-codex",
            [{"role": "user", "content": "hi"}],
            headers={"_odysseus_owner": "alice", "_odysseus_reasoning_effort": "high"},
        ):
            pass

    asyncio.run(run())
    # The header value reached the wire payload as a reasoning block.
    assert captured["json"]["reasoning"] == {"effort": "high", "summary": "auto"}
    # Internal routing metadata must NOT be forwarded upstream.
    assert "_odysseus_reasoning_effort" not in captured["headers"]
    assert "_odysseus_owner" not in captured["headers"]


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeResp(self._lines)

    async def __aexit__(self, *args):
        return False


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, **kwargs):
        assert url == "https://chatgpt.com/backend-api/codex/responses"
        assert kwargs["json"]["store"] is False
        assert kwargs["headers"]["Authorization"] == "Bearer token"
        assert "_odysseus_owner" not in kwargs["headers"]
        return _FakeStreamCtx(self._lines)


def test_codex_sse_stream_maps_text_reasoning_usage_and_tool_calls(monkeypatch):
    lines = [
        "data: " + json.dumps({"type": "response.reasoning_summary_text.delta", "delta": "thinking"}),
        "data: " + json.dumps({"type": "response.output_text.delta", "delta": "answer"}),
        "data: " + json.dumps({
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "call_id": "call_1", "name": "search", "arguments": ""},
        }),
        "data: " + json.dumps({"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"q":"cats"}'}),
        "data: " + json.dumps({
            "type": "response.completed",
            "response": {"usage": {"input_tokens": 3, "output_tokens": 4}},
        }),
    ]
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeClient(lines))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def _headers(owner, session_id=None, websocket=False):
        assert owner == "alice"
        assert websocket is False
        return {"Authorization": "Bearer token"}

    import src.openai_codex as codex
    monkeypatch.setattr(codex, "resolve_codex_headers", _headers)

    async def run():
        events = []
        async for chunk in llm_core.stream_llm(
            "https://chatgpt.com/backend-api/codex/responses",
            "gpt-5.2-codex",
            [{"role": "user", "content": "hi"}],
            headers={"_odysseus_owner": "alice"},
        ):
            for line in chunk.splitlines():
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    events.append(json.loads(line[6:]))
        return events

    events = asyncio.run(run())
    assert {"delta": "thinking", "thinking": True} in events
    assert {"delta": "answer"} in events
    assert {"type": "tool_calls", "calls": [{"id": "call_1", "name": "search", "arguments": '{"q":"cats"}'}]} in events
    assert {"type": "usage", "data": {"input_tokens": 3, "output_tokens": 4}} in events


class _FakeAuthResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


class _FakeAuthClient:
    """Async client stub that returns queued responses and records requests."""

    def __init__(self, responses, calls):
        self._responses = responses
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        self._calls.append({"url": url, **kwargs})
        return self._responses.pop(0)


def _patch_codex_db(monkeypatch, codex, row):
    """Stub out the DB-touching helpers so the flow runs without a real database."""
    class _FakeSession:
        def add(self, *a, **k):
            pass

        def commit(self):
            pass

        def close(self):
            pass

        def query(self, *a, **k):
            session = self

            class _Q:
                def filter(self_inner, *a, **k):
                    return self_inner

                def first(self_inner):
                    return row

            return _Q()

    monkeypatch.setattr(codex, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(codex, "get_credential", lambda db, owner: None)

    class _FakeCred:
        pass

    monkeypatch.setattr(codex, "ProviderOAuthCredential", lambda **k: _FakeCred())

    class _FakeEndpoint:
        id = "ep_1"

    monkeypatch.setattr(codex, "ensure_codex_endpoint", lambda db, owner: _FakeEndpoint())


def test_start_device_login_uses_usercode_endpoint(monkeypatch):
    import src.openai_codex as codex

    calls = []
    responses = [
        _FakeAuthResp(200, {
            "device_auth_id": "dev_abc",
            "user_code": "3P35-HTTFS",
            "interval": "5",
            "expires_at": "2026-01-01T00:00:00Z",
        })
    ]
    monkeypatch.setattr(codex, "SessionLocal", lambda: type("S", (), {
        "add": lambda self, *a, **k: None,
        "commit": lambda self: None,
        "close": lambda self: None,
    })())
    monkeypatch.setattr(codex.httpx, "AsyncClient", lambda *a, **k: _FakeAuthClient(responses, calls))

    result = asyncio.run(codex.start_device_login("alice"))

    assert calls[0]["url"] == codex.DEVICE_USERCODE_URL
    assert calls[0]["json"] == {"client_id": codex.CLIENT_ID}
    assert calls[0]["headers"]["User-Agent"] == codex.CODEX_USER_AGENT
    assert calls[0]["headers"]["originator"] == codex.CODEX_ORIGINATOR
    assert result["verification_uri"] == codex.DEVICE_VERIFICATION_URI
    assert result["verification_uri_complete"] == codex.DEVICE_VERIFICATION_URI
    assert result["user_code"] == "3P35-HTTFS"
    assert result["interval_seconds"] == 5
    assert result["status"] == "pending"


def test_poll_device_login_pending_on_403(monkeypatch):
    import src.openai_codex as codex

    row = type("Row", (), {
        "expires_at": codex._now() + codex.timedelta(seconds=300),
        "device_auth_id": "dev_abc",
        "user_code": "3P35-HTTFS",
        "interval_seconds": 5,
        "status": "pending",
        "error": None,
    })()
    _patch_codex_db(monkeypatch, codex, row)

    calls = []
    responses = [_FakeAuthResp(403, text="forbidden")]
    monkeypatch.setattr(codex.httpx, "AsyncClient", lambda *a, **k: _FakeAuthClient(responses, calls))

    result = asyncio.run(codex.poll_device_login("alice", "login_1"))

    assert result == {"status": "pending", "interval_seconds": 5}
    assert calls[0]["url"] == codex.DEVICE_TOKEN_POLL_URL
    assert calls[0]["json"] == {"device_auth_id": "dev_abc", "user_code": "3P35-HTTFS"}


def test_poll_device_login_success_runs_inline_exchange(monkeypatch):
    import src.openai_codex as codex

    row = type("Row", (), {
        "expires_at": codex._now() + codex.timedelta(seconds=300),
        "device_auth_id": "dev_abc",
        "user_code": "3P35-HTTFS",
        "interval_seconds": 5,
        "status": "pending",
        "error": None,
    })()
    _patch_codex_db(monkeypatch, codex, row)

    access = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_xyz"}})
    calls = []
    responses = [
        _FakeAuthResp(200, {
            "authorization_code": "authcode_1",
            "code_challenge": "chal",
            "code_verifier": "verif_1",
        }),
        _FakeAuthResp(200, {
            "id_token": "idtok",
            "access_token": access,
            "refresh_token": "refresh_1",
        }),
    ]
    monkeypatch.setattr(codex.httpx, "AsyncClient", lambda *a, **k: _FakeAuthClient(responses, calls))

    result = asyncio.run(codex.poll_device_login("alice", "login_1"))

    # Step 2 hit the device token poll endpoint.
    assert calls[0]["url"] == codex.DEVICE_TOKEN_POLL_URL
    # Step 3 hit the oauth/token exchange endpoint with form data.
    assert calls[1]["url"] == codex.TOKEN_URL
    assert calls[1]["data"]["grant_type"] == "authorization_code"
    assert calls[1]["data"]["code"] == "authcode_1"
    assert calls[1]["data"]["code_verifier"] == "verif_1"
    assert calls[1]["data"]["redirect_uri"] == codex.DEVICE_REDIRECT_URI
    assert calls[1]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert calls[1]["headers"]["User-Agent"] == codex.CODEX_USER_AGENT
    assert calls[1]["headers"]["originator"] == codex.CODEX_ORIGINATOR

    assert result["status"] == "connected"
    assert result["account_id"] == "acct_xyz"
    assert result["endpoint_id"] == "ep_1"
    assert result["models"] == list(codex.CODEX_MODELS)
