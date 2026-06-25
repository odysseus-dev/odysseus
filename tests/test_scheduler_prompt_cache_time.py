"""Regression tests for #4850 — scheduled-task system prompt must not embed
a minute-level timestamp that busts the Anthropic prompt cache.

Three focused tests:
1. End-to-end: system prompt is clean; message ordering is [system, datetime
   user-context, task user-prompt] through the real _run_agent_loop.
2. Fallback: same ordering when the agent loop raises and task_llm_call_async
   is used directly.
3. Helper: current_datetime_context_message_for_tz() renders the correct local
   time for an explicit IANA timezone, and falls back to UTC for None or invalid.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace


def _make_task(prompt="run the digest"):
    return SimpleNamespace(
        crew_member_id=None, endpoint_url="http://ep/v1", model="m",
        session_id="s", owner="admin", prompt=prompt,
        name="job", max_steps=5, character_id=None,
    )


def _patch_scheduler_deps(monkeypatch):
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: [] if key == "disabled_tools" else default,
    )
    monkeypatch.setattr("src.tool_index.get_tool_index", lambda: None)


# ---------------------------------------------------------------------------
# Test 1 — end-to-end: system is clean; agent-loop message ordering is correct
# ---------------------------------------------------------------------------

async def test_scheduler_agent_loop_path(monkeypatch):
    """Drive _execute_llm_task end-to-end (real _run_agent_loop, stubbed
    stream_agent_loop).  Asserts:
      - system message contains no 'Current time:' prefix
      - messages[1] is a user-role date/time context block
      - messages[2] is the task prompt
    """
    _patch_scheduler_deps(monkeypatch)

    captured = {}

    async def _stub_stream(**kwargs):
        captured["messages"] = list(kwargs.get("messages", []))
        return
        yield  # async generator

    monkeypatch.setattr("src.agent_loop.stream_agent_loop", _stub_stream)
    monkeypatch.setattr("src.task_endpoint.resolve_task_candidates", lambda **kw: [])

    from src.task_scheduler import TaskScheduler
    await TaskScheduler(session_manager=None)._execute_llm_task(_make_task(), db=None)

    msgs = captured.get("messages", [])
    assert len(msgs) == 3, f"expected 3 messages, got {len(msgs)}"
    assert msgs[0]["role"] == "system"
    assert "Current time:" not in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert "## Current date and time" in msgs[1]["content"]
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"] == "run the digest"


# ---------------------------------------------------------------------------
# Test 2 — fallback path receives the same datetime context
# ---------------------------------------------------------------------------

async def test_scheduler_fallback_path(monkeypatch):
    """When _run_agent_loop raises, task_llm_call_async must receive
    [system, datetime user-context, task user-prompt] — the same ordering."""
    _patch_scheduler_deps(monkeypatch)

    captured = {}

    async def _fail(*args, **kwargs):
        raise RuntimeError("simulated failure")

    async def _capture_call(messages, **kw):
        captured["messages"] = list(messages)
        return "fallback"

    import src.task_endpoint as _te
    monkeypatch.setattr(_te, "task_llm_call_async", _capture_call)

    from src.task_scheduler import TaskScheduler
    sched = TaskScheduler(session_manager=None)
    sched._run_agent_loop = _fail
    await sched._execute_llm_task(_make_task(prompt="send the digest"), db=None)

    msgs = captured.get("messages", [])
    assert len(msgs) == 3, f"expected 3 messages, got {len(msgs)}"
    assert msgs[0]["role"] == "system"
    assert "Current time:" not in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert "## Current date and time" in msgs[1]["content"]
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"] == "send the digest"


# ---------------------------------------------------------------------------
# Test 3 — current_datetime_context_message_for_tz() timezone resolution
# ---------------------------------------------------------------------------

def test_datetime_context_message_for_tz(monkeypatch):
    """Three cases with a fixed UTC timestamp (2026-06-25 18:00 UTC):
      - explicit 'America/New_York' → 2:00 PM EDT, UTC-04:00
      - None                        → UTC fallback: 6:00 PM, UTC+00:00
      - invalid IANA name           → UTC fallback: same
    """
    from src.user_time import current_datetime_context_message_for_tz

    fixed = datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)

    # Explicit IANA timezone
    msg = current_datetime_context_message_for_tz("America/New_York", fixed)
    assert msg["role"] == "user"
    assert "America/New_York" in msg["content"]
    assert "UTC-04:00" in msg["content"]
    assert "2:00 PM" in msg["content"]

    # None → UTC (preserves old scheduler behaviour for tasks without a crew tz)
    msg = current_datetime_context_message_for_tz(None, fixed)
    assert "UTC+00:00" in msg["content"]
    assert "6:00 PM" in msg["content"]

    # Invalid IANA name → UTC fallback, no exception raised
    msg = current_datetime_context_message_for_tz("Not/A_Real_Zone", fixed)
    assert "UTC+00:00" in msg["content"]
    assert "6:00 PM" in msg["content"]
