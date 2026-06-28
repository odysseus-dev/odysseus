import json

import httpx
import pytest

from src import builtin_actions


class _FakeServeResponse:
    content = b"{}"

    def json(self):
        return {"ok": True, "session_id": "tmux-123"}


async def _fake_post(self, *_args, **_kwargs):
    return _FakeServeResponse()


async def _run_scheduled_serve(tmp_path, monkeypatch, server):
    state_path = tmp_path / "cookbook_state.json"
    state_path.write_text(
        json.dumps({"env": {"servers": [server]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(builtin_actions, "COOKBOOK_STATE_FILE", str(state_path))
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    message, ok = await builtin_actions.action_cookbook_serve(
        owner="alice",
        task_name="test-serve",
        command=json.dumps({
            "repo_id": "org/model",
            "cmd": "llama-server --port 8080",
            "host": "gpu-box",
            "end_after_min": 30,
        }),
    )

    assert ok is True, message
    tasks = json.loads(state_path.read_text(encoding="utf-8"))["tasks"]
    assert len(tasks) == 1
    return tasks[0]


@pytest.mark.asyncio
async def test_scheduled_serve_preserves_server_ssh_port_and_platform(tmp_path, monkeypatch):
    task = await _run_scheduled_serve(
        tmp_path,
        monkeypatch,
        {"name": "gpu-box", "host": "gpu-box", "port": "2222", "platform": "windows"},
    )

    assert task["sshPort"] == "2222"
    assert task["platform"] == "windows"
    assert task["remoteHost"] == "gpu-box"
    assert task["payload"]["_cmd"] == "llama-server --port 8080"


@pytest.mark.asyncio
async def test_scheduled_serve_uses_task_state_fallbacks_without_server_metadata(
    tmp_path,
    monkeypatch,
):
    task = await _run_scheduled_serve(
        tmp_path,
        monkeypatch,
        {"name": "gpu-box", "host": "gpu-box"},
    )

    assert task["sshPort"] == ""
    assert task["platform"] == "linux"
