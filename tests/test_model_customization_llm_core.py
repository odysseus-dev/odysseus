"""Model Customization at call time inside src.llm_core (seam b).

Each test drives llm_call / llm_call_async / _stream_llm_inner with the network
replaced by a capture, then asserts on the request body the provider would
receive. One test per Gherkin scenario in
openspec/changes/add-model-customization/specs/model-customization/spec.md
that the payload builders own.
"""

import asyncio
import json

import httpx
import pytest

from core import database
from src import llm_core
from src import model_customization as mc
from tests.helpers.sqlite_db import make_temp_sqlite

LAB = "http://127.0.0.1:11434"                       # ollama-native
LAB_CHAT = LAB + "/api/chat"
LAB_V1_CHAT = LAB + "/v1/chat/completions"           # ollama-openai
DESK = "http://192.168.1.20:8080/v1"                 # local-openai (localhost /v1 counts as Ollama /v1)
DESK_CHAT = DESK + "/chat/completions"
GROQ = "https://api.groq.com/openai/v1"              # hosted-openai
GROQ_CHAT = GROQ + "/chat/completions"
CLAUDE = "https://api.anthropic.com"                 # excluded
CLAUDE_CHAT = CLAUDE + "/v1/messages"
MOONSHOT = "https://api.moonshot.ai/v1"
MOONSHOT_CHAT = MOONSHOT + "/chat/completions"

MESSAGES = [{"role": "user", "content": "Say OK"}]


# ── Fixtures: temp store + temp endpoints + no real context discovery ────────

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "STORE_PATH", str(tmp_path / "model_customizations.json"))
    mc._reset_cache()
    factory, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(mc, "_session_factory", lambda: factory)
    mc._reset_endpoint_cache()
    monkeypatch.setattr(llm_core, "get_context_length", lambda url, model: llm_core.DEFAULT_CONTEXT)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)
    llm_core._response_cache.clear()
    yield factory
    llm_core._response_cache.clear()
    engine.dispose()
    tmpfile.close()


@pytest.fixture
def customize(isolated):
    """Register an endpoint serving ``model`` and save ``settings`` for it."""
    def add(endpoint_id, base_url, model, settings):
        with isolated() as db:
            if not db.get(database.ModelEndpoint, endpoint_id):
                db.add(database.ModelEndpoint(
                    id=endpoint_id, name=endpoint_id, base_url=base_url,
                    cached_models=json.dumps([model]),
                ))
            else:
                ep = db.get(database.ModelEndpoint, endpoint_id)
                ep.cached_models = json.dumps(json.loads(ep.cached_models) + [model])
            db.commit()
        mc._reset_endpoint_cache()
        mc.save_entry(endpoint_id, base_url, model, settings)
    return add


# ── Captures ────────────────────────────────────────────────────────────────

_OK_BODY = {"choices": [{"message": {"content": "OK"}}], "message": {"content": "OK"},
            "content": [{"type": "text", "text": "OK"}]}


def capture_sync(monkeypatch, url, model, **kwargs):
    """Run a synchronous llm_call and return every posted JSON body."""
    llm_core._response_cache.clear()
    seen = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append(json)
        return httpx.Response(200, request=httpx.Request("POST", url), json=_OK_BODY)

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)
    llm_core.llm_call(url, model, MESSAGES, max_tokens=5, **kwargs)
    return seen


class _AsyncResponse:
    is_success = True
    status_code = 200
    text = ""

    def json(self):
        return _OK_BODY


def capture_async(monkeypatch, url, model, **kwargs):
    """Run llm_call_async and return the posted JSON body."""
    llm_core._response_cache.clear()
    seen = []

    async def fake_post(client, url, headers, **kw):
        seen.append(kw["json"])
        return _AsyncResponse()

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    asyncio.run(llm_core.llm_call_async(url, model, MESSAGES, max_tokens=5, **kwargs))
    return seen[0]


class _StreamResponse:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _StreamContext:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _StreamResponse(self._lines)

    async def __aexit__(self, *args):
        return False


class _StreamClient:
    def __init__(self, seen, lines):
        self._seen = seen
        self._lines = lines

    def stream(self, method, url, **kwargs):
        self._seen.append(json.loads(json.dumps(kwargs["json"])))
        return _StreamContext(self._lines)


_OPENAI_LINES = ['data: {"choices":[{"delta":{"content":"OK"}}]}', "data: [DONE]"]
_OLLAMA_LINES = ['{"message":{"content":"OK"},"done":true}']


def capture_stream(monkeypatch, url, model, lines=None, **kwargs):
    """Run _stream_llm_inner to completion and return every streamed JSON body."""
    seen = []
    if lines is None:
        lines = _OLLAMA_LINES if url.endswith("/api/chat") else _OPENAI_LINES
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _StreamClient(seen, lines))

    async def run():
        return [c async for c in llm_core._stream_llm_inner(url, model, MESSAGES, max_tokens=5, **kwargs)]

    asyncio.run(run())
    return seen


def _temperature(payload):
    return payload["options"]["temperature"] if "options" in payload else payload.get("temperature")


# ── Group 8: temperature ────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [LAB_CHAT, LAB_V1_CHAT, DESK_CHAT, GROQ_CHAT, CLAUDE_CHAT])
def test_without_a_customization_the_built_in_default_is_unchanged(monkeypatch, url):
    """Characterization guard: today's default temperature is 1.0 on every format."""
    assert _temperature(capture_sync(monkeypatch, url, "some-model")[0]) == 1.0
    assert _temperature(capture_async(monkeypatch, url, "some-model")) == 1.0
    assert _temperature(capture_stream(monkeypatch, url, "some-model")[0]) == 1.0


@pytest.mark.parametrize("capture", [capture_sync, capture_async, capture_stream])
def test_a_helper_that_does_not_choose_a_temperature_uses_the_model_setting(monkeypatch, customize, capture):
    customize("lab-ollama", LAB, "qwen3:8b", {"temperature": 0.6})
    payload = capture(monkeypatch, LAB_CHAT, "qwen3:8b")
    payload = payload[0] if isinstance(payload, list) else payload
    assert payload["options"]["temperature"] == 0.6


def test_a_helper_on_an_openai_compatible_server_uses_the_model_setting(monkeypatch, customize):
    customize("desk-llama", DESK, "gemma-3-12b", {"temperature": 0.9})
    assert capture_sync(monkeypatch, DESK_CHAT, "gemma-3-12b")[0]["temperature"] == 0.9


def test_an_internal_task_that_chooses_its_own_temperature_keeps_it(monkeypatch, customize):
    customize("lab-ollama", LAB, "qwen3:8b", {"temperature": 0.6})
    assert capture_sync(monkeypatch, LAB_CHAT, "qwen3:8b", temperature=0)[0]["options"]["temperature"] == 0
    assert capture_async(monkeypatch, LAB_CHAT, "qwen3:8b", temperature=0)["options"]["temperature"] == 0


def test_anthropic_calls_without_a_chosen_temperature_keep_the_built_in_default(monkeypatch, customize):
    customize("anthropic-direct", CLAUDE, "claude-sonnet-4", {"temperature": 0.6})
    assert capture_sync(monkeypatch, CLAUDE_CHAT, "claude-sonnet-4")[0]["temperature"] == 1.0
    assert capture_stream(monkeypatch, CLAUDE_CHAT, "claude-sonnet-4")[0]["temperature"] == 1.0


def test_temperature_stays_omitted_for_models_that_reject_it(monkeypatch, customize):
    customize("moonshot", MOONSHOT, "kimi-k2.6", {"temperature": 0.6, "top_p": 0.9})
    payload = capture_sync(monkeypatch, MOONSHOT_CHAT, "kimi-k2.6")[0]
    assert "temperature" not in payload


# ── Group 9: sampling injection ─────────────────────────────────────────────

LAB_SAMPLING = {"temperature": 0.6, "top_p": 0.9, "top_k": 40, "min_p": 0.05,
                "presence_penalty": 0.5, "repeat_penalty": 1.1}


def _first(payload):
    return payload[0] if isinstance(payload, list) else payload


@pytest.mark.parametrize("capture", [capture_sync, capture_async, capture_stream])
def test_ollama_native_requests_carry_the_settings_inside_options(monkeypatch, customize, capture):
    customize("lab-ollama", LAB, "qwen3:8b", LAB_SAMPLING)
    payload = _first(capture(monkeypatch, LAB_CHAT, "qwen3:8b"))
    assert {k: payload["options"][k] for k in LAB_SAMPLING} == LAB_SAMPLING
    assert not {"top_p", "top_k", "min_p", "presence_penalty", "repeat_penalty"} & set(payload)


@pytest.mark.parametrize("capture", [capture_sync, capture_async, capture_stream])
def test_local_openai_compatible_servers_get_both_repeat_penalty_spellings(monkeypatch, customize, capture):
    customize("desk-llama", DESK, "gemma-3-12b", LAB_SAMPLING)
    payload = _first(capture(monkeypatch, DESK_CHAT, "gemma-3-12b"))
    assert payload["temperature"] == 0.6
    assert payload["top_p"] == 0.9 and payload["top_k"] == 40 and payload["min_p"] == 0.05
    assert payload["presence_penalty"] == 0.5
    assert payload["repeat_penalty"] == 1.1 and payload["repetition_penalty"] == 1.1


@pytest.mark.parametrize("capture", [capture_sync, capture_async, capture_stream])
def test_hosted_requests_never_carry_unsupported_settings(monkeypatch, customize, capture):
    # Written straight to the store: the admin route would refuse top_k here.
    customize("groq-cloud", GROQ, "llama-3.3-70b", LAB_SAMPLING)
    payload = _first(capture(monkeypatch, GROQ_CHAT, "llama-3.3-70b"))
    assert payload["top_p"] == 0.9 and payload["presence_penalty"] == 0.5
    assert not {"top_k", "min_p", "repeat_penalty", "repetition_penalty", "options"} & set(payload)


def test_ollama_openai_requests_carry_only_their_supported_settings(monkeypatch, customize):
    customize("lab-ollama-v1", LAB + "/v1", "llama3:8b", LAB_SAMPLING)
    payload = capture_sync(monkeypatch, LAB_V1_CHAT, "llama3:8b")[0]
    assert payload["temperature"] == 0.6 and payload["top_p"] == 0.9 and payload["presence_penalty"] == 0.5
    assert not {"top_k", "min_p", "repeat_penalty", "repetition_penalty"} & set(payload)


def test_a_forced_deterministic_local_model_stays_deterministic(monkeypatch, customize):
    model = "qwen3.6-27b-mlx"
    customize("desk-llama", DESK, model, {"temperature": 0.9, "top_p": 0.5})
    payload = capture_sync(monkeypatch, DESK_CHAT, model)[0]
    assert payload["temperature"] == 0.0
    assert payload["top_p"] == 1.0


def test_a_fill_in_default_does_not_replace_the_admin_value(monkeypatch, customize):
    model = "minimax-m2-mlx"
    customize("desk-llama", DESK, model, {"repeat_penalty": 1.3, "top_k": 50})
    payload = capture_sync(monkeypatch, DESK_CHAT, model)[0]
    assert payload["repetition_penalty"] == 1.3
    assert payload["top_k"] == 50


def test_a_retried_streaming_request_is_unchanged_by_re_application(monkeypatch, customize):
    """The silent-local retry re-enters _stream_llm_inner with the already
    resolved temperature; the rebuilt request must equal the first one."""
    customize("desk-llama", DESK, "gemma-3-12b", LAB_SAMPLING)
    first = capture_stream(monkeypatch, DESK_CHAT, "gemma-3-12b")[0]
    retried = capture_stream(monkeypatch, DESK_CHAT, "gemma-3-12b", temperature=first["temperature"])[0]
    assert retried == first


@pytest.mark.parametrize("capture", [capture_sync, capture_async, capture_stream])
def test_anthropic_requests_are_unchanged(monkeypatch, customize, capture):
    before = json.dumps(_first(capture(monkeypatch, CLAUDE_CHAT, "claude-sonnet-4")), sort_keys=True)
    customize("anthropic-direct", CLAUDE, "claude-sonnet-4", LAB_SAMPLING)
    customize("lab-ollama", LAB, "qwen3:8b", LAB_SAMPLING)
    after = json.dumps(_first(capture(monkeypatch, CLAUDE_CHAT, "claude-sonnet-4")), sort_keys=True)
    assert after == before


@pytest.mark.parametrize("url, base", [(LAB_CHAT, LAB), (LAB_V1_CHAT, LAB + "/v1"), (DESK_CHAT, DESK), (GROQ_CHAT, GROQ)])
@pytest.mark.parametrize("capture", [capture_sync, capture_async, capture_stream])
def test_unset_settings_keep_todays_behaviour(monkeypatch, customize, capture, url, base):
    before = _first(capture(monkeypatch, url, "some-model"))
    customize("endpoint", base, "other-model", LAB_SAMPLING)
    customize("endpoint", base, "some-model", {"temperature": 1.0})
    mc.save_entry("endpoint", base, "some-model", {})
    after = _first(capture(monkeypatch, url, "some-model"))
    assert after == before


@pytest.mark.parametrize("capture", [capture_sync, capture_async, capture_stream])
def test_a_corrupt_file_does_not_break_chats(monkeypatch, customize, capture):
    before = _first(capture(monkeypatch, LAB_CHAT, "qwen3:8b"))
    customize("lab-ollama", LAB, "qwen3:8b", LAB_SAMPLING)
    with open(mc.STORE_PATH, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    mc._reset_cache()
    after = _first(capture(monkeypatch, LAB_CHAT, "qwen3:8b"))
    assert after == before


def test_a_fallback_model_uses_its_own_settings(monkeypatch, customize):
    from fastapi import HTTPException

    customize("lab-ollama", LAB, "qwen3:8b", {"temperature": 0.6})
    customize("desk-llama", DESK, "gemma-3-12b", {"temperature": 0.9})
    seen = {}

    async def fake_post(client, url, headers, **kw):
        seen[url] = kw["json"]
        if url.startswith(LAB):
            raise HTTPException(503, "primary down")
        return _AsyncResponse()

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    asyncio.run(llm_core.llm_call_async_with_route_fallback(
        [(LAB_CHAT, "qwen3:8b", {}), (DESK_CHAT, "gemma-3-12b", {})],
        MESSAGES, fallback_statuses={503}, max_retries=1, max_tokens=5,
    ))
    assert seen[DESK_CHAT]["temperature"] == 0.9


def test_background_tasks_receive_sampling_settings(monkeypatch, customize):
    customize("desk-llama", DESK, "gemma-3-12b", {"temperature": 0.6, "top_p": 0.8})
    payload = capture_async(monkeypatch, DESK_CHAT, "gemma-3-12b", temperature=0)
    assert payload["temperature"] == 0
    assert payload["top_p"] == 0.8


# ── Group 10: cache key ─────────────────────────────────────────────────────

def test_uncustomized_requests_keep_their_existing_cache_keys():
    """Characterization guard: digests recorded before the sampling kwarg was
    added. Red is impossible here by construction; the test only fails if an
    uncustomized key ever changes."""
    assert llm_core._get_cache_key(LAB_CHAT, "qwen3:8b", MESSAGES, 1.0, 5) == (
        "e2d3be0e03364618971bf770fdb23e70b3e77c3ddfeec1f1b637bb5e0dbec269")
    assert llm_core._get_cache_key(
        GROQ_CHAT, "llama-3.3-70b", MESSAGES, 0.6, 5,
        headers={"Authorization": "Bearer x"}, thinking_mode="on",
    ) == "5fd00d9a0bb5d175a1bb27745d12afaf995e11e3985db85c3b9415040f7283ef"
    assert llm_core._get_cache_key(LAB_CHAT, "qwen3:8b", MESSAGES, 1.0, 5, sampling={}) == (
        "e2d3be0e03364618971bf770fdb23e70b3e77c3ddfeec1f1b637bb5e0dbec269")


def test_effective_settings_change_the_cache_key():
    plain = llm_core._get_cache_key(LAB_CHAT, "qwen3:8b", MESSAGES, 1.0, 5)
    tuned = llm_core._get_cache_key(LAB_CHAT, "qwen3:8b", MESSAGES, 1.0, 5, sampling={"top_k": 40})
    assert tuned != plain


@pytest.mark.parametrize("capture", [capture_sync, capture_async])
def test_changing_a_setting_does_not_serve_a_stale_cached_response(monkeypatch, customize, capture):
    customize("lab-ollama", LAB, "qwen3:8b", {"top_k": 40})
    llm_core._response_cache.clear()
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return httpx.Response(200, request=httpx.Request("POST", url), json=_OK_BODY)

    async def fake_post_async(client, url, headers, **kw):
        calls.append(kw["json"])
        return _AsyncResponse()

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)
    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post_async)

    def call():
        if capture is capture_sync:
            llm_core.llm_call(LAB_CHAT, "qwen3:8b", MESSAGES, max_tokens=5)
        else:
            asyncio.run(llm_core.llm_call_async(LAB_CHAT, "qwen3:8b", MESSAGES, max_tokens=5))

    call()
    mc.save_entry("lab-ollama", LAB, "qwen3:8b", {"top_k": 20})
    call()
    assert [c["options"]["top_k"] for c in calls] == [40, 20]


# ── Group 11: chat temperature defaults through the fallback wrappers ────────

def _stream_through_fallback(monkeypatch, url, model, temperature):
    seen = []
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _StreamClient(seen, _OLLAMA_LINES))

    async def run():
        return [c async for c in llm_core.stream_llm_with_fallback(
            [(url, model, {})], MESSAGES, temperature=temperature, max_tokens=5,
        )]

    asyncio.run(run())
    return seen[0]


def _call_through_route_fallback(monkeypatch, url, model, temperature):
    seen = []

    async def fake_post(client, url, headers, **kw):
        seen.append(kw["json"])
        return _AsyncResponse()

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    llm_core._response_cache.clear()
    asyncio.run(llm_core.llm_call_async_with_route_fallback(
        [(url, model, {})], MESSAGES, fallback_statuses={503}, temperature=temperature, max_tokens=5,
    ))
    return seen[0]


def test_a_new_chat_uses_the_per_model_temperature(monkeypatch, customize):
    customize("lab-ollama", LAB, "qwen3:8b", {"temperature": 0.6})
    assert _stream_through_fallback(monkeypatch, LAB_CHAT, "qwen3:8b", None)["options"]["temperature"] == 0.6


def test_an_existing_chat_without_a_temperature_override_follows_the_model_setting(monkeypatch, customize):
    customize("lab-ollama", LAB, "qwen3:8b", {"temperature": 0.6})
    assert _call_through_route_fallback(monkeypatch, LAB_CHAT, "qwen3:8b", None)["options"]["temperature"] == 0.6


def test_a_chat_temperature_override_still_wins(monkeypatch, customize):
    customize("lab-ollama", LAB, "qwen3:8b", {"temperature": 0.6})
    assert _stream_through_fallback(monkeypatch, LAB_CHAT, "qwen3:8b", 0.3)["options"]["temperature"] == 0.3
    assert _call_through_route_fallback(monkeypatch, LAB_CHAT, "qwen3:8b", 0.3)["options"]["temperature"] == 0.3


# ── Group 12 follow-up: num_ctx reaches the Ollama payload ──────────────────

@pytest.mark.parametrize("capture", [capture_sync, capture_async, capture_stream])
def test_ollama_payload_carries_the_configured_context_window(monkeypatch, customize, capture):
    from src import model_context
    monkeypatch.setattr(llm_core, "get_context_length", model_context.get_context_length)
    monkeypatch.setattr(model_context, "_query_context_length", lambda url, model: (40960, True))
    monkeypatch.setattr(model_context, "_context_cache", {})
    customize("lab-ollama", LAB, "qwen3:8b", {"num_ctx": 16384})
    body = capture(monkeypatch, LAB_CHAT, "qwen3:8b")
    body = body[0] if isinstance(body, list) else body
    assert body["options"]["num_ctx"] == 16384


# ── Group 13 follow-up: background calls do not start thinking ──────────────

def test_background_calls_do_not_start_thinking(monkeypatch, customize):
    plain = capture_async(monkeypatch, GROQ_CHAT, "qwen3-32b")
    customize("groq", GROQ, "qwen3-32b", {"thinking": "on"})
    tuned = capture_async(monkeypatch, GROQ_CHAT, "qwen3-32b")
    assert tuned == plain
