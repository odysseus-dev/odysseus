"""Parallel autonomous agents — the spawn_agents tool + the Parallel UI toggle.

spawn_agents fans out up to N child agent loops (each a full stream_agent_loop
with tools) via asyncio.gather, inheriting the parent session's model, and
aggregates their results for the calling agent to summarize. It is gated by the
Parallel composer toggle (base-available, disabled unless the toggle is on).

Functional tests run the real orchestration with a fake stream_agent_loop so no
model is needed; source-level guards verify the 6 wiring sites + the UI.
"""

import asyncio
from pathlib import Path

import src.ai_interaction as ai
import src.agent_loop as al

_ROOT = Path(__file__).resolve().parent.parent


# ── Fakes ───────────────────────────────────────────────────────


class _FakeSession:
    endpoint_url = "http://endpoint/v1"
    model = "test-model"
    headers = {"Authorization": "Bearer x"}


class _FakeMgr:
    def get_session(self, sid):
        return _FakeSession()


def _fake_stream_factory(text="branch output"):
    async def _fake_stream(*args, **kwargs):
        # Mirror the SSE shape stream_agent_loop yields.
        yield 'data: {"type": "tool_start", "tool": "bash"}\n\n'
        yield 'data: {"delta": "%s"}\n\n' % text
        yield 'data: {"thinking": true, "delta": "ignore me"}\n\n'
        yield "data: [DONE]\n\n"
    return _fake_stream


# ── Functional: the orchestration ───────────────────────────────


def test_spawn_agents_runs_all_branches(monkeypatch):
    monkeypatch.setattr(ai, "_session_manager", _FakeMgr())
    monkeypatch.setattr(al, "stream_agent_loop", _fake_stream_factory("done"))
    res = asyncio.run(ai.do_spawn_agents('{"tasks": ["a", "b", "c"]}', session_id="s1", owner="u"))
    assert res["count"] == 3
    assert res["succeeded"] == 3
    assert all(b["output"] == "done" for b in res["branches"])  # thinking delta excluded
    assert "Parallel agents (3/3 succeeded)" in res["results"]


def test_spawn_agents_parses_lines(monkeypatch):
    monkeypatch.setattr(ai, "_session_manager", _FakeMgr())
    monkeypatch.setattr(al, "stream_agent_loop", _fake_stream_factory())
    res = asyncio.run(ai.do_spawn_agents("task one\ntask two", session_id="s1"))
    assert res["count"] == 2


def test_spawn_agents_caps_count(monkeypatch):
    monkeypatch.setattr(ai, "_session_manager", _FakeMgr())
    monkeypatch.setattr(al, "stream_agent_loop", _fake_stream_factory())
    res = asyncio.run(ai.do_spawn_agents(
        '{"tasks": ["1", "2", "3", "4", "5", "6"]}', session_id="s1"))
    assert "error" in res
    assert str(ai.MAX_PARALLEL_AGENTS) in res["error"]


def test_spawn_agents_empty_is_error(monkeypatch):
    monkeypatch.setattr(ai, "_session_manager", _FakeMgr())
    res = asyncio.run(ai.do_spawn_agents("   ", session_id="s1"))
    assert "error" in res


def test_spawn_agents_requires_session(monkeypatch):
    monkeypatch.setattr(ai, "_session_manager", _FakeMgr())
    res = asyncio.run(ai.do_spawn_agents('{"tasks": ["a"]}', session_id=None))
    assert "error" in res


def test_spawn_agents_one_branch_failure_isolated(monkeypatch):
    monkeypatch.setattr(ai, "_session_manager", _FakeMgr())

    def _factory():
        calls = {"n": 0}

        async def _stream(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            yield 'data: {"delta": "ok"}\n\n'
            yield "data: [DONE]\n\n"
        return _stream

    monkeypatch.setattr(al, "stream_agent_loop", _factory())
    res = asyncio.run(ai.do_spawn_agents('{"tasks": ["a", "b"]}', session_id="s1"))
    assert res["count"] == 2
    # One failed, one succeeded — the failure must not poison the other.
    assert res["succeeded"] == 1
    assert any(b["error"] for b in res["branches"])


def test_child_disabled_tools_block_docs_and_recursion():
    d = ai._CHILD_DISABLED_TOOLS
    assert "spawn_agents" in d   # no recursive fan-out
    assert "ask_user" in d       # children can't pause for input
    assert "create_document" in d and "edit_document" in d  # doc-state race


def test_drain_agent_stream_collects_deltas():
    async def _s():
        yield 'data: {"delta": "Hello "}\n\n'
        yield 'data: {"type": "tool_output"}\n\n'
        yield 'data: {"delta": "world"}\n\n'
        yield "data: [DONE]\n\n"
    out = asyncio.run(ai._drain_agent_stream(_s()))
    assert out == "Hello world"


# ── Source-level guards: the 6 wiring sites ─────────────────────


def test_wired_in_all_sites():
    def has(rel, needle):
        return needle in (_ROOT / rel).read_text(encoding="utf-8")

    assert has("src/tool_schemas.py", '"name": "spawn_agents"'), "missing function schema"
    assert has("src/tool_schemas.py", '{"tasks": args.get("tasks", [])}'), "missing args packing"
    assert has("src/tool_execution.py", '"spawn_agents"'), "not routed to dispatch_ai_tool"
    assert has("src/ai_interaction.py", 'elif tool == "spawn_agents":'), "no dispatch branch"
    assert has("src/agent_tools/__init__.py", '"spawn_agents"'), "not in TOOL_TAGS"
    assert has("src/tool_policy.py", '"spawn_agents"'), "not in _COMMON_TOOL_NAMES"
    assert has("src/tool_index.py", '"spawn_agents"'), "not in tool_index (desc/ALWAYS_AVAILABLE)"


def test_chat_route_gates_spawn_agents():
    src = (_ROOT / "routes" / "chat_routes.py").read_text(encoding="utf-8")
    assert "parallel_mode" in src, "chat route must parse parallel_mode"
    assert 'disabled_tools.add("spawn_agents")' in src, "must disable spawn_agents when off"
    assert "if not parallel_mode:" in src, "gate must be off-by-default"


# ── UI guards ───────────────────────────────────────────────────


def test_ui_has_parallel_toggle():
    html = (_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "parallel-toggle" in html
    js = (_ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    assert "'parallel'" in js or '"parallel"' in js, "chat.js must send the parallel flag"
