"""Pinned OpenCode and Hermes distribution binaries for Agent Server PATH."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
OVERLAY = ROOT / "docker-compose.openhands.yml"
VERSIONS = ROOT / "deploy" / "openhands" / "versions.env"
INSTALLER = ROOT / "scripts" / "install_openhands_runtime_bin.py"
RUNTIME_BIN = ROOT / "data" / "openhands-runtime-bin"
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.openhands.yml")
AGENT_SERVER = "openhands-agent-server"
OPENCODE_RELEASE = "https://github.com/anomalyco/opencode/releases/download/"
HERMES_TAG_INSTALLER = (
    "https://raw.githubusercontent.com/NousResearch/hermes-agent/"
)


def _versions() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in VERSIONS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *(item for file in COMPOSE_FILES for item in ("-f", file)), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_runtime_bin_installer_pins_official_tagged_distributions():
    text = INSTALLER.read_text(encoding="utf-8")
    values = _versions()
    assert INSTALLER.is_file()
    assert values["OPENCODE_VERSION"] == "v1.18.29"
    assert values["HERMES_VERSION"] == "v2026.9.7"
    assert values["OPENCODE_LINUX_X64_SHA256"]
    assert values["OPENCODE_LINUX_ARM64_SHA256"]
    assert values["HERMES_GIT_COMMIT"]
    assert "latest" not in text.lower() or "refuse" in text.lower() or "must not" in text.lower()
    assert OPENCODE_RELEASE in text
    assert f"{HERMES_TAG_INSTALLER}{values['HERMES_VERSION']}/scripts/install.sh" in text
    assert "hermes-agent/main/scripts/install.sh" not in text
    assert "openhands-runtime-bin" in text
    assert "OPENCODE_VERSION" in text
    assert "HERMES_VERSION" in text


def test_overlay_exposes_runtime_bin_on_agent_server_path():
    compose = OVERLAY.read_text(encoding="utf-8")
    assert "openhands-runtime-bin:/opt/oh-bin:ro" in compose
    assert "PATH: /opt/oh-bin:" in compose or "PATH: /opt/oh-bin/" in compose


def test_host_runtime_bin_has_pinned_opencode_and_hermes():
    opencode = RUNTIME_BIN / "opencode"
    hermes = RUNTIME_BIN / "hermes"
    assert opencode.is_file(), "run scripts/install_openhands_runtime_bin.py"
    assert hermes.is_file(), "run scripts/install_openhands_runtime_bin.py"
    assert opencode.stat().st_mode & 0o111
    assert hermes.stat().st_mode & 0o111


def test_agent_server_executes_pinned_opencode_and_hermes():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; live runtime-bin unproven")
    values = _versions()
    probe = _compose(
        "exec",
        "-T",
        AGENT_SERVER,
        "python",
        "-c",
        r"""
import json, shutil, subprocess
from pathlib import Path

def version(cmd):
    path = shutil.which(cmd) or (f"/opt/oh-bin/{cmd}" if Path(f"/opt/oh-bin/{cmd}").is_file() else "")
    if not path:
        return {"cmd": cmd, "path": "", "version": "", "returncode": 127}
    proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30)
    text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return {"cmd": cmd, "path": path, "version": text[:200], "returncode": proc.returncode}

print(json.dumps({"opencode": version("opencode"), "hermes": version("hermes")}))
""",
    )
    if probe.returncode != 0:
        raise AssertionError(probe.stderr[-400:] or probe.stdout[-400:])
    payload = json.loads(probe.stdout.strip().splitlines()[-1])
    opencode = payload["opencode"]
    hermes = payload["hermes"]
    assert opencode["path"].endswith("/opencode")
    assert opencode["returncode"] == 0
    assert values["OPENCODE_VERSION"].lstrip("v") in opencode["version"]
    assert hermes["path"].endswith("/hermes")
    assert hermes["returncode"] == 0
    hermes_ver = hermes["version"].lower()
    assert "hermes" in hermes_ver
    pin = (RUNTIME_BIN / ".hermes-pin.json").read_text(encoding="utf-8")
    assert values["HERMES_VERSION"] in pin
    assert values["HERMES_GIT_COMMIT"] in pin
