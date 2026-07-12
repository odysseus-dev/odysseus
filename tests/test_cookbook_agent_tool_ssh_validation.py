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
            if "/api/cookbook/stop-session" in url:
                return FakeResponse({"ok": True})
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
    assert posts[0][0].endswith("/api/cookbook/stop-session")
    body = posts[0][1]
    assert body["session_id"] == "serve-abc123"
    assert body["remote_host"] == "user@gpu-box"
    assert body["ssh_port"] == "2222"


@pytest.mark.asyncio
async def test_cancel_download_resolves_windows_platform_and_download_task_type(monkeypatch):
    """Agent cancel must resolve remote Windows platform from server config and
    only send repo_id for download tasks."""
    posts = _install_httpx_client(
        monkeypatch,
        state={
            "env": {
                "servers": [
                    {"host": "user@winbox", "platform": "windows", "port": "22"},
                ],
            },
            "tasks": [
                {
                    "sessionId": "cookbook-abc123",
                    "type": "download",
                    "remoteHost": "user@winbox",
                    "sshPort": "22",
                    # platform intentionally missing — must come from servers[]
                    "payload": {"repo_id": "org/model"},
                }
            ],
        },
    )

    result = await tools.do_cancel_download(
        json.dumps({"session_id": "cookbook-abc123"})
    )

    assert result["exit_code"] == 0
    stop_posts = [p for p in posts if str(p[0]).endswith("/api/cookbook/stop-session")]
    assert stop_posts
    body = stop_posts[0][1]
    assert body["platform"] == "windows"
    assert body["task_type"] == "download"
    assert body["repo_id"] == "org/model"


@pytest.mark.asyncio
async def test_stop_served_model_does_not_send_download_repo_metadata(monkeypatch):
    posts = _install_httpx_client(
        monkeypatch,
        state={
            "tasks": [
                {
                    "sessionId": "serve-abc123",
                    "type": "serve",
                    "remoteHost": "",
                    "payload": {"repo_id": "org/model"},
                }
            ],
        },
    )

    result = await tools.do_stop_served_model(
        json.dumps({"session_id": "serve-abc123"})
    )

    assert result["exit_code"] == 0
    stop_posts = [p for p in posts if str(p[0]).endswith("/api/cookbook/stop-session")]
    assert stop_posts
    body = stop_posts[0][1]
    assert body.get("task_type") == "serve"
    assert "repo_id" not in body


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
