import json

import pytest

from src import tool_implementations as tools


class FakeResponse:
    def __init__(self, data=None, status_code=200):
        self._data = data or {}
        self.status_code = status_code
        self.text = json.dumps(self._data)
        self.content = self.text.encode("utf-8")
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._data


def _install_httpx_client(monkeypatch, *, state=None, posts=None):
    import httpx

    posts = posts if posts is not None else []
    state = state if state is not None else {"tasks": []}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            return FakeResponse(state)

        async def post(self, url, json=None, **kwargs):
            posts.append((url, json, kwargs))
            return FakeResponse({"stdout": "", "stderr": "", "exit_code": 0})

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    return posts


@pytest.mark.asyncio
async def test_stop_served_model_rejects_invalid_remote_host_before_shell(monkeypatch):
    posts = _install_httpx_client(monkeypatch)

    result = await tools.do_stop_served_model(
        json.dumps({"session_id": "serve-abc123", "remote_host": "-bad"})
    )

    assert result["exit_code"] == 1
    assert "Invalid remote_host" in result["error"]
    assert posts == []


@pytest.mark.asyncio
async def test_stop_served_model_rejects_invalid_state_host_before_shell(monkeypatch):
    posts = _install_httpx_client(
        monkeypatch,
        state={
            "tasks": [
                {
                    "sessionId": "serve-abc123",
                    "remoteHost": "-bad",
                    "sshPort": "22",
                }
            ]
        },
    )

    result = await tools.do_stop_served_model(
        json.dumps({"session_id": "serve-abc123"})
    )

    assert result["exit_code"] == 1
    assert "Invalid remote_host" in result["error"]
    assert posts == []


@pytest.mark.asyncio
async def test_stop_served_model_rejects_invalid_ssh_port_before_shell(monkeypatch):
    posts = _install_httpx_client(monkeypatch)

    result = await tools.do_stop_served_model(
        json.dumps(
            {
                "session_id": "serve-abc123",
                "remote_host": "gpu-box",
                "ssh_port": "not-a-port",
            }
        )
    )

    assert result["exit_code"] == 1
    assert "Invalid ssh_port" in result["error"]
    assert posts == []


@pytest.mark.asyncio
async def test_stop_served_model_uses_validated_remote_target(monkeypatch):
    posts = _install_httpx_client(monkeypatch)

    result = await tools.do_stop_served_model(
        json.dumps(
            {
                "session_id": "serve-abc123",
                "remote_host": "user@gpu-box",
                "ssh_port": 2222,
            }
        )
    )

    assert result["exit_code"] == 0
    assert len(posts) == 1
    command = posts[0][1]["command"]
    assert "ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no" in command
    assert "-p 2222 user@gpu-box" in command
    assert "tmux kill-session -t serve-abc123" in command


@pytest.mark.asyncio
async def test_cancel_download_rejects_invalid_remote_host_before_shell(monkeypatch):
    posts = _install_httpx_client(monkeypatch)

    result = await tools.do_cancel_download(
        json.dumps({"session_id": "cookbook-abc123", "remote_host": "-bad"})
    )

    assert result["exit_code"] == 1
    assert "Invalid remote_host" in result["error"]
    assert posts == []


@pytest.mark.asyncio
async def test_cancel_download_rejects_invalid_state_host_before_shell(monkeypatch):
    posts = _install_httpx_client(
        monkeypatch,
        state={
            "tasks": [
                {
                    "sessionId": "cookbook-abc123",
                    "remoteHost": "-bad",
                    "sshPort": "22",
                }
            ]
        },
    )

    result = await tools.do_cancel_download(
        json.dumps({"session_id": "cookbook-abc123"})
    )

    assert result["exit_code"] == 1
    assert "Invalid remote_host" in result["error"]
    assert posts == []


@pytest.mark.asyncio
async def test_tail_serve_output_rejects_invalid_remote_host_before_shell(monkeypatch):
    posts = _install_httpx_client(monkeypatch)

    result = await tools.do_tail_serve_output(
        json.dumps({"session_id": "serve-abc123", "remote_host": "-bad"})
    )

    assert result["exit_code"] == 1
    assert "Invalid remote_host" in result["error"]
    assert posts == []


@pytest.mark.asyncio
async def test_tail_serve_output_rejects_invalid_state_host_before_shell(monkeypatch):
    posts = _install_httpx_client(
        monkeypatch,
        state={
            "tasks": [
                {
                    "sessionId": "serve-abc123",
                    "remoteHost": "-bad",
                    "sshPort": "22",
                }
            ]
        },
    )

    result = await tools.do_tail_serve_output(
        json.dumps({"session_id": "serve-abc123"})
    )

    assert result["exit_code"] == 1
    assert "Invalid remote_host" in result["error"]
    assert posts == []


@pytest.mark.asyncio
async def test_adopt_served_model_rejects_invalid_remote_host_before_shell(monkeypatch):
    posts = _install_httpx_client(monkeypatch)

    result = await tools.do_adopt_served_model(
        json.dumps(
            {
                "tmux_session": "serve_abc123",
                "model": "org/model",
                "host": "-bad",
            }
        )
    )

    assert result["exit_code"] == 1
    assert "Invalid remote_host" in result["error"]
    assert posts == []
