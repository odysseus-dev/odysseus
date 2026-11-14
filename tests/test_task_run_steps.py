"""Scheduled agent runs persist their tool calls to ``task_runs.steps`` (#5755)."""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import pytest

import core.database as database
import src.endpoint_resolver as resolver
from src.agent_runtime.authority import seal_task_authority
from src.task_scheduler import TaskScheduler, _task_run_step
from tests.helpers.database import disposable_database


BASE = "https://models.example.test/v1"


def _event(payload):
    return f"data: {json.dumps(payload)}\n\n"


def _step(event, current_round=None):
    step = _task_run_step(event, current_round)
    assert step.pop("ts").endswith("Z")
    return step


def test_step_keeps_execution_facts_and_drops_arguments_and_output():
    # Shape emitted by the compact preview loop (src/clean_agent_preview.py).
    assert _step({
        "type": "tool_output", "tool": "web_search", "command": '{"query": "secret plans"}',
        "output": "ten results", "exit_code": 0, "error": False,
        "execution_attempted": True, "blocked": False, "desc": "search", "round": 2,
    }) == {"round": 2, "tool": "web_search", "exit_code": 0, "error": False, "executed": True}


@pytest.mark.parametrize("event, expected", [
    # The main loop (src/agent_loop.py) sends no round or error key: the round
    # comes from the preceding tool_start and the outcome from the exit code.
    ({"tool": "bash", "exit_code": 2, "execution_attempted": True, "blocked": False},
     {"round": 4, "tool": "bash", "exit_code": 2, "error": True, "executed": True}),
    ({"tool": "bash", "exit_code": 0, "execution_attempted": True, "blocked": False},
     {"round": 4, "tool": "bash", "exit_code": 0, "error": False, "executed": True}),
    # No exit code and no error flag: the outcome is unknown, not a success.
    ({"tool": "research", "exit_code": None, "execution_attempted": True, "blocked": False},
     {"round": 4, "tool": "research", "exit_code": None, "error": None, "executed": True}),
    ({"tool": "bash", "exit_code": None, "execution_attempted": False, "blocked": True},
     {"round": 4, "tool": "bash", "exit_code": None, "error": True, "executed": False}),
    ({"tool": "bash", "exit_code": 1, "error": True, "execution_attempted": False, "round": 3},
     {"round": 3, "tool": "bash", "exit_code": 1, "error": True, "executed": False}),
    ({}, {"round": 4, "tool": "?", "exit_code": None, "error": None, "executed": True}),
])
def test_step_reads_each_emitter_shape(event, expected):
    assert _step(event, current_round=4) == expected


def test_step_round_is_unknown_without_any_announcement():
    assert _step({"tool": "update_document", "output": "saved"})["round"] is None


@pytest.fixture
def run_task(tmp_path, monkeypatch):
    """Drive one stored task through the real scheduler and return its run row."""
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(database, "SessionLocal", factory)
        monkeypatch.setattr(resolver, "resolve_url", lambda url: url)
        monkeypatch.setattr("src.interactive_gate.wait_for_interactive_quiet", AsyncMock())
        monkeypatch.setattr("src.interactive_gate.has_foreground_activity", lambda: False)
        monkeypatch.setattr("src.task_endpoint.resolve_task_candidates", lambda **kwargs: [])
        monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: default)
        monkeypatch.setattr("src.task_scheduler._resolve_task_timezone", lambda db, task: "UTC")
        monkeypatch.setattr("src.tool_index.get_tool_index", lambda: None)
        monkeypatch.setattr("src.event_bus.fire_event", lambda *args: None)
        monkeypatch.setattr(TaskScheduler, "_deliver_task_result", AsyncMock())
        monkeypatch.setattr(TaskScheduler, "_log_to_assistant", lambda *args, **kwargs: None)

        async def run(events, *, failure=None, **task_fields):
            async def stream(**kwargs):
                for event in events:
                    yield _event(event)
                if failure is not None:
                    raise failure
                yield "data: [DONE]\n\n"

            monkeypatch.setattr("src.agent_loop.stream_agent_loop", stream)
            if failure is not None:
                monkeypatch.setattr("src.task_endpoint.task_llm_call_async",
                                    AsyncMock(side_effect=failure))
            task_id = uuid.uuid4().hex
            fields = {"task_type": "llm", "action": None, "prompt": "Research this topic",
                      "endpoint_url": BASE, "model": "model", **task_fields}
            with factory() as db:
                if not db.query(database.ModelEndpoint).first():
                    db.add(database.ModelEndpoint(id="endpoint", name="Endpoint", owner="alice",
                                                 base_url=BASE, api_key="key", is_enabled=True))
                db.add(database.ScheduledTask(
                    id=task_id, owner="alice", name="Research task", status="active",
                    trigger_type="event",
                    request_authority_json=seal_task_authority(
                        fields["prompt"], fields["task_type"], fields["action"], owner="alice"),
                    **fields))
                db.commit()
            await TaskScheduler(session_manager=None)._execute_task(task_id, bypass_model_slot=True)
            with factory() as db:
                row = db.query(database.TaskRun).filter(database.TaskRun.task_id == task_id).one()
                return row.status, row.steps

        yield run


async def test_agent_run_persists_one_step_per_tool_call(run_task):
    status, steps = await run_task([
        {"type": "tool_start", "tool": "web_search", "command": '{"query": "odysseus"}', "round": 1},
        {"type": "tool_output", "tool": "web_search", "command": '{"query": "odysseus"}',
         "output": "results", "exit_code": 0, "execution_attempted": True, "blocked": False},
        {"type": "agent_step", "round": 2},
        {"type": "tool_start", "tool": "bash", "command": "exit 3", "round": 2},
        {"type": "tool_output", "tool": "bash", "command": "exit 3",
         "output": "boom", "exit_code": 3, "execution_attempted": True, "blocked": False},
        {"delta": "Summary."},
    ])
    assert status == "success"
    recorded = json.loads(steps)
    assert [(s["round"], s["tool"], s["exit_code"], s["error"]) for s in recorded] == [
        (1, "web_search", 0, False), (2, "bash", 3, True)]
    assert "odysseus" not in steps and "boom" not in steps


async def test_agent_run_without_tool_calls_records_an_empty_list(run_task):
    """The reported case: a well-formed answer produced with zero tool calls."""
    status, steps = await run_task([{"delta": "A confident answer with invented citations."}])
    assert status == "success"
    assert steps == "[]"


async def test_failed_run_keeps_the_steps_it_reached(run_task):
    status, steps = await run_task(
        [{"type": "tool_output", "tool": "web_search", "exit_code": 0, "round": 1}],
        failure=RuntimeError("provider went away"))
    assert status == "error"
    assert [s["tool"] for s in json.loads(steps)] == ["web_search"]


async def test_cancelled_run_keeps_the_steps_it_reached(run_task):
    status, steps = await run_task(
        [{"type": "tool_output", "tool": "bash", "exit_code": 0}],
        failure=asyncio.CancelledError())
    assert status == "aborted"
    assert [s["tool"] for s in json.loads(steps)] == ["bash"]


async def test_action_run_leaves_steps_null(run_task, monkeypatch):
    monkeypatch.setattr(TaskScheduler, "_execute_action", AsyncMock(return_value=("done", True)))
    status, steps = await run_task([], task_type="action", action="noop")
    assert status == "success"
    assert steps is None


async def test_steps_do_not_leak_into_the_next_run(run_task, monkeypatch):
    await run_task([{"type": "tool_output", "tool": "web_search", "exit_code": 0, "round": 1}])
    monkeypatch.setattr(TaskScheduler, "_execute_action", AsyncMock(return_value=("done", True)))
    _, steps = await run_task([], task_type="action", action="noop")
    assert steps is None
