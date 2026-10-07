import asyncio
import re
from unittest.mock import AsyncMock

import pytest

from src.agent_tools.subprocess_tools import (
    _host_shell_requires_detach,
    _host_shell_should_auto_poll,
)
from src.agent_loop import _host_bridge_failure_response, _is_host_bridge_failure_result
from src.tool_execution import format_tool_result


class _FakeResponse:
    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    requests: list[dict] = []

    def __init__(self, *args, **kwargs):
        self._responses = iter([
            {"status": "running", "detached": True, "job_id": "job-1", "output": "started"},
            {"status": "completed", "job_id": "job-1", "output": "finished", "exit_code": 0},
        ])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, *, json, headers):
        self.requests.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(next(self._responses))


def test_explicit_background_marker_requires_detach():
    assert _host_shell_requires_detach("#!bg\npip install package")


def test_long_sleep_requires_detach_when_model_omits_flag():
    assert _host_shell_requires_detach("sleep 22; printf done")
    assert not _host_shell_requires_detach("sleep 3; printf done")


def test_short_commands_remain_synchronous():
    assert not _host_shell_requires_detach("ip route")


def test_only_implicit_long_jobs_are_auto_polled():
    assert _host_shell_should_auto_poll("sleep 22; printf done")
    assert not _host_shell_should_auto_poll("#!bg\nsleep 22")


def test_detached_result_requires_polling_the_returned_job_id():
    rendered = format_tool_result(
        "host_shell: sleep",
        {"output": "started", "exit_code": 0, "detached": True, "status": "running", "job_id": "job-1"},
    )
    assert "POLL REQUIRED" in rendered
    assert '"job_id":"job-1"' in rendered
    assert "substitute command" in rendered


def test_bridge_transport_failure_is_terminal_for_the_turn():
    assert _is_host_bridge_failure_result(
        {"error": "host_shell: bridge call failed: connection refused", "exit_code": 1}
    )
    assert not _is_host_bridge_failure_result(
        {"output": "bridge check completed", "exit_code": 0}
    )


def test_bridge_transport_failure_response_is_concise_and_actionable():
    response = _host_bridge_failure_response()
    assert "unavailable" in response
    assert "Restart or reconnect" in response
    assert len(response) < 180


@pytest.mark.asyncio
async def test_host_shell_auto_polls_implicit_long_job(monkeypatch):
    from src.agent_tools import subprocess_tools

    _FakeAsyncClient.requests = []
    monkeypatch.setattr(subprocess_tools.httpx, "AsyncClient", _FakeAsyncClient)
    result = await subprocess_tools.HostShellTool().execute(
        '{"command":"sleep 22; printf done"}',
        {
            "client_runtime_context": {
                "host_shell_bridge": {
                    "url": "http://127.0.0.1:17654/run",
                    "token": "bridge-token",
                }
            }
        },
    )

    assert result["output"] == "finished"
    assert result["status"] == "completed"
    assert [item["json"] for item in _FakeAsyncClient.requests] == [
        {"command": "sleep 22; printf done", "timeout": 30, "detach": True},
        {"job_id": "job-1"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ({"error": "request rejected", "exit_code": 1}, "request rejected"),
        ({"output": "looks fine", "exit_code": "zero"}, "invalid exit_code"),
        ({"output": "looks fine", "exit_code": False}, "invalid exit_code"),
    ],
)
async def test_host_shell_turns_malformed_bridge_results_into_tool_failures(
    monkeypatch, payload, expected_error,
):
    from src.agent_tools import subprocess_tools

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return _FakeResponse(payload)

    monkeypatch.setattr(subprocess_tools.httpx, "AsyncClient", Client)
    result = await subprocess_tools.HostShellTool().execute(
        '{"command":"printf ready"}',
        {
            "client_runtime_context": {
                "host_shell_bridge": {
                    "url": "http://127.0.0.1:17654/run",
                    "token": "bridge-token",
                }
            }
        },
    )

    assert expected_error in result["error"]
    assert result["exit_code"] == 1
    assert result["host_bridge"] == "tui"


@pytest.mark.asyncio
async def test_cancelled_canonical_host_shell_notifies_tui_bridge(monkeypatch):
    from src.agent_tools import subprocess_tools

    entered = asyncio.Event()
    captured = {}

    class BlockingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, json, headers):
            captured.update(json)
            entered.set()
            await asyncio.Future()

    cancel = AsyncMock()
    monkeypatch.setattr(subprocess_tools.httpx, "AsyncClient", BlockingClient)
    monkeypatch.setattr(
        subprocess_tools, "_cancel_host_shell_bridge_request", cancel,
    )
    task = asyncio.create_task(subprocess_tools.HostShellTool().execute(
        '{"command":"printf ready"}',
        {
            "client_runtime_context": {
                "host_shell_bridge": {
                    "url": "http://127.0.0.1:17654/run",
                    "token": "bridge-token",
                }
            }
        },
    ))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert re.fullmatch(r"[A-Za-z0-9_-]+", captured["request_id"])
    cancel.assert_awaited_once_with(
        "http://127.0.0.1:17654/run",
        "bridge-token",
        captured["request_id"],
    )
