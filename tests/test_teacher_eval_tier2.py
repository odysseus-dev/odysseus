import asyncio
import json
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
async def test_background_teacher_learning_never_persists_without_approval(monkeypatch):
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: {
            "teacher_model": "teacher-model",
        }.get(key, default),
    )

    async def fail_teacher_call(*args, **kwargs):
        raise AssertionError("background learning spent a teacher call without approval UI")

    async def fail_direct_skill_save(*args, **kwargs):
        raise AssertionError("background teacher output was persisted directly")

    monkeypatch.setattr("src.teacher_escalation._call_teacher", fail_teacher_call)
    monkeypatch.setattr(
        "src.tool_implementations.do_manage_skills",
        fail_direct_skill_save,
    )

    saved = await teacher_escalation.escalate_and_learn(
        user_request="test request",
        tool_results=[],
        agent_reply="student failed",
        failure_reason="test failure",
        owner="alice",
    )

    assert saved is None


@pytest.mark.asyncio
async def test_run_teacher_inline_triggers_tier2_escalation(monkeypatch):
    from src.tool_approvals import tool_approval_store

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
        yield "data: " + json.dumps({
            "type": "metrics",
            "data": {
                "model": "teacher-model",
                "round_texts": ["Teacher reply"],
                "tool_events": [
                    {
                        "round": 1,
                        "tool": "bash",
                        "output": "done",
                        "exit_code": 0,
                    },
                ],
            },
        }) + "\n\n"
        yield "data: [DONE]\n\n"
    monkeypatch.setattr("src.agent_loop.stream_agent_loop", fake_stream_agent_loop)

    # Mock _call_teacher returning a skill definition
    async def fake_call_teacher(spec, prompt, owner=None):
        return '```json\n{"action": "add", "name": "test-skill"}\n```'
    monkeypatch.setattr("src.teacher_escalation._call_teacher", fake_call_teacher)

    async def fail_direct_skill_save(*args, **kwargs):
        raise AssertionError("teacher output was persisted without approval")

    monkeypatch.setattr(
        "src.tool_implementations.do_manage_skills",
        fail_direct_skill_save,
    )

    events = []
    async for evt in teacher_escalation.run_teacher_inline(
        student_endpoint_url="http://student.local/v1",
        student_messages=[{"role": "user", "content": "test request"}],
        student_tool_events=[],
        student_reply="student reply",
        owner="alice",
        session_id="teacher-approval-session",
    ):
        events.append(evt)

    # The teacher takeover runs, but its cross-model skill output is sealed for
    # an explicit approval instead of being written directly.
    assert any("teacher_takeover" in evt for evt in events)
    assert any("tool_output" in evt for evt in events)
    approval_event = next(
        json.loads(evt[6:])
        for evt in events
        if evt.startswith("data: ")
        and "\"kind\": \"tool_approval\"" in evt
        and "\"type\": \"tool_output\"" in evt
    )
    approval = approval_event["ask_user"]
    final_metrics = next(
        json.loads(evt[6:])
        for evt in reversed(events)
        if evt.startswith("data: ") and '"type": "metrics"' in evt
    )
    persisted_approval = final_metrics["data"]["tool_events"][-1]
    assert persisted_approval["ask_user"] == approval
    assert persisted_approval["round"] == 2
    pending = tool_approval_store.peek(approval["approval_id"])
    assert pending is not None
    assert pending.tool_name == "manage_skills"
    assert json.loads(pending.content)["name"] == "test-skill"
    assert pending.external_untrusted_context_seen is True
    tool_approval_store.consume(
        pending.approval_id,
        decision="deny",
        owner="alice",
        session_id="teacher-approval-session",
    )
    assert not any("skill_saved" in evt for evt in events)


@pytest.mark.asyncio
async def test_teacher_approval_keeps_parent_authority_and_skips_skill_save(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: {
            "teacher_enabled": True,
            "teacher_model": "teacher-model",
        }.get(key, default),
    )
    monkeypatch.setattr(
        "src.ai_interaction._resolve_model",
        lambda spec, owner=None: (
            "http://teacher.local/v1",
            "teacher-model",
            {},
        ),
    )
    monkeypatch.setattr(
        "src.teacher_escalation.evaluate_turn_regex",
        lambda *args: ("failure", "student failed"),
    )
    captured = {}
    approval = {
        "kind": "tool_approval",
        "approval_id": "opaque-id",
        "question": "Allow this exact action once?",
    }

    async def fake_stream_agent_loop(*args, **kwargs):
        captured.update(kwargs)
        yield "data: " + json.dumps({
            "type": "tool_output",
            "tool": "bash",
            "output": "Waiting for an exact user approval.",
            "ask_user": approval,
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    async def fail_skill_distillation(*args, **kwargs):
        raise AssertionError("paused teacher trace was distilled into a skill")

    monkeypatch.setattr(
        "src.agent_loop.stream_agent_loop",
        fake_stream_agent_loop,
    )
    monkeypatch.setattr(
        "src.teacher_escalation._call_teacher",
        fail_skill_distillation,
    )
    active_document = object()
    active_email = {"uid": "email-1"}
    policy = object()

    events = []
    async for evt in teacher_escalation.run_teacher_inline(
        student_endpoint_url="http://student.local/v1",
        student_messages=[{"role": "user", "content": "test request"}],
        student_tool_events=[],
        student_reply="student reply",
        owner="alice",
        session_id="session-1",
        workspace="/workspace",
        disabled_tools={"web_fetch"},
        tool_policy=policy,
        active_document=active_document,
        active_email=active_email,
    ):
        events.append(evt)

    assert captured["session_id"] == "session-1"
    assert captured["workspace"] == "/workspace"
    assert captured["disabled_tools"] == {"web_fetch"}
    assert captured["tool_policy"] is policy
    assert captured["active_document"] is active_document
    assert captured["active_email"] == active_email
    assert any("opaque-id" in event for event in events)
    assert not any("skill_saved" in event for event in events)


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
