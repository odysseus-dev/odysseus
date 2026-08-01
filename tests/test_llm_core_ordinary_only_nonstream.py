import asyncio
import json

from src import llm_core


class _Response:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _Client:
    def __init__(self, body):
        self.body = body
        self.payload = None

    async def post(self, url, **kwargs):
        self.payload = kwargs["json"]
        return _Response(self.body)

    def stream(self, *args, **kwargs):
        raise AssertionError("allowlisted compatibility mode must not open an SSE stream")


def _event_payloads(chunks):
    payloads = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                payloads.append(json.loads(line[5:].strip()))
    return payloads


def _drive(monkeypatch, body, *, model="moonshotai/Kimi-K3", max_tokens=4096):
    client = _Client(body)
    monkeypatch.setenv("ODYSSEUS_NONSTREAM_ORDINARY_ONLY_MODELS", "moonshotai/Kimi-K3")
    monkeypatch.delenv("ODYSSEUS_NONSTREAM_ORDINARY_ONLY_MAX_TOKENS", raising=False)
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda _url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *_args, **_kwargs: None)

    async def run():
        chunks = []
        async for chunk in llm_core.stream_llm(
            "http://localhost:8000/v1/chat/completions",
            model,
            [{"role": "user", "content": "hello"}],
            max_tokens=max_tokens,
        ):
            chunks.append(chunk)
        return chunks

    return client, asyncio.run(run())


def test_allowlist_is_exact_case_insensitive(monkeypatch):
    monkeypatch.setenv(
        "ODYSSEUS_NONSTREAM_ORDINARY_ONLY_MODELS",
        " example/other , MoonshotAI/Kimi-K3 ",
    )
    assert llm_core._ordinary_only_nonstream_enabled("moonshotai/kimi-k3")
    assert not llm_core._ordinary_only_nonstream_enabled("moonshotai/kimi-k3-extra")


def test_token_budget_is_bounded_and_respects_lower_request(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_NONSTREAM_ORDINARY_ONLY_MAX_TOKENS", "99999")
    assert llm_core._ordinary_only_nonstream_token_budget(0) == 4096
    assert llm_core._ordinary_only_nonstream_token_budget(32) == 32
    monkeypatch.setenv("ODYSSEUS_NONSTREAM_ORDINARY_ONLY_MAX_TOKENS", "invalid")
    assert llm_core._ordinary_only_nonstream_token_budget(4096) == 64


def test_guard_detects_single_codepoint_runs_without_replaying_text():
    repeated = chr(0x2603)
    guard = llm_core._DegenerateStreamGuard("example/model")
    assert guard.check(repeated * 8) is None
    event = guard.check(repeated * 8)
    assert event is not None
    assert repeated not in event
    payload = _event_payloads([event])[0]
    assert payload["code"] == "malformed_output"
    assert payload["reason"] == "single_codepoint_run"
    assert payload["codepoint"] == 0x2603
    assert payload["run_length"] == 16


def test_guard_does_not_replay_repeated_words():
    guard = llm_core._DegenerateStreamGuard("example/model")
    event = guard.check(("sample " * 30).strip())
    assert event is not None
    assert "sample" not in event
    assert _event_payloads([event])[0]["reason"] == "same_token_run"


def test_reserved_delimiter_shape_is_detected_without_literal_tokens():
    opening = chr(0x3C) + chr(0x7C)
    closing = chr(0x7C) + chr(0x3E)
    value = opening + "reserved" + closing
    assert llm_core._looks_like_reserved_delimiter_output(value)
    assert not llm_core._looks_like_reserved_delimiter_output("ordinary answer")


def test_nonstream_emits_only_ordinary_content_and_truncation(monkeypatch):
    hidden_value = chr(0x2603) * 24
    body = {
        "choices": [{
            "message": {"content": "A concise answer.", "reasoning": hidden_value},
            "finish_reason": "length",
        }],
        "usage": {"prompt_tokens": 11, "completion_tokens": 64},
    }
    client, chunks = _drive(monkeypatch, body)
    events = _event_payloads(chunks)

    assert client.payload["stream"] is False
    assert "stream_options" not in client.payload
    assert client.payload["max_tokens"] == 64
    assert {event.get("delta") for event in events if "delta" in event} == {"A concise answer."}
    assert any(event.get("type") == "output_truncated" for event in events)
    assert hidden_value not in "".join(chunks)


def test_malformed_content_precedes_truncation_and_is_not_replayed(monkeypatch):
    repeated = chr(0x2603) * 16
    body = {
        "choices": [{
            "message": {"content": repeated},
            "finish_reason": "length",
        }],
    }
    _client, chunks = _drive(monkeypatch, body)
    combined = "".join(chunks)
    events = _event_payloads(chunks)

    assert repeated not in combined
    assert not any("delta" in event for event in events)
    assert not any(event.get("type") == "output_truncated" for event in events)
    assert events[0]["code"] == "malformed_output"


def test_reserved_delimiter_content_is_not_replayed(monkeypatch):
    opening = chr(0x3C) + chr(0x7C)
    closing = chr(0x7C) + chr(0x3E)
    value = opening + "reserved" + closing
    body = {"choices": [{"message": {"content": value}, "finish_reason": "stop"}]}
    _client, chunks = _drive(monkeypatch, body)
    combined = "".join(chunks)
    events = _event_payloads(chunks)

    assert value not in combined
    assert events[0]["code"] == "malformed_output"
    assert events[0]["reason"] == "reserved_delimiter"


def test_non_allowlisted_model_keeps_streaming(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_NONSTREAM_ORDINARY_ONLY_MODELS", "moonshotai/Kimi-K3")
    assert not llm_core._ordinary_only_nonstream_enabled("example/other")
