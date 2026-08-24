import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from starlette.requests import Request

import routes.cookbook_routes as cookbook_routes
from routes.cookbook_helpers import ModelDownloadRequest


def _route_endpoint(path: str, method: str):
    router = cookbook_routes.setup_cookbook_routes()
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _admin_request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/model/download",
            "headers": [],
            "state": {},
        }
    )
    request.state.current_user = "admin"
    return request


class _EmptyStream:
    async def read(self):
        return b""


class _SuccessfulProcess:
    returncode = 0
    stderr = _EmptyStream()

    async def wait(self):
        return 0


class _FailedProcess:
    returncode = 1
    stderr = _EmptyStream()

    async def wait(self):
        return 1


@pytest.mark.asyncio
async def test_reliable_local_download_sets_xet_timeouts_watchdog_and_private_wrapper(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(cookbook_routes.shutil, "which", lambda binary: f"/usr/bin/{binary}")
    async def _successful_subprocess(*args, **kwargs):
        return _SuccessfulProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _successful_subprocess)
    monkeypatch.setenv("ODYSSEUS_HF_DOWNLOAD_ATTEMPT_TIMEOUT_SECONDS", "1234")

    endpoint = _route_endpoint("/api/model/download", "POST")
    result = await endpoint(
        _admin_request(),
        ModelDownloadRequest(
            repo_id="org/private-model",
            hf_token="hf_test_secret",
            disable_hf_transfer=True,
        ),
    )

    wrapper = tmp_path / f"{result['session_id']}.sh"
    script = wrapper.read_text(encoding="utf-8")
    assert "export HF_HUB_DISABLE_XET=1" in script
    assert "export HF_HUB_DOWNLOAD_TIMEOUT=" in script
    assert "export HF_HUB_ETAG_TIMEOUT=" in script
    assert "_ODYSSEUS_DOWNLOAD_ATTEMPT_TIMEOUT=1234" in script
    assert "_odysseus_run_with_timeout hf download org/private-model" in script
    assert "trap _odysseus_cleanup EXIT" in script
    assert "trap 'exit 143' TERM" in script
    assert "_odysseus_cleanup\ntrap - EXIT HUP INT TERM\nexec" in script
    assert os.stat(wrapper).st_mode & 0o777 == 0o700


def test_posix_download_watchdog_ends_a_hung_attempt():
    timeout_tool = shutil.which("timeout") or shutil.which("gtimeout")
    if not timeout_tool:
        pytest.skip("coreutils timeout is unavailable")

    lines = ["#!/bin/sh"]
    cookbook_routes._append_posix_download_watchdog(lines, 1)
    lines.append("_odysseus_run_with_timeout sh -c 'sleep 10'")
    script = "\n".join(lines)

    started = time.monotonic()
    result = subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode in {124, 137}
    assert time.monotonic() - started < 4


def test_private_wrapper_term_signal_exits_once_and_removes_script(tmp_path):
    wrapper = tmp_path / "private-runner.sh"
    lines = cookbook_routes._posix_private_wrapper_prelude()
    lines.extend(["kill -TERM $$", 'echo "UNEXPECTED_CONTINUATION"'])
    wrapper.write_text("\n".join(lines) + "\n", encoding="utf-8")
    wrapper.chmod(0o700)

    result = subprocess.run(
        ["bash", str(wrapper)], capture_output=True, text=True, timeout=5
    )

    assert result.returncode == 143
    assert "UNEXPECTED_CONTINUATION" not in result.stdout
    assert not wrapper.exists()


def test_download_attempt_timeout_is_bounded_and_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_HF_DOWNLOAD_ATTEMPT_TIMEOUT_SECONDS", "999999")
    assert cookbook_routes._hf_download_attempt_timeout_seconds() == 86_400

    monkeypatch.setenv("ODYSSEUS_HF_DOWNLOAD_ATTEMPT_TIMEOUT_SECONDS", "0")
    assert cookbook_routes._hf_download_attempt_timeout_seconds() == 0

    monkeypatch.setenv("ODYSSEUS_HF_DOWNLOAD_ATTEMPT_TIMEOUT_SECONDS", "invalid")
    assert cookbook_routes._hf_download_attempt_timeout_seconds() == 900


@pytest.mark.asyncio
async def test_remote_reliable_runner_is_private_and_local_copy_is_always_removed(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    captured = {}

    class _RemoteCheckProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def _successful_exec(*args, **kwargs):
        return _RemoteCheckProcess()

    async def _capture_setup(*args, **kwargs):
        [runner] = tmp_path.glob("*_run.sh")
        captured["path"] = runner
        captured["script"] = runner.read_text(encoding="utf-8")
        captured["mode"] = os.stat(runner).st_mode & 0o777
        captured["command"] = args[0]
        return _SuccessfulProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _successful_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _capture_setup)

    endpoint = _route_endpoint("/api/model/download", "POST")
    result = await endpoint(
        _admin_request(),
        ModelDownloadRequest(
            repo_id="org/private-model",
            hf_token="hf_remote_secret",
            disable_hf_transfer=True,
            remote_host="gpu-box",
            platform="linux",
        ),
    )

    assert result["ok"] is True
    assert captured["mode"] == 0o700
    assert "export HF_HUB_DISABLE_XET=1" in captured["script"]
    assert (
        '_odysseus_run_with_timeout "$ODYSSEUS_HF_CLI" download '
        "org/private-model" in captured["script"]
    )
    assert "chmod 700" in captured["command"]
    assert not captured["path"].exists()


@pytest.mark.asyncio
async def test_remote_runner_local_copy_is_removed_when_setup_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    captured = {}

    class _RemoteCheckProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def _successful_exec(*args, **kwargs):
        return _RemoteCheckProcess()

    async def _failed_setup(*args, **kwargs):
        [runner] = tmp_path.glob("*_run.sh")
        captured["path"] = runner
        return _FailedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _successful_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _failed_setup)

    endpoint = _route_endpoint("/api/model/download", "POST")
    result = await endpoint(
        _admin_request(),
        ModelDownloadRequest(
            repo_id="org/private-model",
            hf_token="hf_remote_secret",
            disable_hf_transfer=True,
            remote_host="gpu-box",
            platform="linux",
        ),
    )

    assert result["ok"] is False
    assert not captured["path"].exists()
