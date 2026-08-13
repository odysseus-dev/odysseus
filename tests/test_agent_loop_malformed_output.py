import asyncio
import json

import src.agent_loop as agent_loop


def _collect(generator):
    async def run():
        return [chunk async for chunk in generator]

    return asyncio.run(run())


def _patch_common(monkeypatch):
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *_args, **_kwargs: 10, raising=False)


def _data_payloads(chunks):
    payloads = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                payloads.append(json.loads(line[5:].strip()))
    return payloads


def test_error_code_parser_uses_only_structured_code():
    event = "event: error\ndata: " + json.dumps({
        "status": 502,
        "code": "malformed_output",
        "text": chr(0x2603) * 24,
    }) + "\n\n"
    assert agent_loop._stream_error_code(event) == "malformed_output"


def test_reasoning_auxiliaries_are_removed_without_mutating_input():
    marker = chr(0x2603) * 24
    original = [{
        "role": "assistant",
        "content": "visible",
        "reasoning_content": marker,
        "reasoning": marker,
        "thinking": marker,
    }]
    cleaned = agent_loop._strip_reasoning_history_for_malformed_retry(original)
    assert cleaned[0] == {"role": "assistant", "content": "visible"}
    assert len(original[0]) == 5


def test_malformed_output_retries_once_at_zero_temperature(monkeypatch):
    _patch_common(monkeypatch)
    calls = []

    async def fake_stream(_candidates, messages, **kwargs):
        calls.append({"temperature": kwargs["temperature"], "messages": list(messages)})
        if len(calls) == 1:
            payload = {
                "status": 502,
                "code": "malformed_output",
                "text": "Model returned malformed repetitive output.",
            }
            yield "event: error\ndata: " + json.dumps(payload) + "\n\n"
            return
        yield 'data: {"delta": "Recovered answer."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    messages = [{
        "role": "assistant",
        "content": "prior answer",
        "reasoning": chr(0x2603) * 24,
    }, {"role": "user", "content": "continue"}]
    chunks = _collect(agent_loop.stream_agent_loop(
        "http://localhost:8000/v1/chat/completions",
        "example/model",
        messages,
        temperature=0.7,
        max_rounds=2,
    ))

    assert [call["temperature"] for call in calls] == [0.7, 0.0]
    assert "reasoning" not in calls[1]["messages"][0]
    assert not any(chunk.startswith("event: error") for chunk in chunks)
    assert any(payload.get("delta") == "Recovered answer." for payload in _data_payloads(chunks))


def test_valid_truncation_continues_without_tools_or_duplicate_output(monkeypatch):
    _patch_common(monkeypatch)
    calls = []

    async def fake_stream(_candidates, messages, **kwargs):
        calls.append({"messages": list(messages), **kwargs})
        if len(calls) == 1:
            yield 'data: {"delta": "First section."}\n\n'
            yield 'data: {"type": "output_truncated", "reason": "length"}\n\n'
            yield "data: [DONE]\n\n"
            return
        yield 'data: {"delta": " Second section."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    chunks = _collect(agent_loop.stream_agent_loop(
        "http://localhost:8000/v1/chat/completions",
        "example/model",
        [{"role": "user", "content": "write a detailed answer"}],
        max_rounds=2,
        relevant_tools={"bash"},
    ))
    payloads = _data_payloads(chunks)

    assert [payload.get("delta") for payload in payloads if "delta" in payload] == [
        "First section.",
        " Second section.",
    ]
    assert not any(payload.get("type") == "output_truncated" for payload in payloads)
    assert calls[1]["tools"] is None
    assert calls[1]["tool_choice_none"] is True
    assert "First section." not in calls[1]["messages"][-2]["content"]
    assert "base64-encoded" in calls[1]["messages"][-1]["content"]
    assert calls[1]["messages"][-2]["role"] == "user"
