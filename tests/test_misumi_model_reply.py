import asyncio
import logging

import httpx

from routes import misumi_routes
from src import endpoint_resolver, llm_core, seed_order_context


FALLBACK = "Odysseus is available, but no working model backend is configured for this request."


def _configure_model(monkeypatch, llm_call):
    monkeypatch.setattr(
        endpoint_resolver,
        "resolve_endpoint",
        lambda *args, **kwargs: (
            "http://localhost:11434/v1/chat/completions",
            "qwen3:8b",
            {},
        ),
    )
    monkeypatch.setattr(llm_core, "llm_call_async", llm_call)
    monkeypatch.setattr(seed_order_context, "build_seed_order_context", lambda: "")


def test_model_reply_returns_normal_content_unchanged(monkeypatch):
    async def llm_call(url, model, messages, **kwargs):
        assert kwargs["max_tokens"] == 480
        assert kwargs["allow_reasoning_fallback"] is False
        return "A concise answer."

    _configure_model(monkeypatch, llm_call)

    result = asyncio.run(misumi_routes._model_reply("hello", "aoteru"))

    assert result == (
        "A concise answer.",
        "http://localhost:11434/v1/chat/completions",
        "qwen3:8b",
    )


def test_model_reply_logs_reasoning_only_response_and_falls_back(monkeypatch, caplog):
    async def llm_call(url, model, messages, **kwargs):
        # llm_call_async intentionally withholds the separate reasoning field.
        return ""

    _configure_model(monkeypatch, llm_call)

    with caplog.at_level(logging.ERROR, logger=misumi_routes.__name__):
        result = asyncio.run(misumi_routes._model_reply("hello", "aoteru"))

    assert result == (FALLBACK, None, None)
    assert "model returned empty content (reasoning-only)" in caplog.text


def test_model_reply_logs_backend_exception_and_falls_back(monkeypatch, caplog):
    async def llm_call(url, model, messages, **kwargs):
        raise RuntimeError("upstream exploded")

    _configure_model(monkeypatch, llm_call)

    with caplog.at_level(logging.ERROR, logger=misumi_routes.__name__):
        result = asyncio.run(misumi_routes._model_reply("hello", "aoteru"))

    assert result == (FALLBACK, None, None)
    assert "upstream exploded" in caplog.text


def test_llm_call_async_withholds_reasoning_fallback(monkeypatch):
    class FakeAsyncClient:
        async def post(self, *args, **kwargs):
            request = httpx.Request("POST", "http://misumi-test/v1/chat/completions")
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{
                        "message": {
                            "content": "",
                            "reasoning": "private reasoning",
                            "reasoning_content": "private reasoning",
                        },
                    }],
                },
            )

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: FakeAsyncClient())

    result = asyncio.run(llm_core.llm_call_async(
        "http://misumi-test/v1/chat/completions",
        "qwen3:8b",
        [{"role": "user", "content": "hello"}],
        allow_reasoning_fallback=False,
    ))

    assert result == ""


def test_native_ollama_disables_thinking_when_reasoning_fallback_is_disabled(monkeypatch):
    seen = {}

    class FakeAsyncClient:
        async def post(self, url, **kwargs):
            seen["url"] = url
            seen["payload"] = kwargs["json"]
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={"message": {"content": "A concise answer."}, "done": True},
            )

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: FakeAsyncClient())
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda _url: False)

    result = asyncio.run(llm_core.llm_call_async(
        "http://127.0.0.1:11434/api",
        "qwen3:8b",
        [{"role": "user", "content": "hello"}],
        allow_reasoning_fallback=False,
    ))

    assert result == "A concise answer."
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert seen["payload"]["think"] is False


def test_native_ollama_thinking_only_is_not_returned_as_content(monkeypatch):
    class FakeAsyncClient:
        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={
                    "message": {"content": "", "thinking": "private reasoning"},
                    "done": True,
                    "done_reason": "length",
                },
            )

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: FakeAsyncClient())
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda _url: False)

    result = asyncio.run(llm_core.llm_call_async(
        "http://127.0.0.1:11434/api",
        "qwen3:8b",
        [{"role": "user", "content": "hello"}],
        allow_reasoning_fallback=False,
    ))

    assert result == ""
    assert llm_core._parse_ollama_response(
        {"message": {"content": "", "thinking": "private reasoning"}},
        allow_reasoning_fallback=False,
    ) == ""
