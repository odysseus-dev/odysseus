#!/usr/bin/env python3
"""Read-only readiness and pin probe for the OpenHands Compose overlay."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.openhands.yml")
SERVICES = {
    "openhands-agent-server": "OPENHANDS_AGENT_SERVER_IMAGE",
    "openhands-automation": "OPENHANDS_AUTOMATION_IMAGE",
    "openhands-canvas": "OPENHANDS_CANVAS_IMAGE",
    "odysseus-mcp": None,
}
HEALTH_URLS = {
    "openhands-agent-server": "http://127.0.0.1:8000/health",
    "openhands-automation": "http://127.0.0.1:8000/health",
    "openhands-canvas": "http://127.0.0.1:8000/health",
    "odysseus-mcp": "http://127.0.0.1:7000/api/health",
}
CONNECTIVITY = {
    "openhands-automation": "http://openhands-agent-server:8000/health",
    "openhands-canvas": "http://openhands-agent-server:8000/health",
    "odysseus-mcp": "http://openhands-agent-server:8000/health",
}


@dataclass(frozen=True)
class ProbeResult:
    name: str
    passed: bool
    evidence: dict[str, object]


def _versions() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / "deploy/openhands/versions.env").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *(item for file in COMPOSE_FILES for item in ("-f", file)), *args],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )


def _exec(service: str, url: str) -> subprocess.CompletedProcess[str]:
    return _compose("exec", "-T", service, "python", "-c", "import urllib.request; urllib.request.urlopen(" + repr(url) + ", timeout=3).read()")


def probe_stack() -> list[ProbeResult]:
    config = _compose("config", "--format", "json")
    if config.returncode:
        return [ProbeResult("compose", False, {"stderr": config.stderr.strip()})]
    try:
        configured = json.loads(config.stdout).get("services", {})
    except json.JSONDecodeError as error:
        return [ProbeResult("compose", False, {"error": "invalid_config_json", "detail": str(error)})]
    state = _compose("ps", "--format", "json")
    if state.returncode:
        return [ProbeResult("compose", False, {"error": "ps_failed", "stderr": state.stderr.strip()})]
    try:
        running = {row["Service"]: row for line in state.stdout.splitlines() if line.strip() for row in [json.loads(line)]}
    except (json.JSONDecodeError, KeyError) as error:
        return [ProbeResult("compose", False, {"error": "invalid_ps_json", "detail": str(error)})]
    versions = _versions()
    results = []
    for name, pin_key in SERVICES.items():
        service = configured.get(name, {})
        row = running.get(name, {})
        expected = versions.get(pin_key) if pin_key else None
        image = service.get("image")
        health = row.get("Health", "")
        running_state = row.get("State") == "running"
        health_probe = _exec(name, HEALTH_URLS[name]) if running_state else None
        connectivity_probe = _exec(name, CONNECTIVITY[name]) if name in CONNECTIVITY and running_state else None
        passed = bool(service) and running_state and health == "healthy" and (not expected or image == expected) and bool(health_probe and health_probe.returncode == 0) and (not connectivity_probe or connectivity_probe.returncode == 0)
        results.append(ProbeResult(name, passed, {"expected_image": expected, "configured_image": image, "state": row.get("State"), "health": health, "health_url": HEALTH_URLS[name], "health_exit": health_probe.returncode if health_probe else None, "connectivity_url": CONNECTIVITY.get(name), "connectivity_exit": connectivity_probe.returncode if connectivity_probe else None, "networks": service.get("networks", {})}))
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", choices=["stack"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = probe_stack()
    payload = [asdict(result) for result in results]
    print(json.dumps(payload if args.json else {"results": payload}, sort_keys=True))
    return 0 if results and all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
