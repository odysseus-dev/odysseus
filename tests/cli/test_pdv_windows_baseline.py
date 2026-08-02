"""Behavioral tests for the PDV native-Windows boundary verifier."""

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "pdv_verify_native_windows_baseline.ps1"
LIFECYCLE_SCRIPT = Path(__file__).parents[2] / "scripts" / "pdv_windows_lifecycle.ps1"
KEY_INIT_SCRIPT = Path(__file__).parents[2] / "scripts" / "pdv_initialize_adapter_key.ps1"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is required for the Windows boundary verifier")
    return subprocess.run(
        [pwsh, "-NoProfile", "-File", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _run_lifecycle(*args: str) -> subprocess.CompletedProcess[str]:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is required for the Windows lifecycle wrapper")
    return subprocess.run(
        [pwsh, "-NoProfile", "-File", str(LIFECYCLE_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _run_lifecycle_file_capture(output_root: Path, label: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Avoid Windows daemon descendants retaining pytest's capture pipe."""
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is required for the Windows lifecycle wrapper")
    stdout_path = output_root / f"{label}.stdout"
    stderr_path = output_root / f"{label}.stderr"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        result = subprocess.run(
            [pwsh, "-NoProfile", "-File", str(LIFECYCLE_SCRIPT), *args],
            stdout=stdout,
            stderr=stderr,
            text=True,
            timeout=30,
            check=False,
        )
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        stdout_path.read_text(encoding="utf-8-sig"),
        stderr_path.read_text(encoding="utf-8-sig"),
    )


def _run_key_init(*args: str) -> subprocess.CompletedProcess[str]:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is required for adapter-key initialization")
    return subprocess.run(
        [pwsh, "-NoProfile", "-File", str(KEY_INIT_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _initialized_key(repo: Path) -> Path:
    key_file = repo / "data" / "pdv-integration-v1" / "adapter.key"
    result = _run_key_init("-RepositoryRoot", str(repo), "-KeyFile", str(key_file), "-Json")
    assert result.returncode == 0, result.stderr or result.stdout
    return key_file


def _make_checkout(tmp_path: Path, origin: str) -> tuple[Path, str]:
    repo = tmp_path / "checkout"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "dev"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=repo, check=True)
    for name, content in {
        "LICENSE": "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3\n",
        "requirements.txt": "fastapi\n",
        "requirements-optional.txt": "markitdown\n",
        "pyproject.toml": "[tool.pytest.ini_options]\n",
        "package.json": "{}\n",
        "package-lock.json": "{}\n",
        "setup.py": "# setup marker\n",
    }.items():
        (repo / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=PDV Baseline Test",
            "-c",
            "user.email=pdv-baseline@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, head


def test_verifier_reports_safe_canonical_checkout(tmp_path):
    repo, head = _make_checkout(tmp_path, "https://github.com/odysseus-dev/odysseus.git")

    result = _run(
        "-RepositoryRoot",
        str(repo),
        "-ExpectedUpstreamCommit",
        head,
        "-Json",
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["repository"]["origin"] == "https://github.com/odysseus-dev/odysseus.git"
    assert report["repository"]["expectedUpstreamCommit"] == head
    assert report["repository"]["expectedCommitIsAncestor"] is True
    assert report["license"]["spdx"] == "AGPL-3.0-or-later"
    assert report["guardrails"]["dockerInvoked"] is False
    assert report["guardrails"]["portsTouched"] == []


def test_verifier_rejects_noncanonical_origin(tmp_path):
    repo, head = _make_checkout(tmp_path, "https://example.invalid/not-odysseus.git")

    result = _run(
        "-RepositoryRoot",
        str(repo),
        "-ExpectedUpstreamCommit",
        head,
        "-Json",
    )

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "canonical origin" in report["errors"]


def test_lifecycle_check_enforces_loopback_and_secret_reference(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("# app marker\n", encoding="utf-8")
    key_file = _initialized_key(repo)

    result = _run_lifecycle(
        "-Action",
        "Check",
        "-RepositoryRoot",
        str(repo),
        "-AdapterKeyFile",
        str(key_file),
        "-PythonExecutable",
        shutil.which("python") or "python",
        "-ExecutionOsUrl",
        "http://127.0.0.1:4310",
        "-Port",
        "7000",
        "-Json",
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["bindHost"] == "127.0.0.1"
    assert report["adapterKeyReferenceConfigured"] is True
    assert report["adapterKeyReadable"] is True
    assert report["reservedPorts"] == [11435, 11436]
    assert report["portsTouched"] == []
    assert key_file.read_text(encoding="ascii") not in result.stdout
    assert str(key_file) not in result.stdout
    assert report["adapterKeyAclRestricted"] is True
    assert report["executionOsConfigured"] is True


def test_lifecycle_rejects_reserved_model_ports_without_starting(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("# app marker\n", encoding="utf-8")
    key_file = _initialized_key(repo)

    result = _run_lifecycle(
        "-Action",
        "Check",
        "-RepositoryRoot",
        str(repo),
        "-AdapterKeyFile",
        str(key_file),
        "-PythonExecutable",
        shutil.which("python") or "python",
        "-ExecutionOsUrl",
        "http://127.0.0.1:4310",
        "-Port",
        "11435",
        "-Json",
    )

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "reserved model port" in report["errors"]
    assert report["processStarted"] is False


def test_adapter_key_initializer_creates_once_and_returns_fingerprint_only(tmp_path):
    import hashlib

    repo = tmp_path / "repo"
    key_file = repo / "data" / "pdv-integration-v1" / "adapter.key"
    repo.mkdir()

    first = _run_key_init(
        "-RepositoryRoot", str(repo), "-KeyFile", str(key_file), "-Json"
    )

    assert first.returncode == 0, first.stderr or first.stdout
    first_report = json.loads(first.stdout)
    key_bytes = key_file.read_bytes()
    assert len(key_bytes) == 64
    assert key_bytes.decode("ascii") == key_bytes.decode("ascii").lower()
    assert len(bytes.fromhex(key_bytes.decode("ascii"))) == 32
    assert first_report == {
        "ok": True,
        "created": True,
        "fingerprintSha256": hashlib.sha256(key_bytes).hexdigest(),
        "aclRestricted": True,
        "keyBytes": 64,
    }
    assert str(key_file) not in first.stdout
    assert key_bytes.hex() not in first.stdout

    second = _run_key_init(
        "-RepositoryRoot", str(repo), "-KeyFile", str(key_file), "-Json"
    )

    assert second.returncode == 0, second.stderr or second.stdout
    second_report = json.loads(second.stdout)
    assert second_report["created"] is False
    assert second_report["fingerprintSha256"] == first_report["fingerprintSha256"]
    assert key_file.read_bytes() == key_bytes


def test_adapter_key_initializer_rejects_target_outside_runtime_boundary(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.key"

    result = _run_key_init(
        "-RepositoryRoot", str(repo), "-KeyFile", str(outside), "-Json"
    )

    assert result.returncode != 0
    assert not outside.exists()
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert report["error"] == "key target must stay inside the PDV runtime directory"
    assert str(outside) not in result.stdout


def test_lifecycle_start_and_stop_verify_exact_process_receipt(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/api/health')\ndef health(): return {'status': 'healthy'}\n",
        encoding="utf-8",
    )
    key_file = _initialized_key(repo)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    start = _run_lifecycle_file_capture(
        tmp_path, "start",
        "-Action", "Start", "-RepositoryRoot", str(repo), "-AdapterKeyFile", str(key_file),
        "-ExecutionOsUrl", "http://127.0.0.1:4310", "-PythonExecutable", sys.executable, "-Port", str(port), "-Json",
    )
    try:
        assert start.returncode == 0, start.stderr or start.stdout
        start_report = json.loads(start.stdout)
        assert start_report["processStarted"] is True
        receipt = json.loads((repo / "data" / "pdv-integration-v1" / "odysseus.pid").read_text(encoding="utf-8-sig"))
        assert receipt["schemaVersion"] == 1
        assert receipt["repositoryRoot"] == str(repo.resolve())
        assert receipt["port"] == port
        assert receipt["pid"] == start_report["processId"]
        restart = _run_lifecycle_file_capture(
            tmp_path, "restart",
            "-Action", "Restart", "-RepositoryRoot", str(repo), "-AdapterKeyFile", str(key_file),
            "-ExecutionOsUrl", "http://127.0.0.1:4310", "-PythonExecutable", sys.executable, "-Port", str(port), "-Json",
        )
        assert restart.returncode == 0, restart.stderr or restart.stdout
        restart_report = json.loads(restart.stdout)
        assert restart_report["action"] == "Restart"
        assert restart_report["processStopped"] is True
        assert restart_report["processStarted"] is True
        assert restart_report["processId"] != start_report["processId"]
    finally:
        stop = _run_lifecycle_file_capture(
            tmp_path, "stop",
            "-Action", "Stop", "-RepositoryRoot", str(repo), "-AdapterKeyFile", str(key_file),
            "-ExecutionOsUrl", "http://127.0.0.1:4310", "-PythonExecutable", sys.executable, "-Port", str(port), "-Json",
        )
    assert stop.returncode == 0, stop.stderr or stop.stdout
    assert json.loads(stop.stdout)["processStopped"] is True
