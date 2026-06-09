"""SSE lines with no space after \'data:\' must still be parsed.

The SSE spec makes the space after the colon optional ("data:value" is
valid), and several gateways / local inference servers emit it that way.
stream_llm gated on line.startswith("data: ") (trailing space) in both the
OpenAI-compatible and Anthropic branches, so those providers\' ENTIRE
stream — content and usage — was silently dropped.
"""
import asyncio
import json

from src import llm_core


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeResp(self._lines)

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, **kw):
        return _FakeStreamCtx(self._lines)


def _drive(monkeypatch, url, lines, model):
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeClient(lines))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_mark_host_dead", lambda *a, **k: False, raising=False)

    async def run():
        out = []
        async for chunk in llm_core.stream_llm(
            url, model, [{"role": "user", "content": "hi"}],
            headers={"Authorization": "Bearer k"},
        ):
            out.append(chunk)
        return "".join(out)

    return asyncio.run(run())


def _deltas(blob):
    deltas = []
    for ln in blob.split("\n"):
        ln = ln.strip()
        if ln.startswith("data: ") and ln[6:] != "[DONE]":
            try:
                j = json.loads(ln[6:])
            except ValueError:
                continue
            if "delta" in j:
                deltas.append(j["delta"])
    return deltas


def test_openai_compat_no_space_data_is_parsed(monkeypatch):
    lines = [
        'data:' + json.dumps({"choices": [{"delta": {"content": "Hi"}}]}),
        'data:' + json.dumps({"choices": [{"delta": {"content": " there"}}]}),
        'data:[DONE]',
    ]
    blob = _drive(
        monkeypatch,
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        lines,
        "gpt-4o-test",
    )
    assert "".join(_deltas(blob)) == "Hi there"


def test_openai_compat_with_space_still_works(monkeypatch):
    lines = [
        'data: ' + json.dumps({"choices": [{"delta": {"content": "Yo"}}]}),
        'data: [DONE]',
    ]
    blob = _drive(
        monkeypatch,
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        lines,
        "gpt-4o-test",
    )
    assert "".join(_deltas(blob)) == "Yo"


def test_anthropic_no_space_data_is_parsed(monkeypatch):
    lines = [
        'data:' + json.dumps({"type": "content_block_delta",
                              "delta": {"type": "text_delta", "text": "Hi"}}),
        'data:' + json.dumps({"type": "message_stop"}),
    ]
    blob = _drive(
        monkeypatch,
        "https://api.anthropic.com/v1/messages",
        lines,
        "claude-test",
    )
    assert "Hi" in "".join(_deltas(blob))
