"""Optional thinking controls stay local and cannot reuse another mode's cache."""

import json

import httpx
import pytest

from src import llm_core, model_context


@pytest.fixture
async def upstream(monkeypatch):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": f"Answer {len(requests)}"}, "finish_reason": "stop"}],
            "message": {"content": f"Answer {len(requests)}"}, "done_reason": "stop",
        })

    monkeypatch.setattr(llm_core, "_response_cache", {})
    monkeypatch.setattr(llm_core, "_response_model_cache", {})
    monkeypatch.setattr(llm_core, "get_context_length", lambda *args: llm_core.DEFAULT_CONTEXT)
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda url: "local" if "//llama:" in url else None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        yield requests


async def _call(url="http://llama:8081/v1", model="Qwen3.5-9B", **kwargs):
    return await llm_core.llm_call_async(
        url, model, [{"role": "user", "content": "Return a brief summary."}],
        max_tokens=128, max_retries=1, **kwargs,
    )


async def test_local_thinking_modes_have_distinct_caches_and_preserve_default(upstream):
    assert await _call() == "Answer 1"
    assert await _call(enable_thinking=False) == "Answer 2"
    assert await _call(enable_thinking=True) == "Answer 3"
    assert await _call(enable_thinking=None) == "Answer 1"
    assert await _call(enable_thinking=False) == "Answer 2"
    assert await _call(enable_thinking=True) == "Answer 3"
    assert len(upstream) == 3
    assert "chat_template_kwargs" not in upstream[0]
    assert "think" not in upstream[0]
    assert upstream[1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert upstream[2]["chat_template_kwargs"] == {"enable_thinking": True}
    assert "think" not in upstream[1]


async def test_localhost_llama_override_works_despite_ollama_heuristic(upstream):
    assert llm_core._is_ollama_openai_compat_url("http://localhost:8081/v1")

    await _call(url="http://localhost:8081/v1", enable_thinking=False)

    assert upstream[0]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "https://unknown-cloud.test/v1"])
async def test_remote_overrides_are_ignored_in_payload_and_cache(upstream, url):
    assert await _call(url=url) == "Answer 1"
    assert await _call(url=url, enable_thinking=False) == "Answer 1"
    assert await _call(url=url, enable_thinking=True) == "Answer 1"
    assert len(upstream) == 1
    assert "chat_template_kwargs" not in upstream[0]
    assert "think" not in upstream[0]


async def test_remote_mistral_keeps_its_existing_reasoning_payload(upstream):
    await _call(url="https://api.mistral.ai/v1", model="magistral-small", enable_thinking=False)

    assert upstream[0]["reasoning_effort"] == llm_core._MISTRAL_REASONING_EFFORT
    assert "chat_template_kwargs" not in upstream[0]
    assert "think" not in upstream[0]


async def test_remote_native_ollama_keeps_default_payload_and_cache(upstream):
    assert await _call(url="https://ollama.com/api/chat") == "Answer 1"
    assert await _call(url="https://ollama.com/api/chat", enable_thinking=False) == "Answer 1"
    assert await _call(url="https://ollama.com/api/chat", enable_thinking=True) == "Answer 1"
    assert len(upstream) == 1
    assert "chat_template_kwargs" not in upstream[0]
    assert "think" not in upstream[0]


async def test_unknown_local_model_ignores_override_and_reuses_default_cache(upstream):
    assert await _call(model="ordinary-model") == "Answer 1"
    assert await _call(model="ordinary-model", enable_thinking=False) == "Answer 1"
    assert len(upstream) == 1
    assert "chat_template_kwargs" not in upstream[0]
    assert "think" not in upstream[0]


async def test_native_ollama_only_sends_explicit_thinking_control(upstream):
    assert await _call(url="http://localhost:11434") == "Answer 1"
    assert await _call(url="http://localhost:11434", enable_thinking=False) == "Answer 2"
    assert await _call(url="http://localhost:11434", enable_thinking=True) == "Answer 3"
    assert "think" not in upstream[0]
    assert upstream[1]["think"] is False
    assert upstream[2]["think"] is True
    assert all("chat_template_kwargs" not in payload for payload in upstream)


async def test_ollama_v1_keeps_default_and_honors_explicit_true(upstream):
    await _call(url="http://localhost:11434/v1")
    await _call(url="http://localhost:11434/v1", enable_thinking=True)

    assert upstream[0]["think"] is False
    assert "chat_template_kwargs" not in upstream[0]
    assert upstream[1]["think"] is True


async def test_configured_proxy_does_not_receive_local_template_control(upstream, monkeypatch):
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda _: "proxy")

    assert await _call(url="http://192.168.1.2:8081/v1") == "Answer 1"
    assert await _call(url="http://192.168.1.2:8081/v1", enable_thinking=False) == "Answer 1"
    assert len(upstream) == 1
    assert "chat_template_kwargs" not in upstream[0]
