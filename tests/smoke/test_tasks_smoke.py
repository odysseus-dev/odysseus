"""Tasks: a scheduled task is created with a computed next run and is listed.

Deliberately not fired. Running a task is model and tool work the
checkpoint benchmark covers; what this asserts is that the scheduler
still accepts a task and computes when it should run, which is the part
a route move can break silently.
"""
from __future__ import annotations

TASKS_PATH = "/api/tasks"

NAME = "Odysseus smoke task"
SCHEDULED_TIME = "03:00"


def test_a_scheduled_task_round_trips(client):
    created = client.post(TASKS_PATH, json={
        "name": NAME,
        "task_type": "llm",
        "prompt": "Smoke task; never run by this suite.",
        "trigger_type": "schedule",
        "schedule": "daily",
        "scheduled_time": SCHEDULED_TIME,
    })
    assert created.status_code == 200, created.text
    body = created.json()
    task_id = body["id"]
    try:
        assert body.get("next_run"), f"no next run computed for a daily task: {body}"
        assert body.get("status") == "active", body

        listed = client.get(TASKS_PATH)
        assert listed.status_code == 200, listed.text
        assert task_id in [t.get("id") for t in listed.json().get("tasks") or []]

        paused = client.post(f"{TASKS_PATH}/{task_id}/pause")
        assert paused.status_code == 200, paused.text
        assert client.get(f"{TASKS_PATH}/{task_id}").json().get("status") == "paused"
    finally:
        removed = client.delete(f"{TASKS_PATH}/{task_id}")
        assert removed.status_code == 200, removed.text
