import asyncio
import json
from pathlib import Path

from src import agent_loop as al


def _checkpoint(status="incomplete"):
    return {
        "status": status,
        "completed": ["Inspected the current state"],
        "pending": ["Continue implementation"] if status == "incomplete" else [],
        "files_changed": ["src/agent_loop.py"],
        "tests_run": ["focused tests passed"],
        "blockers": [],
        "next_action": "Continue the next atomic step." if status == "incomplete" else "",
        "required_tools": ["bash", "edit_file"],
    }


def _collect(gen):
    async def run():
        return [chunk async for chunk in gen]

    return asyncio.run(run())


def _events(chunks):
    return [
        json.loads(chunk[6:])
        for chunk in chunks
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]")
    ]


def _patch_common(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *args, **kwargs: 10, raising=False)


def test_handoff_message_is_bounded_internal_data():
    message = al._continuation_handoff_message(_checkpoint())
    assert message["role"] == "system"
    assert "bounded task-state data" in message["content"]
    assert "current tool permissions" in message["content"]
    raw = message["content"].split("<continuation_checkpoint>", 1)[1].split(
        "</continuation_checkpoint>", 1
    )[0]
    assert json.loads(raw) == _checkpoint()


def test_complete_or_invalid_checkpoint_is_not_injected():
    assert al._continuation_handoff_message(_checkpoint("complete")) is None
    assert al._continuation_handoff_message({"status": "incomplete"}) is None


def test_stream_injects_handoff_before_latest_user_without_granting_tools(monkeypatch):
    _patch_common(monkeypatch)
    captured = []

    async def fake_stream(_candidates, messages, **kwargs):
        captured.append(messages)
        yield 'data: {"delta":"Resumed safely."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)
    events = _events(
        _collect(
            al.stream_agent_loop(
                "https://api.openai.com/v1",
                "gpt-test",
                [{"role": "user", "content": "Continue the unfinished repository task."}],
                max_rounds=3,
                relevant_tools=set(),
                continuation_checkpoint=_checkpoint(),
                _is_teacher_run=True,
            )
        )
    )
    assert captured
    sent = captured[0]
    assert sent[-1]["role"] == "user"
    handoff = next(
        message
        for message in sent
        if "Continuation handoff" in message.get("content", "")
    )
    assert handoff["role"] == "system"
    assert not any(event.get("type") == "tool_start" for event in events)


def test_frontend_posts_checkpoint_only_for_round_limit_continue():
    source = Path("static/js/chat.js").read_text(encoding="utf-8")
    assert "let _pendingContinuationCheckpoint = null;" in source
    assert "_pendingContinuationCheckpoint = _checkpoint;" in source
    assert (
        "fd.append('continuation_checkpoint', "
        "JSON.stringify(_pendingContinuationCheckpoint));"
    ) in source
    assert "_pendingContinuationCheckpoint = null;" in source


def test_route_validates_and_forwards_checkpoint():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    assert 'raw_checkpoint = form_data.get("continuation_checkpoint")' in source
    assert "_normalize_continuation_checkpoint(" in source
    assert "continuation_checkpoint=continuation_checkpoint," in source
