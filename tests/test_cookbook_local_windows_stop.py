"""Local-Windows cookbook stop-session / orphan guard tests (PR1 scope)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import routes.cookbook_routes as cookbook_routes
from routes.cookbook_helpers import ModelDownloadRequest
from routes.cookbook_routes import (
    _cmdline_references_hf_repo,
    _coerce_ssh_port,
    _download_target_key,
)


def _route_endpoint(path: str, method: str):
    router = cookbook_routes.setup_cookbook_routes()
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and method in route.methods
    )


def _admin_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "state": {},
        }
    )


def test_cmdline_references_hf_repo_exact_match_not_prefix():
    short = "org/model"
    long_repo = "org/model-large"
    assert _cmdline_references_hf_repo(f"python hf_download.py {short}", short)
    assert _cmdline_references_hf_repo(f"hf download {short} --local-dir /tmp", short)
    assert _cmdline_references_hf_repo(
        f"snapshot_download('{short}', local_dir='/tmp')", short
    )
    assert not _cmdline_references_hf_repo(f"python hf_download.py {long_repo}", short)
    assert not _cmdline_references_hf_repo(f"hf download {long_repo}", short)


def test_coerce_ssh_port_rejects_out_of_range():
    assert _coerce_ssh_port("2222") == "2222"
    assert _coerce_ssh_port("0") is None
    assert _coerce_ssh_port("65536") is None


def test_local_windows_stop_session_wiring_source():
    routes_src = Path(cookbook_routes.__file__).read_text(encoding="utf-8")
    running_src = (Path(__file__).resolve().parents[1] / "static" / "js" / "cookbookRunning.js").read_text(
        encoding="utf-8"
    )
    assert "/api/cookbook/stop-session" in routes_src
    assert "_scan_windows_session_pids" in routes_src
    assert "_cmdline_references_hf_repo" in routes_src
    assert "grep -Fxq" in routes_src
    assert "_stopCookbookSession" in running_src
    assert "async function _onTaskStop" in running_src
    assert "_userStopped: false, status: priorStatus" in running_src


@pytest.mark.asyncio
async def test_stop_session_dependency_pip_label_still_kills(monkeypatch):
    import asyncio

    endpoint = _route_endpoint("/api/cookbook/stop-session", "POST")
    kill_ran: list[bool] = []

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_subprocess_shell(*args, **kwargs):
        kill_ran.append(True)
        return FakeProc()

    monkeypatch.setattr(cookbook_routes, "IS_WINDOWS", False)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_subprocess_shell)
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)

    req = SimpleNamespace(
        session_id="cookbook-deadbeef",
        remote_host=None,
        ssh_port=None,
        platform=None,
        repo_id="llama-cpp-python[server]",
        task_type="dependency",
    )
    result = await endpoint(_admin_request("/api/cookbook/stop-session"), req)
    assert result["ok"] is True
    assert kill_ran


@pytest.mark.asyncio
async def test_stop_session_serve_skips_download_repo_side_effects(monkeypatch):
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    endpoint = _route_endpoint("/api/cookbook/stop-session", "POST")
    seen: list = []

    async def fake_impl(
        session_id,
        remote_host="",
        ssh_port=None,
        platform="",
        repo_id=None,
        download_key=None,
    ):
        seen.append(
            {
                "repo_id": repo_id,
                "platform": platform,
                "download_key": download_key,
            }
        )
        return {"ok": True}

    for name, cell in zip(endpoint.__code__.co_freevars, endpoint.__closure__ or ()):
        if name == "_stop_cookbook_session_impl":
            cell.cell_contents = fake_impl
            break

    request = _admin_request("/api/cookbook/stop-session")
    req_serve = SimpleNamespace(
        session_id="serve-deadbeef",
        remote_host=None,
        ssh_port=None,
        platform=None,
        repo_id="org/model",
        task_type="serve",
    )
    result = await endpoint(request, req_serve)
    assert result["ok"] is True
    assert seen and seen[0]["repo_id"] is None

    req_dl = SimpleNamespace(
        session_id="cookbook-deadbeef",
        remote_host=None,
        ssh_port=None,
        platform=None,
        repo_id="org/model",
        task_type="download",
    )
    await endpoint(request, req_dl)
    assert seen[-1]["repo_id"] == "org/model"
    assert seen[-1]["download_key"] == _download_target_key("org/model")


def test_download_target_identity_includes_cache_and_include():
    base = _download_target_key("org/model", r"C:\Models", "*.gguf")
    assert base == _download_target_key("org/model", r"c:\models\\", "*.gguf")
    assert base != _download_target_key("org/model", r"C:\Other", "*.gguf")
    assert base != _download_target_key("org/model", r"C:\Models", "*.safetensors")


@pytest.mark.asyncio
async def test_failed_windows_scan_blocks_duplicate_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(cookbook_routes, "IS_WINDOWS", True)
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)

    def failed_scan(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(cookbook_routes.subprocess, "run", failed_scan)
    endpoint = _route_endpoint("/api/model/download", "POST")
    result = await endpoint(
        _admin_request("/api/model/download"),
        ModelDownloadRequest(repo_id="org/model"),
    )

    assert result["ok"] is False
    assert result["recovery_required"] is True
    assert "Could not verify" in result["error"]


@pytest.mark.asyncio
async def test_local_download_launch_claim_is_atomic_and_target_scoped(monkeypatch):
    endpoint = _route_endpoint("/api/model/download", "POST")
    entered: list[str | None] = []
    release = asyncio.Event()

    async def fake_impl(request, req, download_key=None):
        entered.append(req.local_dir)
        await release.wait()
        return {"ok": True, "session_id": "cookbook-deadbeef"}

    for name, cell in zip(endpoint.__code__.co_freevars, endpoint.__closure__ or ()):
        if name == "_model_download_impl":
            cell.cell_contents = fake_impl
            break

    request = _admin_request("/api/model/download")
    first = asyncio.create_task(
        endpoint(request, ModelDownloadRequest(repo_id="org/model", local_dir="C:/one"))
    )
    while not entered:
        await asyncio.sleep(0)

    duplicate = await endpoint(
        request, ModelDownloadRequest(repo_id="org/model", local_dir="C:/one")
    )
    assert duplicate["ok"] is False
    assert duplicate["launch_in_progress"] is True

    other = asyncio.create_task(
        endpoint(request, ModelDownloadRequest(repo_id="org/model", local_dir="C:/two"))
    )
    while len(entered) < 2:
        await asyncio.sleep(0)
    assert entered == ["C:/one", "C:/two"]

    release.set()
    assert (await first)["ok"] is True
    assert (await other)["ok"] is True


@pytest.mark.asyncio
async def test_local_download_launch_claim_releases_after_failure():
    endpoint = _route_endpoint("/api/model/download", "POST")
    calls = 0

    async def failed_impl(request, req, download_key=None):
        nonlocal calls
        calls += 1
        return {"ok": False, "error": "launch failed"}

    for name, cell in zip(endpoint.__code__.co_freevars, endpoint.__closure__ or ()):
        if name == "_model_download_impl":
            cell.cell_contents = failed_impl
            break

    request = _admin_request("/api/model/download")
    req = ModelDownloadRequest(repo_id="org/model")
    assert (await endpoint(request, req))["ok"] is False
    assert (await endpoint(request, ModelDownloadRequest(repo_id="org/model")))["ok"] is False
    assert calls == 2


def test_local_download_guard_present_without_remote_windows():
    src = Path(cookbook_routes.__file__).read_text(encoding="utf-8")
    assert "_find_live_local_download" in src
    assert "_clear_download_stopped" in src
    assert "_find_live_remote_windows_download" not in src


@pytest.mark.asyncio
async def test_remote_windows_stop_uses_powershell_then_cleans_state(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    runner = tmp_path / "cookbook-deadbeef_run.ps1"
    runner.write_text("hf download org/model", encoding="utf-8")
    calls: list[tuple] = []

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    endpoint = _route_endpoint("/api/cookbook/stop-session", "POST")
    req = SimpleNamespace(
        session_id="cookbook-deadbeef",
        remote_host="winbox",
        ssh_port="2222",
        platform="windows",
        repo_id="org/model",
        local_dir="C:/models",
        include="*.gguf",
        disable_hf_transfer=False,
        task_type="download",
    )

    result = await endpoint(_admin_request("/api/cookbook/stop-session"), req)

    assert result["ok"] is True
    assert calls
    assert calls[0][:7] == (
        "ssh",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "StrictHostKeyChecking=no",
        "-p",
        "2222",
    )
    assert "taskkill /F /T /PID" in str(calls[0][-1])
    assert not runner.exists()
    marker = (tmp_path / "cookbook-stopped-repos.json").read_text(encoding="utf-8")
    assert _download_target_key("org/model", "C:/models", "*.gguf") in marker


@pytest.mark.asyncio
async def test_remote_windows_stop_failure_preserves_recovery_state(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    runner = tmp_path / "cookbook-deadbeef_run.ps1"
    runner.write_text("hf download org/model", encoding="utf-8")

    class FailedProc:
        returncode = 255

        async def communicate(self):
            return b"", b"ssh failed"

    async def failed_exec(*args, **kwargs):
        return FailedProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failed_exec)
    endpoint = _route_endpoint("/api/cookbook/stop-session", "POST")
    req = SimpleNamespace(
        session_id="cookbook-deadbeef",
        remote_host="winbox",
        ssh_port="22",
        platform="windows",
        repo_id="org/model",
        local_dir=None,
        include=None,
        disable_hf_transfer=False,
        task_type="download",
    )

    result = await endpoint(_admin_request("/api/cookbook/stop-session"), req)

    assert result["ok"] is False
    assert result["exit_code"] == 255
    assert runner.exists()
    assert not (tmp_path / "cookbook-deadbeef.stop").exists()
    assert not (tmp_path / "cookbook-stopped-repos.json").exists()


@pytest.mark.asyncio
async def test_local_windows_stop_failure_preserves_recovery_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        cookbook_routes, "COOKBOOK_STATE_FILE", str(tmp_path / "state.json")
    )
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(cookbook_routes, "pid_alive", lambda pid: True)
    runner = tmp_path / "cookbook-deadbeef_run.ps1"
    pid_file = tmp_path / "cookbook-deadbeef.pid"
    runner.write_text("hf download org/model", encoding="utf-8")
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
async def test_local_windows_stop_does_not_kill_other_same_repo_target(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    target = "cookbook-deadbeef"
    other = "cookbook-feedface"
    target_key = _download_target_key("org/model", "C:/one", "*.gguf")
    other_key = _download_target_key("org/model", "C:/two", "*.safetensors")
    (tmp_path / f"{target}.sh").write_text("outer", encoding="utf-8")
    (tmp_path / f"{target}_run.sh").write_text(
        f"_ODYSSEUS_DOWNLOAD_KEY={target_key}\nhf download org/model --include '*.gguf'",
        encoding="utf-8",
    )
    (tmp_path / f"{target}.pid").write_text("111", encoding="utf-8")
    (tmp_path / f"{other}.sh").write_text("outer", encoding="utf-8")
    (tmp_path / f"{other}_run.sh").write_text(
        f"_ODYSSEUS_DOWNLOAD_KEY={other_key}\n"
        "hf download org/model --include '*.safetensors'",
        encoding="utf-8",
    )
    (tmp_path / f"{other}.pid").write_text("222", encoding="utf-8")
    monkeypatch.setattr(
        cookbook_routes, "pid_alive", lambda pid: pid == 222
    )

    def session_scan_only(args, **kwargs):
        assert args[0] == "powershell"
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(cookbook_routes.subprocess, "run", session_scan_only)
    endpoint = _route_endpoint("/api/cookbook/stop-session", "POST")
    req = SimpleNamespace(
        session_id=target,
        remote_host=None,
        ssh_port=None,
        platform="windows",
        repo_id="org/model",
        local_dir="C:/one",
        include="*.gguf",
        task_type="download",
    )

    result = await endpoint(_admin_request("/api/cookbook/stop-session"), req)

    assert result["ok"] is False
    assert other in result["error"]
    assert (tmp_path / f"{other}.pid").exists()
    assert not (tmp_path / f"{target}.stop").exists()


@pytest.mark.asyncio
async def test_local_windows_already_gone_stop_is_success(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        cookbook_routes, "COOKBOOK_STATE_FILE", str(tmp_path / "state.json")
    )
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(cookbook_routes, "pid_alive", lambda pid: False)
    runner = tmp_path / "cookbook-deadbeef_run.sh"
    runner.write_text("hf download org/model", encoding="utf-8")

    def empty_scan(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(cookbook_routes.subprocess, "run", empty_scan)
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
    assert result["stopped"] is False
    assert not runner.exists()
    assert (tmp_path / "cookbook-deadbeef.stop").exists()
