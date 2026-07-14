import asyncio
import json

import pytest

from src import agent_loop as al


def _block(payload):
    return "<continuation_checkpoint>" + json.dumps(payload) + "</continuation_checkpoint>"


def _payload(status="incomplete"):
    return {
        "status": status,
        "completed": ["Inspected code"],
        "pending": ["Transfer patch"] if status == "incomplete" else [],
        "files_changed": ["src/agent_loop.py"],
        "tests_run": ["pytest: passed"],
        "blockers": [],
        "next_action": "Transfer the verified patch." if status == "incomplete" else "",
        "required_tools": ["bash", "edit_file"],
    }


def test_extracts_incomplete_checkpoint_and_preserves_ordinary_text():
    ordinary, checkpoint = al._extract_continuation_checkpoint(
        "Progress report.\n" + _block(_payload()) + "\nPlease continue."
    )
    assert ordinary == "Progress report.\n\nPlease continue."
    assert checkpoint["status"] == "incomplete"
    assert checkpoint["completed"] == ["Inspected code"]
    assert checkpoint["required_tools"] == ["bash", "edit_file"]


def test_complete_checkpoint_is_normalized():
    ordinary, checkpoint = al._extract_continuation_checkpoint(_block(_payload("complete")))
    assert ordinary == ""
    assert checkpoint["status"] == "complete"
    assert checkpoint["pending"] == []


@pytest.mark.parametrize("text", [
    "<continuation_checkpoint>{bad}</continuation_checkpoint>",
    _block({**_payload(), "status": "paused"}),
    _block({**_payload(), "unexpected": True}),
    "<continuation_checkpoint>" + ("x" * 8193) + "</continuation_checkpoint>",
])
def test_rejects_malformed_unsupported_or_oversized_blocks(text):
    ordinary, checkpoint = al._extract_continuation_checkpoint(text)
    assert ordinary == text
    assert checkpoint is None


def test_normalization_bounds_lists_and_fields():
    payload = _payload()
    payload["completed"] = [("item %d " % i) + ("x" * 500) for i in range(20)]
    payload["required_tools"] = ["bash"] * 20 + ["not a valid tool"]
    checkpoint = al._normalize_continuation_checkpoint(payload)
    assert len(checkpoint["completed"]) == 8
    assert all(len(item) <= 300 for item in checkpoint["completed"])
    assert len(checkpoint["required_tools"]) == 12


def test_deterministic_round_exhaustion_checkpoint():
    checkpoint = al._rounds_exhausted_checkpoint(50)
    assert checkpoint["status"] == "incomplete"
    assert checkpoint["next_action"]
    assert "50-round" in checkpoint["blockers"][0]


def _collect(gen):
    async def run():
        return [chunk async for chunk in gen]
    return asyncio.run(run())


def _events(chunks):
    return [json.loads(chunk[6:]) for chunk in chunks
            if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]")]


def _patch_common(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *args, **kwargs: 10, raising=False)


def test_round_exhaustion_emits_fallback_checkpoint_and_incomplete_metrics(monkeypatch):
    _patch_common(monkeypatch)

    async def fake_stream(*args, **kwargs):
        call = {"id": "call_1", "name": "bash", "arguments": json.dumps({"command": "true"})}
        yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(*args, **kwargs):
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(al, "execute_tool_block", fake_execute)
    chunks = _collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-test",
        [{"role": "user", "content": "Do work"}], max_rounds=1,
        relevant_tools={"bash"}, _is_teacher_run=True,
    ))
    events = _events(chunks)
    exhausted = next(event for event in events if event.get("type") == "rounds_exhausted")
    metrics = next(event["data"] for event in events if event.get("type") == "metrics")
    assert exhausted["checkpoint"]["status"] == "incomplete"
    assert metrics["rounds_exhausted"] is True
    assert metrics["task_incomplete"] is True
    assert metrics["continuation_checkpoint"] == exhausted["checkpoint"]
    assert all(event.get("delta") != "Done." for event in events)


def test_complete_checkpoint_sets_complete_metrics(monkeypatch):
    _patch_common(monkeypatch)
    checkpoint_text = _block(_payload("complete"))

    async def fake_stream(*args, **kwargs):
        yield f'data: {json.dumps({"delta": "Finished. " + checkpoint_text})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)
    events = _events(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-test",
        [{"role": "user", "content": "Answer"}], max_rounds=3,
        relevant_tools=set(), _is_teacher_run=True,
    )))
    metrics = next(event["data"] for event in events if event.get("type") == "metrics")
    assert metrics["continuation_checkpoint"]["status"] == "complete"
    assert metrics["task_incomplete"] is False


def test_incomplete_checkpoint_on_direct_path_sets_incomplete_without_fallback(monkeypatch):
    _patch_common(monkeypatch)
    checkpoint_text = _block(_payload("incomplete"))

    async def fake_stream(*args, **kwargs):
        yield f'data: {json.dumps({"delta": checkpoint_text})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)
    events = _events(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-test",
        [{"role": "user", "content": "Answer"}], max_rounds=3,
        relevant_tools=set(), _is_teacher_run=True,
    )))
    metrics = next(event["data"] for event in events if event.get("type") == "metrics")
    assert metrics["continuation_checkpoint"]["status"] == "incomplete"
    assert metrics["task_incomplete"] is True
    deltas = "".join(event.get("delta", "") for event in events)
    assert "Hey." not in deltas
    assert "Done." not in deltas


def test_success_without_checkpoint_remains_unchanged(monkeypatch):
    _patch_common(monkeypatch)

    async def fake_stream(*args, **kwargs):
        yield 'data: {"delta":"All requirements complete."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)
    events = _events(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-test",
        [{"role": "user", "content": "Answer"}], max_rounds=3,
        relevant_tools=set(), _is_teacher_run=True,
    )))
    metrics = next(event["data"] for event in events if event.get("type") == "metrics")
    assert "continuation_checkpoint" not in metrics
    assert "task_incomplete" not in metrics
    assert "All requirements complete." in "".join(event.get("delta", "") for event in events)
