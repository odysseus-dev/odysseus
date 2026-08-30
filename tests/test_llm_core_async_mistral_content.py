"""Integration regression test for #5435.

llm_call_async must normalise Mistral structured content to a plain string,
matching llm_call (sync) and stream_llm. Before the fix, the async
non-streaming parser returned the raw list when Mistral reasoning was enabled,
violating its -> str contract, leaking a non-string into callers such as
auto-title generation and memory extraction, and poisoning _response_cache
with a non-string value.
"""
import asyncio

import src.llm_core as llm_core


class _FakeResponse:
    is_success = True
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _payload(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _call(monkeypatch, content):
    async def fake_post(client, url, headers, **kwargs):
        return _FakeResponse(_payload(content))

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    llm_core._response_cache.clear()
    return asyncio.run(llm_core.llm_call_async(
        "http://mistral.test/v1/chat/completions",
        "mistral-medium",
        [{"role": "user", "content": "q"}],
    ))


def test_llm_call_async_normalizes_mistral_structured_content(monkeypatch):
    out = _call(monkeypatch, [
        {"type": "thinking",
         "thinking": [{"type": "text", "text": "Let me work through this..."}],
         "closed": True},
        {"type": "text", "text": "The answer is 42."},
    ])
    assert isinstance(out, str), f"expected str, got {type(out).__name__}"
    assert "The answer is 42." in out
    assert "Let me work through this..." in out
    # The cache must hold the normalised string, not the raw list,
    # otherwise repeat calls serve the poisoned value.
    assert all(isinstance(v, str) for v in llm_core._response_cache.values())


def test_llm_call_async_thinking_only_still_returns_str(monkeypatch):
    out = _call(monkeypatch, [
        {"type": "thinking",
         "thinking": [{"type": "text", "text": "still thinking"}],
         "closed": True},
    ])
    assert isinstance(out, str)
    assert "still thinking" in out


def test_llm_call_async_plain_string_passthrough(monkeypatch):
    out = _call(monkeypatch, "plain answer")
    assert out == "plain answer"
