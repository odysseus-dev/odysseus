"""Agents dashboard API: owner-scoped overview, stop, steer, launch."""
import asyncio
import json
from types import SimpleNamespace

import pytest

import src.agent_loop as agent_loop
from routes import agents_routes as ar
from src import agent_activity as act
from src import agent_control, agent_runs, constants, tool_approvals


class _Sess:
    def __init__(self, sid, name, owner="alice", model="m"):
        self.id, self.name, self.owner, self.model, self.archived = sid, name, owner, model, False
        self.endpoint_url, self.headers, self.context_length, self.history = "http://x", {}, 0, []

    def add_message(self, m):
        self.history.append(m)


class _Mgr:
    def __init__(self, sessions):
        self.sessions = {s.id: s for s in sessions}

    def get_sessions_for_user(self, user):
        return {k: v for k, v in self.sessions.items() if v.owner == user}

    def get_session(self, sid):
        return self.sessions.get(sid)

    def create_session(self, session_id, name, endpoint_url, model, rag, owner):
        s = _Sess(session_id, name, owner=owner, model=model)
        s.endpoint_url = endpoint_url
        self.sessions[session_id] = s
        return s

    def save_sessions(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    act._reset_for_tests(); tool_approvals._reset_for_tests()
    agent_runs._RUNS.clear(); agent_runs._EXTERNAL.clear(); agent_runs._FINISHED.clear()
    agent_control._STEER.clear()
    mgr = _Mgr([_Sess("a1", "Alice chat"), _Sess("b1", "Bob chat", owner="bob")])
    monkeypatch.setattr(ar, "effective_user", lambda request: "alice")
    import src.ai_interaction as ai
    monkeypatch.setattr(ai, "get_session_manager", lambda: mgr)
    import core.database as db
    monkeypatch.setattr(db, "get_session_settings", lambda sid: {})
    monkeypatch.setattr(db, "update_session_settings", lambda sid, patch: patch)
    router = ar.setup_agents_routes(mgr)
    eps = {(m, r.path): r.endpoint for r in router.routes for m in r.methods}
    yield mgr, eps
    act._reset_for_tests(); tool_approvals._reset_for_tests(); agent_control._STEER.clear()


def _req(body=None):
    async def _json():
        return body
    return SimpleNamespace(json=_json)


async def test_overview_is_owner_scoped_and_grouped(env):
    mgr, eps = env
    with agent_runs.track_external("a1", source="subagent", owner="alice"):
        act.run_started("a1", "session", "Sub-agent · worker: dig", owner="alice", data={"target_session": "x"})
        act.run_started("b1", "session", "Bob's worker", owner="bob")
        tool_approvals.request("a1", "bash", "git push", "pushes to a remote")
        out = await eps[("GET", "/api/agents/overview")](_req())
    assert [r["session_id"] for r in out["rows"]] == ["a1"]
    row = out["rows"][0]
    assert row["status"] == "waiting_approval" and row["pending_approvals"] == 1 and row["children_running"] == 1
    assert out["totals"]["waiting_approval"] == 1 and out["totals"]["workers_running"] == 1
    approvals = await eps[("GET", "/api/agents/approvals")](_req())
    assert [a["session_id"] for a in approvals["approvals"]] == ["a1"]


async def test_steer_requires_a_running_chat_and_lands_in_the_next_round(env, monkeypatch):
    mgr, eps = env
    steer = eps[("POST", "/api/agents/sessions/{session_id}/steer")]
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await steer(_req({"text": "focus on tests"}), session_id="a1")
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException):
        await steer(_req({"text": "x"}), session_id="b1")

    with agent_runs.track_external("a1", source="bg_job", owner="alice"):
        out = await steer(_req({"text": "focus on tests"}), session_id="a1")
    assert out["queued"] and agent_control.pending_steer("a1")[0]["text"] == "focus on tests"

    # The loop drains it at the start of a round as a user message.
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *a, **k: 10, raising=False)
    seen = {}

    async def fake_stream(_candidates, messages, **kwargs):
        seen["messages"] = list(messages)
        yield 'data: {"delta": "ok"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream, raising=False)
    chunks = [c async for c in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o", [{"role": "user", "content": "go"}],
        relevant_tools={"read_file"}, session_id="a1", _is_teacher_run=True)]
    assert any('"steer_applied"' in c for c in chunks)
    assert seen["messages"][-1]["content"].endswith("focus on tests")
    assert agent_control.pending_steer("a1") == []


async def test_launch_starts_a_worker_chat_and_stop_ends_it(env, monkeypatch):
    mgr, eps = env
    started = asyncio.Event()

    async def slow_loop(url, model, messages, **kwargs):
        yield 'data: {"delta": "partial"}\n\n'
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(agent_loop, "stream_agent_loop", slow_loop)
    import src.agent_profiles as ap
    monkeypatch.setattr(ap, "load_profiles", lambda: ap.validate_profiles([{"name": "researcher", "model": "cheap"}]))
    import src.agent_tools.session_tools as st_mod
    monkeypatch.setattr(st_mod, "_resolve_model", lambda spec, owner=None: ("http://cheap", spec, {}))

    out = await eps[("POST", "/api/agents/launch")](_req({"task": "compare pricing", "profile": "researcher", "parent_session": "a1"}))
    child = mgr.sessions[out["session_id"]]
    assert child.owner == "alice" and child.model == "cheap" and child.name.startswith("↳ researcher")
    await started.wait()
    ov = await eps[("GET", "/api/agents/overview")](_req())
    statuses = {r["session_id"]: r["status"] for r in ov["rows"]}
    assert statuses[child.id] == "running"

    res = await eps[("POST", "/api/agents/runs/{run_id}/stop")](_req(), run_id=out["run_id"])
    assert res == {"stopped": True, "how": "headless"}
    await asyncio.wait_for(agent_control._WORKERS.get(out["run_id"]) or asyncio.sleep(0), 5)
    assert act.get_run(out["run_id"])["status"] == "cancelled"
    assert child.history[-1].content.startswith("partial")

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await eps[("POST", "/api/agents/launch")](_req({"task": "x", "parent_session": "b1"}))
    assert exc.value.status_code == 404
