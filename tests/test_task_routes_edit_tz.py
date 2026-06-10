"""Editing/resuming a crew task must recompute next_run in the crew timezone.

create_task (initial creation) and assistant_routes pass tz_name to
compute_next_run, but the task EDIT paths in task_routes (update_task,
resume_task, revert-to-defaults, and resume-on-open) called compute_next_run
WITHOUT tz_name. So editing a crew member's check-in time recomputed
next_run in UTC wall-clock, firing the next occurrence at the wrong local
hour until the scheduler self-corrects on the following run.
"""
import ast
from pathlib import Path

from src.task_scheduler import compute_next_run


def test_tz_name_actually_shifts_compute_next_run():
    # Proves the parameter matters: a non-UTC tz changes the stored instant.
    utc = compute_next_run("daily", "09:00")
    ny = compute_next_run("daily", "09:00", tz_name="America/New_York")
    assert utc != ny


def test_edit_call_sites_pass_tz_name():
    src = Path("routes/task_routes.py").read_text()
    tree = ast.parse(src)
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "compute_next_run":
            first = ast.unparse(node.args[0]) if node.args else ""
            # The edit/resume paths build the call from a `task` object or the
            # housekeeping `defs`. create_task builds it from `req.` and is the
            # initial-creation path (crew tz attached elsewhere), so skip it.
            if first.startswith("task.") or first.startswith("defs["):
                kw = {k.arg for k in node.keywords}
                if "tz_name" not in kw:
                    missing.append(ast.unparse(node)[:60])
    assert not missing, f"compute_next_run edit call(s) missing tz_name: {missing}"
