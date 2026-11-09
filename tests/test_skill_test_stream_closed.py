"""Skill test runs must close the agent stream in their own task (#6635).

When the agent asks for an exact approval, the skill test pauses by leaving its
`async for` over `stream_agent_loop`. The abandoned generator was later closed
by asyncio's finalizer in another task, so the ContextVar resets in its cleanup
(request_authority, turn_contract, ...) raised "Token ... was created in a
different Context". The stream must be closed before the run returns.
"""
import asyncio
import json
from contextvars import ContextVar

import routes.skills_routes as skills_routes

_BOUND: ContextVar = ContextVar("skill_test_bound", default=None)

_APPROVAL = {"kind": "tool_approval", "approval_id": "approval-1", "question": "Allow?"}


def _fake_stream(events, closed):
    async def stream_agent_loop(*args, **kwargs):
        token = _BOUND.set("bound")
        try:
            for event in events:
                yield "data: " + json.dumps(event) + "\n\n"
            yield "data: [DONE]\n\n"
        finally:
            # Raises ValueError when run from another task's context.
            _BOUND.reset(token)
            closed.append(asyncio.current_task())
    return stream_agent_loop


async def test_paused_skill_test_closes_the_agent_stream_in_its_own_task(monkeypatch):
    closed = []
    events = [{"type": "tool_output", "output": "blocked", "ask_user": _APPROVAL},
              {"delta": "never reached"}]
    monkeypatch.setattr("src.agent_loop.stream_agent_loop", _fake_stream(events, closed))
    key = ("alice", "demo")
    skills_routes._skill_test_jobs[key] = {"log": [], "status": "running"}
    try:
        await skills_routes._run_skill_test_job(
            key, "demo", "# skill", "task", "http://model.test", "m", {}, "alice")
        job = skills_routes._skill_test_jobs[key]
        assert job["status"] == "awaiting_approval"
        assert closed == [asyncio.current_task()]
    finally:
        skills_routes._skill_test_jobs.pop(key, None)


async def test_audit_arm_closes_the_agent_stream_when_it_stops_for_approval(monkeypatch):
    closed = []
    events = [{"type": "tool_output", "output": "blocked", "ask_user": _APPROVAL},
              {"delta": "never reached"}]
    monkeypatch.setattr("src.agent_loop.stream_agent_loop", _fake_stream(events, closed))

    _, _, approval = await skills_routes._run_skill_audit_arm(
        [{"role": "user", "content": "task"}], "http://model.test", "m", {}, "alice")

    assert approval == _APPROVAL
    assert closed == [asyncio.current_task()]
