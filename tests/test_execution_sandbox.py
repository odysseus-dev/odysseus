"""Linux sandbox invariants for model-requested process execution."""

import asyncio
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from src.execution_sandbox import (
    SandboxUnavailable,
    environment_for_sandbox_launcher,
    sandbox_command,
)


def test_sandbox_argv_is_positive_mount_networkless_and_clearenv(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    argv = sandbox_command(["/bin/bash", "-c", "true"], workspace=str(workspace))

    assert "--unshare-all" in argv
    assert "--clearenv" in argv
    assert "/usr/bin/prlimit" in argv
    assert "--nproc=256" in argv
    assert "--as=4294967296" in argv
    assert ["--ro-bind", "/", "/"] not in [
        argv[index:index + 3] for index in range(len(argv) - 2)
    ]
    bind_index = argv.index("--bind")
    assert argv[bind_index + 1:bind_index + 3] == [
        str(workspace),
        str(workspace),
    ]
    assert environment_for_sandbox_launcher() == {}
    assert "OPENAI_API_KEY" not in argv


def test_sandbox_overlays_credentials_and_protects_git(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("SECRET=value", encoding="utf-8")
    (workspace / ".git").mkdir()
    (workspace / ".ssh").mkdir()

    argv = sandbox_command(["/bin/true"], workspace=str(workspace))

    triples = [argv[index:index + 3] for index in range(len(argv) - 2)]
    pairs = [argv[index:index + 2] for index in range(len(argv) - 1)]
    assert ["--ro-bind", "/dev/null", str(workspace / ".env")] in triples
    assert [
        "--ro-bind",
        str(workspace / ".git"),
        str(workspace / ".git"),
    ] in triples
    assert ["--tmpfs", str(workspace / ".ssh")] in pairs


def test_sandbox_rejects_broad_workspace():
    with pytest.raises(SandboxUnavailable):
        sandbox_command(["/bin/true"], workspace="/")


def test_sandbox_hides_odysseus_data_inside_broader_workspace(
    tmp_path,
    monkeypatch,
):
    import src.constants as constants

    workspace = tmp_path / "app"
    data_dir = workspace / "data"
    logs_dir = workspace / "logs"
    agent_dir = data_dir / "agent_workspace"
    data_dir.mkdir(parents=True)
    logs_dir.mkdir()
    agent_dir.mkdir()
    (data_dir / "app.db").write_text("private", encoding="utf-8")
    (data_dir / ".env").write_text("PRIVATE=value", encoding="utf-8")
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(constants, "LOGS_DIR", str(logs_dir))
    monkeypatch.setattr(constants, "AGENT_WORKSPACE_DIR", str(agent_dir))
    monkeypatch.setattr(constants, "MAIL_ATTACHMENTS_DIR", str(data_dir / "mail"))

    argv = sandbox_command(
        [
            "/bin/bash",
            "-c",
            "test ! -e data/app.db && test ! -e logs/private.log",
        ],
        workspace=str(workspace),
    )

    pairs = [argv[index:index + 2] for index in range(len(argv) - 1)]
    assert ["--tmpfs", str(data_dir)] in pairs
    assert ["--tmpfs", str(logs_dir)] in pairs
    completed = subprocess.run(
        argv,
        cwd=str(workspace),
        env={},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_sandbox_allows_only_dedicated_workspace_below_data(
    tmp_path,
    monkeypatch,
):
    import src.constants as constants

    data_dir = tmp_path / "data"
    agent_dir = data_dir / "agent_workspace"
    private_dir = data_dir / "personal_docs"
    agent_dir.mkdir(parents=True)
    private_dir.mkdir()
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(constants, "LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(constants, "AGENT_WORKSPACE_DIR", str(agent_dir))
    monkeypatch.setattr(constants, "MAIL_ATTACHMENTS_DIR", str(data_dir / "mail"))

    assert sandbox_command(["/bin/true"], workspace=str(agent_dir))
    with pytest.raises(SandboxUnavailable):
        sandbox_command(["/bin/true"], workspace=str(private_dir))


def test_sandbox_hides_host_and_environment_at_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret"
    outside.write_text("outside", encoding="utf-8")
    (workspace / ".env").write_text("INSIDE_SECRET=value", encoding="utf-8")
    (workspace / ".git").mkdir()
    command = (
        "set -eu; "
        "test ! -e \"$1\"; "
        "test -z \"${OPENAI_API_KEY:-}\"; "
        "test ! -s .env; "
        "test ! -e /home; "
        "test ! -e /proc; "
        "touch allowed.txt; "
        "if touch .git/blocked 2>/dev/null; then exit 91; fi"
    )
    argv = sandbox_command(
        ["/bin/bash", "-c", command, "sandbox", str(outside)],
        workspace=str(workspace),
    )
    env = {"OPENAI_API_KEY": "must-not-cross"}

    completed = subprocess.run(
        argv,
        cwd=str(workspace),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (workspace / "allowed.txt").exists()
    assert not (workspace / ".git" / "blocked").exists()


def test_sandbox_network_namespace_has_no_external_route(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code = (
        "import socket; "
        "s=socket.socket(); s.settimeout(0.2); "
        "\ntry: s.connect(('127.0.0.1', 9))"
        "\nexcept OSError: raise SystemExit(0)"
        "\nraise SystemExit(1)"
    )
    argv = sandbox_command(
        ["/usr/bin/python3", "-I", "-c", code],
        workspace=str(workspace),
    )

    completed = subprocess.run(
        argv,
        cwd=str(workspace),
        env={},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_tmux_bash_shell_runs_inside_same_sandbox(tmp_path):
    from src.agent_tools.subprocess_tools import (
        _run_exec,
        _run_tmux_bash,
        _tmux_session_name,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret"
    outside.write_text("secret", encoding="utf-8")
    session_id = f"sandbox-test-{uuid.uuid4().hex}"
    session_name = _tmux_session_name(session_id, str(workspace))

    async def run():
        try:
            return await _run_tmux_bash(
                f"test ! -e {outside!s} && pwd && touch tmux-write.txt",
                session_id=session_id,
                cwd=str(workspace),
                timeout=10,
            )
        finally:
            await _run_exec(
                "tmux",
                "kill-session",
                "-t",
                session_name,
                timeout=3,
            )

    stdout, stderr, returncode, timed_out = asyncio.run(run())

    assert timed_out is False
    assert returncode == 0, stderr
    assert str(workspace) in stdout
    assert (workspace / "tmux-write.txt").exists()


def test_detached_background_job_uses_sandbox(tmp_path, monkeypatch):
    from src import bg_jobs

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", jobs_dir)
    monkeypatch.setattr(bg_jobs, "_STORE", tmp_path / "jobs.json")

    record = bg_jobs.launch(
        f"test ! -e {outside!s} && printf background-ok && touch bg-write.txt",
        session_id="sandbox-session",
        cwd=str(workspace),
        max_runtime_s=10,
    )
    deadline = time.time() + 10
    current = record
    while current.get("status") == "running" and time.time() < deadline:
        time.sleep(0.05)
        current = bg_jobs.get(record["id"]) or current

    assert current["status"] == "done", current
    assert current["exit_code"] == 0
    assert "background-ok" in current["output"]
    assert (workspace / "bg-write.txt").exists()
