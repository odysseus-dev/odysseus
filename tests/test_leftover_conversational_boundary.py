"""Leftover conversational callers must leave llm_call / stream_llm."""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agent_tools import model_interaction_tools as mit
from src.agent_tools import session_tools as st
from src.deep_research import DeepResearcher
from src.task_scheduler import TaskScheduler
from src import teacher_escalation

_FORBIDDEN = (
    "llm_call_async",
    "llm_call_async_with_fallback",
    "llm_call_async_with_route_fallback",
    "task_llm_call_async",
    "stream_llm(",
    "stream_llm_with_fallback",
)


def _assert_no_legacy_inference(source: str, *, require: str) -> None:
    assert require in source
    for needle in _FORBIDDEN:
        assert needle not in source, f"leftover inference primitive {needle!r}"


def test_call_teacher_uses_governed_openhands():
    _assert_no_legacy_inference(
        inspect.getsource(teacher_escalation._call_teacher),
        require="stream_governed_agent(",
    )


def test_evaluate_turn_llm_uses_model_job():
    _assert_no_legacy_inference(
        inspect.getsource(teacher_escalation.evaluate_turn_llm),
        require="submit_model_job",
    )


def test_ask_teacher_uses_governed_openhands():
    _assert_no_legacy_inference(
        inspect.getsource(mit.ask_teacher),
        require="stream_governed_agent(",
    )


def test_chat_with_model_uses_model_job():
    _assert_no_legacy_inference(
        inspect.getsource(mit.chat_with_model),
        require="submit_model_job",
    )


def test_send_to_session_uses_governed_openhands():
    _assert_no_legacy_inference(
        inspect.getsource(st.send_to_session),
        require="stream_governed_agent(",
    )


def test_deep_researcher_llm_uses_governed_openhands():
    _assert_no_legacy_inference(
        inspect.getsource(DeepResearcher._llm),
        require="stream_governed_agent(",
    )


def test_scheduler_leftover_completions_use_governed_openhands():
    _assert_no_legacy_inference(
        inspect.getsource(TaskScheduler._execute_llm_task),
        require="stream_governed_agent(",
    )
    _assert_no_legacy_inference(
        inspect.getsource(TaskScheduler._run_agent_loop),
        require="stream_governed_agent(",
    )


def test_owned_files_register_no_completions_gateway():
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        '"/v1/chat/completions"',
        "'/v1/chat/completions'",
        '"/api/v1/chat/completions"',
        "'/api/v1/chat/completions'",
    )
    for rel in (
        "src/teacher_escalation.py",
        "src/agent_tools/model_interaction_tools.py",
        "src/agent_tools/session_tools.py",
        "src/deep_research.py",
        "src/task_scheduler.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{rel} registers inbound completions path"


@pytest.mark.asyncio
async def test_evaluate_turn_llm_job_flags_failure(monkeypatch):
    seen = {}

    def fake_job(archetype, payload, owner, **kwargs):
        seen["id"] = getattr(archetype, "id", None)
        seen["owner"] = owner
        seen["text"] = payload.get("text")
        return SimpleNamespace(output={"text": '  "Failure"  '})

    monkeypatch.setattr(
        "services.agents.model_jobs.submit_model_job", fake_job, raising=False
    )

    status, reason = await teacher_escalation.evaluate_turn_llm(
        user_request="test request",
        tool_results=[],
        agent_reply="test reply",
        student_endpoint_url="http://student.local/v1",
        owner="alice",
    )

    assert status == "failure"
    assert "LLM evaluation flagged failure" in reason
    assert seen["id"] == "evaluate-turn"
    assert seen["owner"] == "alice"
    assert seen["text"]


@pytest.mark.asyncio
async def test_call_teacher_collects_governed_deltas(monkeypatch):
    seen = {}

    def fake_resolve(spec, owner=None):
        seen["spec"] = spec
        seen["owner"] = owner
        return ("http://teacher.local/v1", "teacher-model", {})

    async def fake_stream(**kwargs):
        seen["messages"] = kwargs.get("messages")
        seen["stream_owner"] = kwargs.get("owner")
        yield 'data: {"delta": "teacher reply"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve)
    monkeypatch.setattr(
        "services.agents.legacy_bridge.stream_governed_agent", fake_stream
    )

    result = await teacher_escalation._call_teacher(
        "teacher-model", "prompt", owner="alice"
    )

    assert result == "teacher reply"
    assert seen["owner"] == "alice"
    assert seen["stream_owner"] == "alice"
    assert seen["messages"][1]["content"] == "prompt"


@pytest.mark.asyncio
async def test_chat_with_model_job_returns_response(monkeypatch):
    seen = {}

    def fake_resolve(spec, owner=None):
        seen["owner"] = owner
        return ("http://x", "model-x", {})

    def fake_job(archetype, payload, owner, **kwargs):
        seen["job"] = getattr(archetype, "id", None)
        seen["payload_text"] = payload.get("text")
        seen["job_owner"] = owner
        return SimpleNamespace(output={"text": "hi back"})

    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve)
    monkeypatch.setattr(
        "services.agents.model_jobs.submit_model_job", fake_job, raising=False
    )

    res = await mit.chat_with_model("model-x\nhello there", owner="alice")

    assert res == {"model": "model-x", "response": "hi back"}
    assert seen["owner"] == "alice"
    assert seen["job"] == "chat-with-model"
    assert seen["payload_text"] == "hello there"


@pytest.mark.asyncio
async def test_ask_teacher_collects_governed_deltas(monkeypatch):
    seen = {}

    def fake_resolve(spec, owner=None):
        seen["owner"] = owner
        return ("http://x", "teacher-x", {})

    async def fake_stream(**kwargs):
        seen["archetype"] = kwargs.get("archetype")
        yield 'data: {"delta": "do this and that"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve)
    monkeypatch.setattr(
        "services.agents.legacy_bridge.stream_governed_agent", fake_stream
    )

    res = await mit.ask_teacher("teacher-x\nI am stuck", owner="bob")

    assert res["teacher"] is True
    assert res["response"] == "do this and that"
    assert seen["owner"] == "bob"


@pytest.mark.asyncio
async def test_send_to_session_reuses_openhands_conversation(monkeypatch):
    seen = {}

    class _Sess:
        name = "Peer"
        endpoint_url = "http://x"
        model = "live-model"
        owner = "alice"
        headers = {}
        openhands_conversation_id = "oh-conv-1"
        added = []

        def get_context_messages(self):
            return [{"role": "user", "content": "prior"}]

        def add_message(self, message):
            self.added.append(message)

    class _Mgr:
        def get_session(self, sid):
            return _Sess() if sid == "sid-1" else None

    async def fake_stream(**kwargs):
        seen.update(kwargs)
        yield 'data: {"type": "execution", "conversation_id": "oh-conv-1"}\n\n'
        yield 'data: {"delta": "peer reply"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(st, "get_session_manager", lambda: _Mgr())
    monkeypatch.setattr(
        "services.agents.legacy_bridge.stream_governed_agent", fake_stream
    )

    res = await st.send_to_session("sid-1\nhello", owner="alice")

    assert res["response"] == "peer reply"
    assert seen.get("conversation_id") == "oh-conv-1"
    assert seen.get("session_id") == "sid-1"
    assert seen["messages"][-1]["content"] == "hello"


@pytest.mark.asyncio
async def test_deep_researcher_llm_reuses_bound_conversation(monkeypatch):
    seen = {}

    async def fake_stream(**kwargs):
        seen.update(kwargs)
        yield 'data: {"type": "execution", "conversation_id": "oh-research-1"}\n\n'
        yield 'data: {"delta": "plan text"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(
        "services.agents.legacy_bridge.stream_governed_agent", fake_stream
    )

    researcher = DeepResearcher(llm_endpoint="http://x", llm_model="m")
    researcher.session_id = "sess-1"
    researcher.owner = "alice"
    researcher.openhands_conversation_id = "oh-research-1"
    out = await researcher._llm([{"role": "user", "content": "plan"}])

    assert out == "plan text"
    assert seen.get("conversation_id") == "oh-research-1"
    assert seen.get("session_id") == "sess-1"
    assert seen.get("owner") == "alice"
    assert researcher.openhands_conversation_id == "oh-research-1"
    assert seen.get("conversation_id") != seen.get("session_id")


@pytest.mark.asyncio
async def test_scheduler_fallback_uses_governed_agent(monkeypatch):
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: [] if key == "disabled_tools" else default,
    )
    monkeypatch.setattr("src.tool_index.get_tool_index", lambda: None)

    captured = {}

    async def _fail(*args, **kwargs):
        raise RuntimeError("simulated failure")

    async def _capture_stream(**kwargs):
        captured["messages"] = list(kwargs.get("messages", []))
        yield 'data: {"delta": "fallback"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(
        "services.agents.legacy_bridge.stream_governed_agent", _capture_stream
    )

    sched = TaskScheduler(session_manager=None)
    sched._run_agent_loop = _fail
    result = await sched._execute_llm_task(
        SimpleNamespace(
            crew_member_id=None,
            endpoint_url="http://ep/v1",
            model="m",
            session_id="s",
            owner="admin",
            prompt="send the digest",
            name="job",
            max_steps=5,
            character_id=None,
        ),
        db=None,
    )

    assert result == "fallback"
    msgs = captured.get("messages", [])
    assert len(msgs) == 3
    assert msgs[0]["role"] == "system"
    assert "Current time:" not in msgs[0]["content"]
    assert msgs[2]["content"] == "send the digest"


@pytest.mark.asyncio
async def test_scheduler_grace_uses_governed_agent(monkeypatch):
    calls = []

    async def fake_stream(**kwargs):
        calls.append(kwargs.get("messages"))
        if len(calls) == 1:
            yield "data: [DONE]\n\n"
            return
        yield 'data: {"delta": "grace summary"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(
        "services.agents.legacy_bridge.stream_governed_agent", fake_stream
    )
    monkeypatch.setattr(
        "src.task_endpoint.resolve_task_candidates",
        lambda **kwargs: [],
    )

    result = await TaskScheduler(session_manager=None)._run_agent_loop(
        "http://ep/v1",
        "m",
        SimpleNamespace(
            crew_member_id=None,
            endpoint_url="http://ep/v1",
            model="m",
            session_id="s",
            owner="admin",
            prompt="run the digest",
            name="job",
            max_steps=5,
            character_id=None,
        ),
        "s",
    )

    assert result == "grace summary"
    assert len(calls) == 2
    assert "Summarize what you accomplished" in calls[1][-1]["content"]
