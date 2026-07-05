import asyncio
from types import SimpleNamespace
import pytest

import src.teacher_escalation as teacher_escalation


@pytest.mark.asyncio
async def test_evaluate_turn_llm_ok(monkeypatch):
    seen = {}

    def fake_resolve_endpoint(prefix, fallback_url=None, owner=None):
        seen["prefix"] = prefix
        seen["owner"] = owner
        return "http://endpoint.local/v1", "utility-model", {}

    async def fake_llm_call_async(url, model, messages, **kwargs):
        seen["called"] = True
        return "ok"

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    status, reason = await teacher_escalation.evaluate_turn_llm(
        user_request="test request",
        tool_results=[],
        agent_reply="test reply",
        student_endpoint_url="http://student.local/v1",
        owner="alice",
    )

    assert status == "ok"
    assert reason is None
    assert seen["prefix"] == "utility"
    assert seen["owner"] == "alice"
    assert seen["called"] is True


@pytest.mark.asyncio
async def test_evaluate_turn_llm_failure(monkeypatch):
    def fake_resolve_endpoint(prefix, fallback_url=None, owner=None):
        return "http://endpoint.local/v1", "utility-model", {}

    async def fake_llm_call_async(url, model, messages, **kwargs):
        return "  \"Failure\"  "

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    status, reason = await teacher_escalation.evaluate_turn_llm(
        user_request="test request",
        tool_results=[],
        agent_reply="test reply",
        student_endpoint_url="http://student.local/v1",
        owner="alice",
    )

    assert status == "failure"
    assert "LLM evaluation flagged failure" in reason


@pytest.mark.asyncio
async def test_evaluate_turn_llm_contains_failure_but_not_exact_match(monkeypatch):
    def fake_resolve_endpoint(prefix, fallback_url=None, owner=None):
        return "http://endpoint.local/v1", "utility-model", {}

    async def fake_llm_call_async(url, model, messages, **kwargs):
        return "this agent execution is not a failure"

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    status, reason = await teacher_escalation.evaluate_turn_llm(
        user_request="test request",
        tool_results=[],
        agent_reply="test reply",
        student_endpoint_url="http://student.local/v1",
        owner="alice",
    )

    assert status == "ok"
    assert reason is None


@pytest.mark.asyncio
async def test_evaluate_turn_llm_exception_handling(monkeypatch):
    def fake_resolve_endpoint(prefix, fallback_url=None, owner=None):
        return "http://endpoint.local/v1", "utility-model", {}

    async def fake_llm_call_async(url, model, messages, **kwargs):
        raise RuntimeError("model timeout")

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    # Should degrade gracefully to "ok"
    status, reason = await teacher_escalation.evaluate_turn_llm(
        user_request="test request",
        tool_results=[],
        agent_reply="test reply",
        student_endpoint_url="http://student.local/v1",
        owner="alice",
    )

    assert status == "ok"
    assert reason is None


@pytest.mark.asyncio
async def test_maybe_escalate_triggers_tier2_background_task(monkeypatch):
    # Enable teacher settings
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: {"teacher_enabled": True, "teacher_model": "teacher-model", "teacher_tier2_enabled": True}.get(key, default))

    # Regex check says OK
    monkeypatch.setattr("src.teacher_escalation.evaluate_turn_regex", lambda *args: ("ok", None))

    llm_eval_called = []
    async def fake_evaluate_turn_llm(*args, **kwargs):
        llm_eval_called.append(True)
        return "failure", "LLM flagged failure"

    monkeypatch.setattr("src.teacher_escalation.evaluate_turn_llm", fake_evaluate_turn_llm)

    escalate_called = []
    async def fake_escalate_and_learn(user_request, tool_results, agent_reply, failure_reason, owner):
        escalate_called.append(failure_reason)
        return "skill-slug"

    monkeypatch.setattr("src.teacher_escalation.escalate_and_learn", fake_escalate_and_learn)

    # Call maybe_escalate
    task = teacher_escalation.maybe_escalate(
        student_endpoint_url="http://student.local/v1",
        mode="agent",
        user_request="test request",
        tool_results=[],
        agent_reply="test reply",
        owner="alice",
    )

    assert task is not None
    assert task.get_name() == "teacher_escalation_tier2"

    # Await the background task execution
    await task

    assert llm_eval_called == [True]
    assert escalate_called == ["LLM flagged failure"]


@pytest.mark.asyncio
async def test_maybe_escalate_tier2_disabled_by_default(monkeypatch):
    # Enable teacher settings, but keep tier2 disabled
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: {"teacher_enabled": True, "teacher_model": "teacher-model", "teacher_tier2_enabled": False}.get(key, default))

    # Regex check says OK
    monkeypatch.setattr("src.teacher_escalation.evaluate_turn_regex", lambda *args: ("ok", None))

    # Call maybe_escalate
    task = teacher_escalation.maybe_escalate(
        student_endpoint_url="http://student.local/v1",
        mode="agent",
        user_request="test request",
        tool_results=[],
        agent_reply="test reply",
        owner="alice",
    )

    # Should not start any background task since Tier 2 is disabled
    assert task is None


@pytest.mark.asyncio
async def test_run_teacher_inline_triggers_tier2_escalation(monkeypatch):
    # Settings and gates
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: {"teacher_enabled": True, "teacher_model": "teacher-model", "teacher_tier2_enabled": True}.get(key, default))
    monkeypatch.setattr("src.ai_interaction._resolve_model", lambda spec, owner=None: ("http://teacher.local/v1", "teacher-model", {}))

    # Regex evaluation says "ok"
    monkeypatch.setattr("src.teacher_escalation.evaluate_turn_regex", lambda *args: ("ok", None))

    # LLM evaluation flags "failure"
    async def fake_evaluate_turn_llm(*args, **kwargs):
        return "failure", "LLM flagged failure"
    monkeypatch.setattr("src.teacher_escalation.evaluate_turn_llm", fake_evaluate_turn_llm)

    # Mock stream_agent_loop recursively called by run_teacher_inline
    async def fake_stream_agent_loop(*args, **kwargs):
        yield "data: {\"type\": \"tool_output\", \"tool\": \"bash\"}\n\n"
        yield "data: {\"type\": \"text\", \"delta\": \"Teacher reply\"}\n\n"
        yield "data: [DONE]\n\n"
    monkeypatch.setattr("src.agent_loop.stream_agent_loop", fake_stream_agent_loop)

    # Mock _call_teacher returning a skill definition
    async def fake_call_teacher(spec, prompt, owner=None):
        return '```json\n{"action": "add", "name": "test-skill"}\n```'
    monkeypatch.setattr("src.teacher_escalation._call_teacher", fake_call_teacher)

    # Mock do_manage_skills
    async def fake_do_manage_skills(skill_json, owner=None):
        return {"success": True}
    monkeypatch.setattr("src.tool_implementations.do_manage_skills", fake_do_manage_skills)

    events = []
    async for evt in teacher_escalation.run_teacher_inline(
        student_endpoint_url="http://student.local/v1",
        student_messages=[{"role": "user", "content": "test request"}],
        student_tool_events=[],
        student_reply="student reply",
        owner="alice",
    ):
        events.append(evt)

    # Make sure teacher takeover was announced and executed
    assert any("teacher_takeover" in evt for evt in events)
    assert any("tool_output" in evt for evt in events)
    assert any("skill_saved" in evt for evt in events)


@pytest.mark.asyncio
async def test_run_teacher_inline_tier2_disabled_by_default(monkeypatch):
    # Settings and gates (Tier 2 disabled)
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: {"teacher_enabled": True, "teacher_model": "teacher-model", "teacher_tier2_enabled": False}.get(key, default))

    # Regex evaluation says "ok"
    monkeypatch.setattr("src.teacher_escalation.evaluate_turn_regex", lambda *args: ("ok", None))

    events = []
    async for evt in teacher_escalation.run_teacher_inline(
        student_endpoint_url="http://student.local/v1",
        student_messages=[{"role": "user", "content": "test request"}],
        student_tool_events=[],
        student_reply="student reply",
        owner="alice",
    ):
        events.append(evt)

    # Should exit early without any events (no takeover)
    assert len(events) == 0
