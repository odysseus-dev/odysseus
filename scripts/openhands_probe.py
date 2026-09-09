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


def probe_stack() -> list[ProbeResult]:
    config = _compose("config", "--format", "json")
    if config.returncode:
        return [ProbeResult("compose", False, {"stderr": config.stderr.strip()})]
    configured = json.loads(config.stdout).get("services", {})
    state = _compose("ps", "--format", "json")
    running = {
        row["Service"]: row
        for line in state.stdout.splitlines()
        if line.strip()
        for row in [json.loads(line)]
    }
    versions = _versions()
    results = []
    for name, pin_key in SERVICES.items():
        service = configured.get(name, {})
        row = running.get(name, {})
        expected = versions.get(pin_key) if pin_key else None
        image = service.get("image")
        health = row.get("Health", "")
        running_state = row.get("State") == "running"
        passed = bool(service) and running_state and (not expected or image == expected) and (not health or health == "healthy")
        results.append(ProbeResult(name, passed, {"expected_image": expected, "configured_image": image, "state": row.get("State"), "health": health, "networks": service.get("networks", {})}))
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
