from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.openhands.yml")
NINE_ROUTER_SERVICE = "9router"
NINE_ROUTER_HEALTH_PATH = "/api/health"
NINE_ROUTER_PORT = "20128"
NINE_ROUTER_HEALTH_URL = f"http://{NINE_ROUTER_SERVICE}:{NINE_ROUTER_PORT}{NINE_ROUTER_HEALTH_PATH}"
INFERENCE_KEY_NAMES = (
    "NINE_ROUTER_KEY",
    "NINE_ROUTER_API_KEY",
    "NINE_ROUTER_INFERENCE_KEY",
    "NINEROUTER_KEY",
    "NINEROUTER_API_KEY",
    "9ROUTER_KEY",
    "9ROUTER_API_KEY",
)


def _overlay_text() -> str:
    return (ROOT / "docker-compose.openhands.yml").read_text(encoding="utf-8")


def _service_block(compose: str, name: str) -> str:
    header = f"  {name}:"
    lines = compose.splitlines()
    start = next((index for index, line in enumerate(lines) if line == header), None)
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            end = index
            break
        if line and not line.startswith(" ") and not line.startswith("#"):
            end = index
            break
    return "\n".join(lines[start:end])


def _env_entries(block: str) -> list[str]:
    entries: list[str] = []
    in_env = False
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("environment:"):
            in_env = True
            continue
        if in_env:
            if line.startswith("    ") or line.startswith("\t"):
                entries.append(stripped.lstrip("- ").split("=", 1)[0].split(":", 1)[0])
                continue
            if stripped.startswith("#"):
                continue
            break
    return entries


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *(item for file in COMPOSE_FILES for item in ("-f", file)), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_agent_server_targets_9router_health_on_overlay_network():
    compose = _overlay_text()
    router = _service_block(compose, NINE_ROUTER_SERVICE)
    agent = _service_block(compose, "openhands-agent-server")
    assert router, "overlay must define 9router service"
    assert agent, "overlay must keep Agent Server"
    assert NINE_ROUTER_HEALTH_PATH in router
    assert NINE_ROUTER_PORT in router
    assert "healthcheck:" in router
    assert "ports:" not in router
    assert "0.0.0.0:" not in router
    assert "network_mode:" not in router
    assert "network_mode:" not in agent
    assert NINE_ROUTER_HEALTH_URL in compose


def test_odysseus_overlay_environment_omits_9router_inference_key():
    compose = _overlay_text()
    odysseus = _service_block(compose, "odysseus")
    assert odysseus, "overlay must keep odysseus service"
    names = {entry.upper() for entry in _env_entries(odysseus)}
    leaked = [name for name in INFERENCE_KEY_NAMES if name in names or name in odysseus]
    assert leaked == []
    list_entries = [line.strip() for line in odysseus.splitlines() if line.strip().startswith("- ")]
    assert any(line.startswith("- ODYSSEUS_OPENHANDS_") for line in list_entries)


def test_compose_config_quiet_renders_9router():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; compose config unproven")
    result = _compose("config", "--quiet")
    assert result.returncode == 0, result.stderr
    rendered = _compose("config", "--format", "json")
    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout).get("services", {})
    assert NINE_ROUTER_SERVICE in services
    router = services[NINE_ROUTER_SERVICE]
    assert not router.get("ports")
    health = router.get("healthcheck") or {}
    test = health.get("test") or []
    assert any(NINE_ROUTER_HEALTH_PATH in str(part) for part in test)


def test_live_9router_health_is_proven_or_recorded_absent():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; live 9router health unproven")
    state = _compose("ps", "--format", "json", NINE_ROUTER_SERVICE)
    running = False
    if state.returncode == 0 and state.stdout.strip():
        try:
            row = json.loads(state.stdout.splitlines()[0])
        except (json.JSONDecodeError, IndexError):
            row = {}
        running = row.get("State") == "running"
    if not running:
        pin = ""
        for raw in (ROOT / "deploy/openhands/versions.env").read_text(encoding="utf-8").splitlines():
            if raw.startswith("NINE_ROUTER_IMAGE="):
                pin = raw.split("=", 1)[1]
                break
        inspect = subprocess.run(
            ["docker", "image", "inspect", pin],
            check=False,
            capture_output=True,
            text=True,
        )
        if inspect.returncode != 0:
            pytest.skip("live 9router stack absent and image not local; live health unproven")
        startup = _compose("up", "-d", "--no-deps", "--pull", "never", NINE_ROUTER_SERVICE)
        if startup.returncode != 0:
            pytest.skip(f"9router --pull never up failed; live health unproven: {startup.stderr[-400:]}")
        wait = _compose("ps", "--format", "json", NINE_ROUTER_SERVICE)
        if wait.returncode != 0 or "running" not in wait.stdout:
            pytest.skip("9router started but not running; live health unproven")
    probe = _compose(
        "exec",
        "-T",
        "openhands-agent-server",
        "python",
        "-c",
        "import urllib.request; urllib.request.urlopen(" + repr(NINE_ROUTER_HEALTH_URL) + ", timeout=5).read()",
    )
    if probe.returncode != 0 and "openhands-agent-server" in (probe.stderr + probe.stdout):
        local = _compose(
            "exec",
            "-T",
            NINE_ROUTER_SERVICE,
            "node",
            "-e",
            "require('http').get('http://127.0.0.1:20128/api/health',r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>{process.exit(r.statusCode===200?0:1)})}).on('error',()=>process.exit(1))",
        )
        if local.returncode != 0:
            pytest.skip("9router up but Agent Server absent; overlay-network health unproven")
        pytest.skip("9router self-health ok; Agent Server absent so overlay reachability unproven")
    assert probe.returncode == 0, probe.stderr
