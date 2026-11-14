"""Task history must serialize the canonical ORM fields, including empty runs."""
import json
from datetime import datetime, timedelta

import pytest

from core.database import ScheduledTask, TaskRun
from tests.helpers.cli_loader import load_script
from tests.helpers.database import disposable_database


@pytest.fixture
def tasks_cli(tmp_path, monkeypatch):
    with disposable_database(tmp_path) as factory:
        cli = load_script("odysseus-tasks")
        monkeypatch.setattr(cli, "SessionLocal", factory)
        yield cli, factory


def test_completed_run_uses_persisted_fields_and_preserves_json_keys(tasks_cli):
    cli, _ = tasks_cli
    run = TaskRun(
        id="run", task_id="task", status="success",
        started_at=datetime(2026, 1, 2, 3, 4, 5),
        finished_at=datetime(2026, 1, 2, 3, 4, 6), result="Task result",
    )

    assert cli._serialize_run(run) == {
        "id": "run", "task_id": "task", "status": "success",
        "started_at": "2026-01-02T03:04:05",
        "completed_at": "2026-01-02T03:04:06",
        "output_preview": "Task result",
    }


@pytest.mark.parametrize("status,finished", [
    ("running", None), ("error", datetime(2026, 1, 2, 3, 4, 6)),
])
def test_runs_without_results_keep_empty_preview(tasks_cli, status, finished):
    cli, _ = tasks_cli
    run = TaskRun(id="run", task_id="task", status=status, result=None,
                  started_at=datetime(2026, 1, 2), finished_at=finished)

    serialized = cli._serialize_run(run)

    assert serialized["status"] == status
    assert serialized["completed_at"] == ("2026-01-02T03:04:06" if finished else "")
    assert serialized["output_preview"] == ""


@pytest.mark.parametrize("result,expected", [
    ("x" * 200, "x" * 200),
    ("x" * 201, "x" * 200 + "…"),
    ("ç" * 201, "ç" * 200 + "…"),
])
def test_real_result_is_previewed_at_existing_boundary(tasks_cli, result, expected):
    cli, _ = tasks_cli

    serialized = cli._serialize_run(TaskRun(
        id="run", task_id="task", status="success", result=result,
    ))

    assert serialized["output_preview"] == expected
    assert serialized["started_at"] == ""
    assert serialized["completed_at"] == ""


@pytest.mark.parametrize("command,expected", [
    (["runs", "--limit", "2"], ["other-run", "run-5"]),
    (["runs", "--task", "task", "--limit", "2"], ["run-5", "run-4"]),
    (["show", "task"], ["run-5", "run-4", "run-3", "run-2", "run-1"]),
])
def test_history_commands_read_real_rows_in_order(tasks_cli, capsys, command, expected):
    cli, factory = tasks_cli
    start = datetime(2026, 1, 2, 3, 4, 5)
    with factory() as db:
        db.add_all([ScheduledTask(id="task", name="Example"),
                    ScheduledTask(id="other", name="Other")])
        db.flush()
        for number in range(6):
            db.add(TaskRun(
                id=f"run-{number}", task_id="task",
                started_at=start + timedelta(minutes=number),
                finished_at=None if number == 5 else start + timedelta(minutes=number, seconds=1),
                status="running" if number == 5 else "success",
                result=None if number == 5 else f"Result {number}",
            ))
        db.add(TaskRun(id="other-run", task_id="other", started_at=start + timedelta(hours=1),
                       finished_at=start + timedelta(hours=1, seconds=1),
                       status="success", result="Other result"))
        db.commit()

    args = cli._build_parser().parse_args(command)
    args.func(args)
    payload = json.loads(capsys.readouterr().out)
    rows = payload["recent_runs"] if command[0] == "show" else payload

    assert [row["id"] for row in rows] == expected
    running = next(row for row in rows if row["id"] == "run-5")
    assert running["completed_at"] == ""
    assert running["output_preview"] == ""
    completed = next(row for row in rows if row["status"] == "success")
    if completed["id"] == "other-run":
        assert completed["completed_at"] == "2026-01-02T04:04:06"
        assert completed["output_preview"] == "Other result"
    else:
        assert completed["task_id"] == "task"
        assert completed["completed_at"] == "2026-01-02T03:08:06"
        assert completed["output_preview"] == "Result 4"


def test_empty_history_remains_valid_json(tasks_cli, capsys):
    cli, factory = tasks_cli
    with factory() as db:
        db.add(ScheduledTask(id="empty", name="No executions yet"))
        db.commit()

    for command in (["runs"], ["show", "empty"]):
        args = cli._build_parser().parse_args(command)
        args.func(args)
        payload = json.loads(capsys.readouterr().out)
        assert (payload["recent_runs"] if command[0] == "show" else payload) == []
