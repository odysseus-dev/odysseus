"""Local-Windows cookbook stop-session / orphan guard tests (PR1 scope)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import routes.cookbook_routes as cookbook_routes
from routes.cookbook_routes import _cmdline_references_hf_repo, _coerce_ssh_port


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

    async def fake_impl(session_id, remote_host="", ssh_port=None, platform="", repo_id=None):
        seen.append({"repo_id": repo_id, "platform": platform})
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


def test_local_download_guard_present_without_remote_windows():
    src = Path(cookbook_routes.__file__).read_text(encoding="utf-8")
    assert "_find_live_local_download" in src
    assert "_clear_download_stopped" in src
    assert "_find_live_remote_windows_download" not in src


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
