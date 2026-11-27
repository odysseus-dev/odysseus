"""Regression: replayed reasoning must reach Ollama's OpenAI-compatible /v1.

The agent loop resends a tool round's reasoning as ``reasoning_content`` on the
assistant message. llama-server reads that field, but Ollama's /v1 layer reads
``reasoning`` and ignores ``reasoning_content``, so the replayed reasoning
never reached Ollama (issue #7114).

Covers:
- Ollama /v1 (port 11434): the assistant message carries both fields with the
  same text, on the streaming and both non-streaming paths
- hosted OpenAI-style and local llama-server style /v1 URLs: messages are
  sent exactly as before, without a ``reasoning`` field
"""
import asyncio
import copy

import httpx
import pytest

from src import llm_core


OLLAMA_V1 = "http://127.0.0.1:11434/v1/chat/completions"
OLLAMA_V1_DOCKER = "http://ollama:11434/v1"
HOSTED_V1 = "https://api.openai.com/v1/chat/completions"
LLAMA_SERVER_V1 = "http://127.0.0.1:8080/v1/chat/completions"

MODEL = "gpt-oss:20b"
REASONING = "The user wants the file list, so call list_files first."


def _messages():
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello."},
        {"role": "user", "content": "List the files."},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": REASONING,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_files", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "a.txt\nb.txt"},
    ]


# ---------------------------------------------------------------------------
# Fake HTTP clients — capture the outgoing payload without network I/O
# ---------------------------------------------------------------------------

class _FakeStreamResp:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}'
        yield "data: [DONE]"

    async def aread(self):
        return b""


class _FakeStreamCtx:
    async def __aenter__(self):
        return _FakeStreamResp()

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    """Stand-in for the shared httpx.AsyncClient that records the payload."""

    def __init__(self):
        self.payload = None

    def stream(self, method, url, **kw):
        self.payload = kw.get("json")
        return _FakeStreamCtx()

    async def post(self, url, **kw):
        self.payload = kw.get("json")
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": "ok"}}]},
        )


@pytest.fixture
def client(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: fake)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "get_context_length", lambda u, m: 32768)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: None)
    monkeypatch.setattr(llm_core, "_set_cached_response", lambda *a, **k: None)
    return fake


def _stream_messages(client, url, messages):
    async def run():
        return [c async for c in llm_core.stream_llm(url, MODEL, messages)]

    asyncio.run(run())
    return client.payload["messages"]


def _async_messages(client, url, messages):
    asyncio.run(llm_core.llm_call_async(url, MODEL, messages))
    return client.payload["messages"]


def _sync_messages(monkeypatch, url, messages):
    seen = {}

    def fake_post(target, headers=None, json=None, timeout=None):
        seen["json"] = json
        return httpx.Response(
            200,
            request=httpx.Request("POST", target),
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)
    llm_core.llm_call(url, MODEL, messages)
    return seen["json"]["messages"]


def _tool_call_turn(sent):
    return next(m for m in sent if m["role"] == "assistant" and m.get("tool_calls"))


# ---------------------------------------------------------------------------
# Ollama /v1 — the reasoning goes out under both names
# ---------------------------------------------------------------------------

class TestOllamaV1ReceivesReasoning:

    def test_stream_llm(self, client):
        turn = _tool_call_turn(_stream_messages(client, OLLAMA_V1, _messages()))
        assert turn["reasoning"] == REASONING
        assert turn["reasoning_content"] == REASONING

    def test_llm_call_async(self, client):
        turn = _tool_call_turn(_async_messages(client, OLLAMA_V1, _messages()))
        assert turn["reasoning"] == REASONING
        assert turn["reasoning_content"] == REASONING

    def test_llm_call(self, client, monkeypatch):
        turn = _tool_call_turn(_sync_messages(monkeypatch, OLLAMA_V1, _messages()))
        assert turn["reasoning"] == REASONING
        assert turn["reasoning_content"] == REASONING

    def test_docker_service_host(self, client):
        """Ollama reached by its compose service name, not loopback."""
        turn = _tool_call_turn(_stream_messages(client, OLLAMA_V1_DOCKER, _messages()))
        assert turn["reasoning"] == REASONING

    def test_only_turns_with_reasoning_get_the_field(self, client):
        sent = _stream_messages(client, OLLAMA_V1, _messages())
        with_reasoning = [m for m in sent if "reasoning" in m]
        assert with_reasoning == [_tool_call_turn(sent)]

    def test_caller_history_is_not_modified(self, client):
        messages = _messages()
        before = copy.deepcopy(messages)
        _stream_messages(client, OLLAMA_V1, messages)
        assert messages == before


# ---------------------------------------------------------------------------
# Other OpenAI-compatible servers — payload unchanged
# ---------------------------------------------------------------------------

class TestOtherServersUnchanged:
    """These history messages already pass the clean-up as they are, so the
    servers must receive exactly them: reasoning_content, and no reasoning."""

    @pytest.mark.parametrize("url", [HOSTED_V1, LLAMA_SERVER_V1])
    def test_stream_llm(self, client, url):
        assert _stream_messages(client, url, _messages()) == _messages()

    @pytest.mark.parametrize("url", [HOSTED_V1, LLAMA_SERVER_V1])
    def test_llm_call_async(self, client, url):
        assert _async_messages(client, url, _messages()) == _messages()

    @pytest.mark.parametrize("url", [HOSTED_V1, LLAMA_SERVER_V1])
    def test_llm_call(self, client, monkeypatch, url):
        assert _sync_messages(monkeypatch, url, _messages()) == _messages()
