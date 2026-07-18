import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import routes.cookbook_routes as cookbook_routes
from routes.cookbook_helpers import ModelDownloadRequest


def _route_endpoint(path: str, method: str):
    router = cookbook_routes.setup_cookbook_routes()
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and method in route.methods
    )


def _admin_request(path: str) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "state": {},
        }
    )
    request.state.current_user = "admin"
    return request


class _ExecProc:
    def __init__(self, returncode: int = 0, stdout: str = ""):
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self):
        return self._stdout.encode("utf-8"), b""


class _EmptyStream:
    async def read(self):
        return b""


class _ShellProc:
    returncode = 0
    stderr = _EmptyStream()

    async def wait(self):
        return 0


@pytest.mark.asyncio
async def test_local_windows_stop_failure_preserves_recovery_state(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        cookbook_routes, "COOKBOOK_STATE_FILE", str(tmp_path / "state.json")
    )
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(cookbook_routes, "pid_alive", lambda pid: True)
    runner = tmp_path / "cookbook-deadbeef_run.ps1"
    pid_file = tmp_path / "cookbook-deadbeef.pid"
    runner.write_text("snapshot_download('org/model')", encoding="utf-8")
    pid_file.write_text("1234", encoding="utf-8")

    def failed_taskkill(args, **kwargs):
        assert args[0] == "taskkill"
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(cookbook_routes.subprocess, "run", failed_taskkill)
    endpoint = _route_endpoint("/api/cookbook/stop-session", "POST")
    req = SimpleNamespace(
        session_id="cookbook-deadbeef",
        remote_host=None,
        ssh_port=None,
        platform="windows",
        repo_id="org/model",
        task_type="download",
    )

    result = await endpoint(_admin_request("/api/cookbook/stop-session"), req)

    assert result["ok"] is False
    assert result["stopped"] is False
    assert "failed to stop local Windows process tree" in result["error"]
    assert runner.exists()
    assert pid_file.exists()
    assert not (tmp_path / "cookbook-deadbeef.stop").exists()
    assert not (tmp_path / "cookbook-stopped-repos.json").exists()


@pytest.mark.asyncio
async def test_local_windows_stop_requires_a_confirmed_process(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        cookbook_routes, "COOKBOOK_STATE_FILE", str(tmp_path / "state.json")
    )
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(cookbook_routes, "pid_alive", lambda pid: False)
    runner = tmp_path / "cookbook-deadbeef_run.ps1"
    runner.write_text("snapshot_download('org/model')", encoding="utf-8")

    def successful_empty_scan(args, **kwargs):
        assert args[0] == "powershell"
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(cookbook_routes.subprocess, "run", successful_empty_scan)
    endpoint = _route_endpoint("/api/cookbook/stop-session", "POST")
    req = SimpleNamespace(
        session_id="cookbook-deadbeef",
        remote_host=None,
        ssh_port=None,
        platform="windows",
        repo_id="org/model",
        task_type="download",
    )

    result = await endpoint(_admin_request("/api/cookbook/stop-session"), req)

    assert result["ok"] is False
    assert result["stopped"] is False
    assert "no live local Windows session process" in result["error"]
    assert runner.exists()
    assert not (tmp_path / "cookbook-deadbeef.stop").exists()


@pytest.mark.asyncio
async def test_local_windows_stop_cleans_artifacts_after_verified_kill(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        cookbook_routes, "COOKBOOK_STATE_FILE", str(tmp_path / "state.json")
    )
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    alive_results = iter([True, False])
    monkeypatch.setattr(
        cookbook_routes, "pid_alive", lambda pid: next(alive_results, False)
    )
    runner = tmp_path / "cookbook-deadbeef_run.ps1"
    pid_file = tmp_path / "cookbook-deadbeef.pid"
    runner.write_text("snapshot_download('org/model')", encoding="utf-8")
    pid_file.write_text("1234", encoding="utf-8")

    def successful_commands(args, **kwargs):
        assert args[0] in {"taskkill", "powershell"}
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(cookbook_routes.subprocess, "run", successful_commands)
    endpoint = _route_endpoint("/api/cookbook/stop-session", "POST")
    req = SimpleNamespace(
        session_id="cookbook-deadbeef",
        remote_host=None,
        ssh_port=None,
        platform="windows",
        repo_id="org/model",
        task_type="download",
    )

    result = await endpoint(_admin_request("/api/cookbook/stop-session"), req)

    assert result["ok"] is True
    assert result["stopped"] is True
    assert not runner.exists()
    assert not pid_file.exists()
    assert (tmp_path / "cookbook-deadbeef.stop").exists()
    assert "org/model" in (
        tmp_path / "cookbook-stopped-repos.json"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_remote_windows_stop_failure_preserves_recovery_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        cookbook_routes, "COOKBOOK_STATE_FILE", str(tmp_path / "state.json")
    )
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    runner = tmp_path / "cookbook-deadbeef_run.ps1"
    pid_file = tmp_path / "cookbook-deadbeef.pid"
    runner.write_text("snapshot_download('org/model')", encoding="utf-8")
    pid_file.write_text("1234", encoding="utf-8")

    async def failed_ssh(*args, **kwargs):
        return _ExecProc(returncode=255)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failed_ssh)
    endpoint = _route_endpoint("/api/cookbook/stop-session", "POST")
    req = SimpleNamespace(
        session_id="cookbook-deadbeef",
        remote_host="winbox",
        ssh_port="22",
        platform="windows",
        repo_id="org/model",
        task_type="download",
    )

    result = await endpoint(_admin_request("/api/cookbook/stop-session"), req)

    assert result["ok"] is False
    assert result["stopped"] is False
    assert result["exit_code"] == 255
    assert runner.exists()
    assert pid_file.exists()
    assert not (tmp_path / "cookbook-deadbeef.stop").exists()
    assert not (tmp_path / "cookbook-stopped-repos.json").exists()


@pytest.mark.asyncio
async def test_remote_windows_stop_finds_python_fallback_without_session_pid(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        cookbook_routes, "COOKBOOK_STATE_FILE", str(tmp_path / "state.json")
    )
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    runner = tmp_path / "cookbook-deadbeef_run.ps1"
    runner.write_text("snapshot_download('org/model')", encoding="utf-8")
    calls = []
    responses = iter(
        [
            _ExecProc(),  # Primary stale/missing-PID session sweep.
            _ExecProc(
                stdout=(
                    "4321\tpython -c \"from huggingface_hub import snapshot_download; "
                    "snapshot_download('org/model', max_workers=8)\"\n"
                )
            ),
            _ExecProc(),  # taskkill fallback process.
            _ExecProc(),  # Follow-up scan confirms it is gone.
        ]
    )

    async def fake_ssh(*args, **kwargs):
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_ssh)
    endpoint = _route_endpoint("/api/cookbook/stop-session", "POST")
    req = SimpleNamespace(
        session_id="cookbook-deadbeef",
        remote_host="winbox",
        ssh_port="22",
        platform="windows",
        repo_id="org/model",
        task_type="download",
    )

    result = await endpoint(_admin_request("/api/cookbook/stop-session"), req)

    assert result["ok"] is True
    assert any("taskkill /F /T /PID 4321" in str(call[-1]) for call in calls)
    assert not runner.exists()
    assert (tmp_path / "cookbook-deadbeef.stop").exists()


@pytest.mark.asyncio
async def test_remote_windows_retry_blocks_on_untracked_python_fallback(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        cookbook_routes, "COOKBOOK_STATE_FILE", str(tmp_path / "state.json")
    )
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    (tmp_path / "cookbook-deadbeef_run.ps1").write_text(
        "python -c \"from huggingface_hub import snapshot_download; "
        "snapshot_download('org/model', max_workers=8)\"",
        encoding="utf-8",
    )
    responses = iter(
        [
            _ExecProc(returncode=1),  # Session PID is stale or missing.
            _ExecProc(
                stdout=(
                    "4321\tpython -c \"from huggingface_hub import snapshot_download; "
                    "snapshot_download('org/model', max_workers=8)\"\n"
                )
            ),
        ]
    )

    async def fake_ssh(*args, **kwargs):
        return next(responses)

    async def fail_if_launches(*args, **kwargs):
        raise AssertionError("retry must not launch while fallback downloader is alive")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_ssh)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fail_if_launches)
    endpoint = _route_endpoint("/api/model/download", "POST")
    req = ModelDownloadRequest(
        repo_id="org/model",
        remote_host="winbox",
        ssh_port="22",
        platform="windows",
    )

    result = await endpoint(_admin_request("/api/model/download"), req)

    assert result["ok"] is False
    assert "pid 4321" in result["error"]
    assert "already downloading org/model" in result["error"]


@pytest.mark.asyncio
async def test_remote_windows_hf_runner_emits_real_powershell_blocks(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        cookbook_routes, "COOKBOOK_STATE_FILE", str(tmp_path / "state.json")
    )
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)

    async def no_existing_download(*args, **kwargs):
        return _ExecProc()

    async def successful_launch(*args, **kwargs):
        return _ShellProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_existing_download)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", successful_launch)
    endpoint = _route_endpoint("/api/model/download", "POST")
    req = ModelDownloadRequest(
        repo_id="org/model",
        remote_host="winbox",
        ssh_port="22",
        platform="windows",
    )

    result = await endpoint(_admin_request("/api/model/download"), req)

    assert result["ok"] is True
    runners = list(tmp_path.glob("cookbook-*_run.ps1"))
    assert len(runners) == 1
    source = runners[0].read_text(encoding="utf-8")
    assert "try {\n" in source
    assert "  if ($hfPath) {\n" in source
    assert "  } else {\n" in source
    assert "    if ($LASTEXITCODE -eq 0) {\n" in source
    assert "} catch {\n" in source
    assert "{{" not in source
    assert "}}" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize("ssh_port", ["0", "65536", "99999"])
async def test_server_setup_rejects_out_of_range_ssh_port(
    monkeypatch, ssh_port
):
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)

    async def fail_if_shell_runs(*args, **kwargs):
        raise AssertionError("invalid SSH port must be rejected before shell launch")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fail_if_shell_runs)
    endpoint = _route_endpoint("/api/cookbook/setup", "POST")
    req = SimpleNamespace(host="winbox", ssh_port=ssh_port)

    with pytest.raises(HTTPException) as exc:
        await endpoint(_admin_request("/api/cookbook/setup"), req)

    assert exc.value.status_code == 400
    assert "ssh_port" in str(exc.value.detail)
