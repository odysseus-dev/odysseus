import json

import pytest

from src import cookbook_serve_lifecycle as lifecycle


@pytest.mark.asyncio
async def test_tick_persists_only_successfully_stopped_serves(tmp_path, monkeypatch):
    state_path = tmp_path / "cookbook_state.json"
    state_path.write_text(
        json.dumps({
            "tasks": [
                {
                    "id": "stop-succeeds",
                    "type": "serve",
                    "status": "running",
                    "_scheduledStopAtMs": 0,
                },
                {
                    "id": "stop-fails",
                    "type": "serve",
                    "status": "running",
                    "_scheduledStopAtMs": 0,
                },
            ]
        }),
        encoding="utf-8",
    )

    async def fake_stop_serve(session_id, remote_host="", ssh_port=""):
        return session_id == "stop-succeeds"

    async def fake_delete_endpoint(task):
        return None

    monkeypatch.setattr(lifecycle, "COOKBOOK_STATE_FILE", str(state_path))
    monkeypatch.setattr(lifecycle, "_stop_serve", fake_stop_serve)
    monkeypatch.setattr(lifecycle, "_delete_endpoint_for_task", fake_delete_endpoint)

    await lifecycle._tick()

    tasks = {
        task["id"]: task
        for task in json.loads(state_path.read_text(encoding="utf-8"))["tasks"]
    }
    assert tasks["stop-succeeds"]["status"] == "stopped"
    assert tasks["stop-succeeds"]["_scheduledStopAtMs"] is None
    assert tasks["stop-fails"]["status"] == "running"
    assert tasks["stop-fails"]["_scheduledStopAtMs"] == 0
