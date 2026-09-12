"""Live native, OpenCode, and Hermes inference must go through 9router."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.openhands.yml")
AGENT_SERVER = "openhands-agent-server"
NINE_ROUTER_V1 = "http://9router:20128/v1"
DIRECT_PROVIDER_MARKERS = (
    "chatgpt.com",
    "api.openai.com",
    "api.anthropic.com",
    "opencode.ai/zen",
)


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *(item for file in COMPOSE_FILES for item in ("-f", file)), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_live_opencode_binary_infers_through_9router():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; live OpenCode ACP unproven")
    probe = _compose(
        "exec",
        "-T",
        AGENT_SERVER,
        "sh",
        "-c",
        "export OPENCODE_CONFIG=${OPENCODE_CONFIG:-/home/opencode/.config/opencode/opencode.json}; "
        "export HOME=/home/opencode; "
        "timeout 40 /opt/oh-bin/opencode run --print-logs 'Reply with the single word pong.'",
    )
    text = (probe.stdout or "") + "\n" + (probe.stderr or "")
    if "opencode: not found" in text or "No such file" in text and "opencode" in text:
        pytest.skip("OpenCode binary absent from /opt/oh-bin")
    assert "ninerouter" in text, text[-800:]
    assert "9router" in text or "ninerouter" in text
    for marker in DIRECT_PROVIDER_MARKERS:
        assert marker not in text
    assert "No active credentials" in text or "AI_APICallError" in text or "pong" in text.lower()


def test_live_hermes_binary_infers_through_9router():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; live Hermes ACP unproven")
    probe = _compose(
        "exec",
        "-T",
        AGENT_SERVER,
        "sh",
        "-c",
        "export HERMES_HOME=/home/hermes/.hermes; "
        "export HOME=/home/hermes; "
        "timeout 45 /opt/oh-bin/hermes -z 'Reply with the single word pong.' --cli --yolo",
    )
    text = (probe.stdout or "") + "\n" + (probe.stderr or "")
    if "hermes: not found" in text or "No such file" in text and "hermes" in text:
        pytest.skip("Hermes binary absent from /opt/oh-bin")
    assert "kiro" in text or "ninerouter" in text or "9router" in text, text[-800:]
    assert "No active credentials" in text or "HTTP 404" in text or "pong" in text.lower()
    for marker in DIRECT_PROVIDER_MARKERS:
        assert marker not in text


def test_live_native_completions_still_target_9router():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; live native 9router unproven")
    script = r"""
import json, urllib.error, urllib.request
settings = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/api/settings", timeout=8).read().decode())
llm = (settings.get("agent_settings") or {}).get("llm") or {}
base = str(llm.get("base_url") or "").rstrip("/")
key = llm.get("api_key")
if isinstance(key, dict):
    key = key.get("secret") or key.get("value")
req = urllib.request.Request(
    base + "/chat/completions",
    data=json.dumps({
        "model": str(llm.get("model") or "openai/auto").split("/", 1)[-1],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }).encode(),
    method="POST",
    headers={"Content-Type": "application/json"},
)
if key:
    req.add_header("Authorization", "Bearer " + str(key))
try:
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read().decode()
        print(json.dumps({"status": response.status, "body": body[:800], "target": base}))
except urllib.error.HTTPError as exc:
    err = exc.read().decode() if exc.fp else ""
    print(json.dumps({"status": exc.code, "body": err[:800], "target": base}))
except Exception as exc:
    print(json.dumps({"status": 0, "body": str(exc), "target": base}))
"""
    probe = _compose("exec", "-T", AGENT_SERVER, "python", "-c", script)
    if probe.returncode != 0:
        pytest.skip(f"native completions probe failed: {probe.stderr[-300:]}")
    payload = json.loads(probe.stdout.strip().splitlines()[-1])
    assert NINE_ROUTER_V1.rstrip("/") in str(payload.get("target") or "")
    body = str(payload.get("body") or "")
    for marker in DIRECT_PROVIDER_MARKERS:
        assert marker not in body
        assert marker not in str(payload.get("target") or "")
    status = int(payload.get("status") or 0)
    assert status != 0, body
    assert status in {200, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503}
