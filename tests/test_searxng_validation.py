"""Native checks must validate the selected runtime without registry credentials."""

import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_ci_checks_the_pinned_upstream_image_on_both_native_architectures():
    workflow = yaml.safe_load((ROOT / ".github/workflows/searxng-image.yml").read_text())
    events = workflow.get("on", workflow.get(True))
    assert "pull_request_target" not in events
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"validate"}
    job = workflow["jobs"]["validate"]
    matrix = job["strategy"]["matrix"]["include"]
    assert {(row["arch"], row["runner"]) for row in matrix} == {
        ("amd64", "ubuntu-latest"), ("arm64", "ubuntu-24.04-arm")
    }
    steps = job["steps"]
    assert not any("login-action" in step.get("uses", "") for step in steps)
    assert all(step.get("with", {}).get("persist-credentials") is False
               for step in steps if "actions/checkout@" in step.get("uses", ""))
    offline = next(step for step in steps if "docker/build-push-action@" in step.get("uses", ""))
    assert offline["with"]["network"] == "none"
    assert offline["with"]["build-args"] == "SEARXNG_IMAGE=${{ steps.image.outputs.image }}"
    live = next(step for step in steps if "validate-searxng-image.sh" in step.get("run", ""))
    assert live["env"]["SEARXNG_TEST_IMAGE"] == "${{ steps.image.outputs.image }}"


@pytest.mark.parametrize("failure,expected", [("none", 0), ("exec", 7), ("up", 1), ("existing-volume", 1)])
def test_compose_probe_owns_only_its_temporary_resources(tmp_path, failure, expected):
    """Exercise the real helper with a Docker double, including failure cleanup."""
    binary = tmp_path / "docker"
    calls = tmp_path / "calls.jsonl"
    binary.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["DOCKER_CALLS"], "a") as log:
    log.write(json.dumps({"args": args, "volume": os.environ["SEARXNG_VALIDATION_VOLUME"], "secret": os.environ["SEARXNG_SECRET"]}) + "\\n")
failure = os.environ["DOCKER_FAILURE"]
if args[:2] == ["volume", "inspect"]:
    sys.exit(0 if failure == "existing-volume" else 1)
if "exec" in args and failure == "exec":
    sys.exit(7)
if "up" in args and failure == "up":
    sys.exit(9)
''')
    binary.chmod(0o755)
    result = subprocess.run(["bash", str(ROOT / ".github/scripts/validate-searxng-image.sh"), "localhost/searxng:validated"], env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"], "DOCKER_CALLS": str(calls), "DOCKER_FAILURE": failure, "SEARXNG_SECRET": "operator-secret-must-not-propagate"}, capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
    entries = [json.loads(line) for line in calls.read_text().splitlines()]
    assert all(entry["secret"] == "searxng-ci-validation-only" for entry in entries)
    commands = [entry["args"] for entry in entries]
    if failure == "existing-volume":
        assert len(commands) == 1
        return
    up = next(command for command in commands if "up" in command)
    assert up[up.index("--env-file") + 1] == "/dev/null"
    assert "--no-deps" in up and "--no-build" in up and "--wait" in up
    assert up[up.index("--pull") + 1] == "never"
    down = next(command for command in commands if "down" in command)
    assert "--volumes" not in down
    assert commands[-1] == ["volume", "rm", entries[-1]["volume"]]
    if failure != "up":
        execute = next(command for command in commands if "exec" in command)
        assert execute[-2:] == ["--running", "--live"]
