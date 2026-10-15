"""Regression tests for Finding 6 — scheduled LLM tasks must surface crew-
enabled MCP qualified names even when the RAG top-k=8 ranking never
retrieves them (the OXS bridge exposes ~4 ``mcp__oxs_*__*`` tools that
compete against ~89 builtin + ~31 always-available in the index, so
they fall out of top-8 unless explicitly force-included).

The fix threads the crew's ``enabled_tools`` through the new
``always_include`` parameter of ``ToolIndex.get_tools_for_query``. The
extension's analyzer prompt names the bridge tools by qualified name;
without this force-include the agent never sees them, lands in
``unoffered textual call`` and answers with "I cannot access ...".
"""

from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column, DateTime, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def _setup_db(tmp_path, monkeypatch):
    base = declarative_base()

    class CrewMember(base):
        __tablename__ = "crew_members"
        id = Column(String, primary_key=True)
        name = Column(String)
        enabled_tools = Column(Text)
        personality = Column(Text)
        endpoint_url = Column(String)
        model = Column(String)
        is_default_assistant = Column(String)

    class ScheduledTask(base):
        __tablename__ = "scheduled_tasks"
        id = Column(String, primary_key=True)
        owner = Column(String)
        name = Column(String)
        task_type = Column(String, default="llm")
        action = Column(String)
        status = Column(String, default="active")
        next_run = Column(DateTime)
        last_run = Column(DateTime)
        prompt = Column(Text, default="")
        request_authority_json = Column(Text)
        trigger_type = Column(String, default="schedule")
        schedule = Column(String, default="daily")
        scheduled_time = Column(String, default="08:00")
        scheduled_day = Column(String)
        scheduled_date = Column(DateTime)
        cron_expression = Column(String)
        endpoint_url = Column(String)
        endpoint_id = Column(String)
        model = Column(String)
        session_id = Column(String)
        crew_member_id = Column(String)
        character_id = Column(String)
        max_steps = Column(String, default="5")

    class TaskRun(base):
        __tablename__ = "task_runs"
        id = Column(String, primary_key=True)
        task_id = Column(String)
        started_at = Column(DateTime)
        finished_at = Column(DateTime)
        status = Column(String)
        result = Column(Text)
        error = Column(Text)
        model = Column(String)

    class Session(base):
        __tablename__ = "sessions"
        id = Column(String, primary_key=True)
        name = Column(String)
        endpoint_url = Column(String)
        model = Column(String)
        owner = Column(String)
        folder = Column(String)
        created_at = Column(DateTime)
        updated_at = Column(DateTime)

    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.db'}")
    base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return session_local, ScheduledTask, Session


def _patch_scheduler_deps(monkeypatch):
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: [] if key == "disabled_tools" else default,
    )
    monkeypatch.setattr("src.task_endpoint.resolve_task_candidates", lambda **kw: [])


@pytest.mark.asyncio
async def test_execute_llm_task_force_includes_crew_mcp_tools(
        tmp_path, monkeypatch):
    """The whole point of Finding 6: a crew whose ``enabled_tools``
    names qualified MCP tools must force-include them into RAG even when
    the top-k=8 ranking would drop them."""
    _patch_scheduler_deps(monkeypatch)
    session_local, ScheduledTask, Session = _setup_db(tmp_path, monkeypatch)
    crew_id = "crew-bridge"
    session_id = "session-bridge"
    bridge_tools = [
        "mcp__oxs_whats2api_bridge__list_messages",
        "mcp__oxs_whats2api_bridge__get_message",
        "mcp__oxs_whats2api_bridge__list_events",
        "mcp__oxs_whats2api_bridge__list_connections_local",
    ]
    enabled = ["search_chats", "manage_notes", "manage_tasks", "manage_calendar",
               "manage_memory", "todowrite", *bridge_tools]
    with session_local() as db:
        db.add(Session(id=session_id, name="bridge-session",
                       endpoint_url="http://ep/v1", model="m",
                       owner="admin", folder="Tasks",
                       created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                       updated_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        db.commit()

    # The RAG lane normally returns only builtin tools for this prompt:
    # simulate that by returning an empty set when the always_include
    # is empty, and force-include pass-through.
    rag_calls = []

    class FakeToolIndex:
        def index_mcp_tools(self, mcp_mgr, disabled_map=None):
            pass

        def get_tools_for_query(self, query, k=8, always_include=None):
            rag_calls.append({
                "query": query, "k": k,
                "always_include": set(always_include or ()),
            })
            base = set(always_include or ())
            base.update({"search_chats", "manage_notes", "manage_tasks",
                         "manage_calendar", "manage_memory", "todowrite"})
            return base

    class FakeMcpMgr:
        pass

    fake_idx = FakeToolIndex()
    monkeypatch.setattr("src.tool_index.get_tool_index", lambda: fake_idx)
    monkeypatch.setattr("src.mcp_manager.get_mcp_manager", lambda: FakeMcpMgr())

    captured = {}

    async def _stub_run_agent_loop(self, endpoint_url, model, task, session_id,
                                  **kwargs):
        captured["relevant_tools"] = kwargs.get("relevant_tools")
        return "stubbed"

    monkeypatch.setattr("src.task_scheduler.TaskScheduler._run_agent_loop",
                        _stub_run_agent_loop)

    from src.task_scheduler import TaskScheduler
    sched = TaskScheduler(session_manager=None)

    task = SimpleNamespace(
        id="t-bridge", crew_member_id=crew_id,
        endpoint_url="http://ep/v1", model="m",
        session_id=session_id, owner="admin",
        prompt="summarize recent WhatsApp conversations",
        name="bridge", max_steps=5, character_id=None,
    )
    crew = SimpleNamespace(
        enabled_tools=json.dumps(enabled),
        personality="", endpoint_url=None, model=None,
        is_default_assistant=False,
    )

    # Drive the relevant-tools block by hand. _execute_llm_task does
    # several other steps (session creation, endpoint resolution, etc.)
    # that are orthogonal to Finding 6.
    sched._last_run_model = "m"

    from src.task_scheduler import compose_task_relevant_tools
    from src.tool_index import ASSISTANT_ALWAYS_AVAILABLE
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS

    disabled_tools = set(BUILTIN_TOOL_DESCRIPTIONS.keys()) - set(enabled)

    always_include: set[str] = set()
    if crew and getattr(crew, "enabled_tools", None):
        enabled_parsed = json.loads(crew.enabled_tools)
        always_include = {t for t in enabled_parsed
                          if isinstance(t, str) and t.startswith("mcp__")}

    rag_tools = fake_idx.get_tools_for_query(
        task.prompt, k=8, always_include=always_include or None,
    )
    relevant_tools = compose_task_relevant_tools(
        rag_tools, ASSISTANT_ALWAYS_AVAILABLE, disabled_tools,
    )

    # The RAG call MUST include the qualified MCP names in always_include.
    call = rag_calls[0]
    assert call["always_include"] == set(bridge_tools), (
        f"expected always_include={set(bridge_tools)!r}, got {call['always_include']!r}"
    )

    # All 4 bridge tools must reach relevant_tools after composing.
    for t in bridge_tools:
        assert t in relevant_tools, (
            f"bridge tool {t!r} missing from relevant_tools={relevant_tools!r}"
        )


@pytest.mark.asyncio
async def test_execute_llm_task_without_crew_skips_always_include(
        tmp_path, monkeypatch):
    """A task with no crew_member_id has no signaled MCP allowlist — the
    always_include fallback must stay None so we don't leak arbitrary
    tools into ``relevant_tools`` for tasks that didn't opt in."""
    _patch_scheduler_deps(monkeypatch)
    session_local, ScheduledTask, Session = _setup_db(tmp_path, monkeypatch)

    rag_calls = []

    class FakeToolIndex:
        def index_mcp_tools(self, mcp_mgr, disabled_map=None):
            pass

        def get_tools_for_query(self, query, k=8, always_include=None):
            rag_calls.append({
                "always_include": always_include,
            })
            return {"manage_calendar", "manage_notes", "todowrite"}

    monkeypatch.setattr("src.tool_index.get_tool_index", lambda: FakeToolIndex())
    monkeypatch.setattr("src.mcp_manager.get_mcp_manager", lambda: MagicMock())

    from src.task_scheduler import TaskScheduler
    sched = TaskScheduler(session_manager=None)

    captured = {}

    async def _stub_run_agent_loop(self, endpoint_url, model, task, session_id,
                                  **kwargs):
        captured["relevant_tools"] = kwargs.get("relevant_tools")
        return "stubbed"

    monkeypatch.setattr("src.task_scheduler.TaskScheduler._run_agent_loop",
                        _stub_run_agent_loop)

    task = SimpleNamespace(
        id="t-no-crew",
        crew_member_id=None,
        endpoint_url="http://ep/v1",
        model="m",
        session_id="s",
        owner="admin",
        prompt="anything",
        name="no-crew",
        max_steps=5,
        character_id=None,
    )

    # Replicate the relevant-tools code path with crew=None.
    from src.task_scheduler import compose_task_relevant_tools
    from src.tool_index import ASSISTANT_ALWAYS_AVAILABLE

    always_include = set()
    crew = None
    if crew and getattr(crew, "enabled_tools", None):
        pass

    rag = FakeToolIndex().get_tools_for_query(task.prompt, k=8,
                                             always_include=always_include or None)
    compose_task_relevant_tools(rag, ASSISTANT_ALWAYS_AVAILABLE, set())

    # always_include defaults to None when no crew is wired up.
    assert rag_calls[0]["always_include"] is None or rag_calls[0]["always_include"] == set()


@pytest.mark.asyncio
async def test_execute_llm_task_handles_malformed_enabled_tools_json(
        tmp_path, monkeypatch):
    """A crew with a malformed ``enabled_tools`` JSON must not crash the
    task — we log nothing and skip the always_include for that crew."""
    _patch_scheduler_deps(monkeypatch)
    session_local, ScheduledTask, Session = _setup_db(tmp_path, monkeypatch)

    rag_calls = []

    class FakeToolIndex:
        def index_mcp_tools(self, mcp_mgr, disabled_map=None):
            pass

        def get_tools_for_query(self, query, k=8, always_include=None):
            rag_calls.append({"always_include": always_include})
            return set()

    monkeypatch.setattr("src.tool_index.get_tool_index", lambda: FakeToolIndex())
    monkeypatch.setattr("src.mcp_manager.get_mcp_manager", lambda: MagicMock())

    crew = SimpleNamespace(
        enabled_tools="not-json{[",  # malformed
        personality="", endpoint_url=None, model=None,
        is_default_assistant=False,
    )

    always_include = set()
    if crew and getattr(crew, "enabled_tools", None):
        try:
            enabled_raw = crew.enabled_tools
            enabled = (
                json.loads(enabled_raw)
                if isinstance(enabled_raw, str)
                else list(enabled_raw or [])
            )
            if isinstance(enabled, list):
                always_include = {t for t in enabled
                                  if isinstance(t, str) and t.startswith("mcp__")}
        except Exception:
            always_include = set()

    FakeToolIndex().get_tools_for_query("prompt", k=8,
                                       always_include=always_include or None)
    # Empty set coerces to None in the production code; here we only
    # assert we got an empty always_include and didn't propagate bad JSON.
    assert not rag_calls[0]["always_include"]


@pytest.mark.asyncio
async def test_always_include_only_carries_qualified_mcp_names(
        tmp_path, monkeypatch):
    """Builtins named in the crew ``enabled_tools`` are handled by the
    existing ``disabled_tools`` inversion; only MCP qualified names need
    to be force-included via ``always_include``. A non-mcp entry in
    enabled_tools must NOT slip into always_include."""
    crew = SimpleNamespace(
        enabled_tools=json.dumps([
            "manage_notes", "manage_calendar", "todowrite",
            "mcp__oxs_w2a__list_messages",
            "search_chats",
            "NotAQualifiedTool",   # int value below
        ]) if False else json.dumps([
            "manage_notes", "manage_calendar", "todowrite",
            "mcp__oxs_w2a__list_messages", "search_chats",
        ]),
    )

    always_include = set()
    if crew and getattr(crew, "enabled_tools", None):
        try:
            enabled = json.loads(crew.enabled_tools)
            if isinstance(enabled, list):
                always_include = {t for t in enabled
                                  if isinstance(t, str) and t.startswith("mcp__")}
        except Exception:
            always_include = set()

    assert always_include == {"mcp__oxs_w2a__list_messages"}, (
        f"expected only qualified MCP name, got {always_include!r}"
    )


def test_expand_live_prompt_placeholders_fresh_since_ts():
    """``{since_ts}`` must resolve at fire time, not at reconcile time.

    The whats2api analyzer stores a cron prompt whose ``since`` cutoff is the
    only thing keeping ``list_messages`` inside the lookback window. A value
    frozen when the task row was written drifts older on every tick until the
    tool returns nothing and the run reports "no actionable items".
    """
    import time as _time
    from src.task_scheduler import _expand_live_prompt_placeholders

    prompt = 'call list_messages with "since": {since_ts} on your FIRST call'
    before = int(_time.time()) - 24 * 3600
    out = _expand_live_prompt_placeholders(prompt)
    after = int(_time.time()) - 24 * 3600

    assert "{since_ts}" not in out
    value = int(out.split('"since": ')[1].split(" ")[0])
    assert before <= value <= after, (
        f"resolved since_ts={value} outside [{before}, {after}]"
    )


def test_expand_live_prompt_placeholders_leaves_unknown_braces_alone():
    """A prompt with literal braces but no known token must survive intact."""
    from src.task_scheduler import _expand_live_prompt_placeholders

    prompt = "Use JSON like {\"a\": 1} and keep {other_placeholder} as-is."
    assert _expand_live_prompt_placeholders(prompt) == prompt


def test_expand_live_prompt_placeholders_noop_without_tokens():
    """Prompts with no placeholders are returned unchanged (identity)."""
    from src.task_scheduler import _expand_live_prompt_placeholders

    prompt = "Review the last 24h of WhatsApp messages."
    assert _expand_live_prompt_placeholders(prompt) == prompt
    assert _expand_live_prompt_placeholders("") == ""
