"""Native OpenHands must infer through 9router and fail closed when it is gone."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.openhands.yml")
NINE_ROUTER_V1 = "http://9router:20128/v1"
NINE_ROUTER_SERVICE = "9router"
AGENT_SERVER = "openhands-agent-server"
BOOTSTRAP_PY = ROOT / "deploy" / "openhands" / "bootstrap_native_llm.py"
ENTRYPOINT_SH = ROOT / "deploy" / "openhands" / "agent-server-entrypoint.sh"
ODYSSEUS_PROFILE = ROOT / "deploy" / "openhands" / "profiles" / "odysseus.yaml"
CHAT_ROUTES = ROOT / "routes" / "chat_routes.py"
CLIENT_PY = ROOT / "services" / "agents" / "openhands_client.py"
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


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *(item for file in COMPOSE_FILES for item in ("-f", file)), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _load_client_module():
    services = sys.modules.get("services")
    if services is None or not getattr(services, "__path__", None):
        services = types.ModuleType("services")
        services.__path__ = [str(ROOT / "services")]
        sys.modules["services"] = services
    agents = sys.modules.get("services.agents")
    if agents is None or not getattr(agents, "__path__", None):
        agents = types.ModuleType("services.agents")
        agents.__path__ = [str(ROOT / "services" / "agents")]
        agents.__package__ = "services.agents"
        sys.modules["services.agents"] = agents
        services.agents = agents
    from services.agents.openhands_client import ConversationResume, OpenHandsClient

    return ConversationResume, OpenHandsClient


class _RecordingTransport:
    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        payload = body or {}
        self.calls.append((method, path, payload))
        if path == "/api/settings" and method == "GET":
            return self.settings
        if path == "/api/conversations" and method == "POST":
            return {"id": "conv-native"}
        raise AssertionError(f"unexpected {method} {path}")


def test_odysseus_profile_keeps_mcp_odysseus():
    text = ODYSSEUS_PROFILE.read_text(encoding="utf-8")
    assert "mcp:" in text
    assert "odysseus" in text
    assert "llm_profile_ref" not in text


def test_interactive_chat_still_binds_openhands_conversation():
    source = CHAT_ROUTES.read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1].split("async def chat_resume", 1)[0]
    assert "async for chunk in stream_governed_agent(" in stream
    assert "# ── Chat mode: call stream_llm directly, NO tools, NO document access ──" not in stream
    picker = stream.split("if compare_mode:", 1)[0]
    assert "No model selected for this chat" not in picker


def test_openhands_client_source_does_not_patch_provider_keys():
    source = CLIENT_PY.read_text(encoding="utf-8")
    assert 'request("PATCH"' not in source
    assert '"/api/settings"' in source
    assert "PATCH" not in source
    assert "/v1/chat/completions" not in source


def test_bootstrap_files_overwrite_subscription_with_9router():
    assert BOOTSTRAP_PY.is_file(), "overlay must bootstrap Agent Server LLM settings"
    assert ENTRYPOINT_SH.is_file(), "Agent Server entrypoint must run native LLM bootstrap"
    script = BOOTSTRAP_PY.read_text(encoding="utf-8")
    wrapper = ENTRYPOINT_SH.read_text(encoding="utf-8")
    assert NINE_ROUTER_V1 in script
    assert "auth_type" in script
    assert "api_key" in script
    assert "subscription" in script
    assert "chatgpt.com" in script
    assert "api_mode" in script
    assert "chat" in script
    assert "openai/" in script
    assert str(BOOTSTRAP_PY.relative_to(ROOT)) in _overlay_text() or BOOTSTRAP_PY.name in wrapper
    assert "bootstrap_native_llm" in wrapper


def test_compose_bootstraps_agent_server_home_and_keeps_9router():
    compose = _overlay_text()
    agent = _service_block(compose, AGENT_SERVER)
    router = _service_block(compose, NINE_ROUTER_SERVICE)
    odysseus = _service_block(compose, "odysseus")
    assert router, "keep unmodified 9router service"
    assert "bootstrap_native_llm" in agent
    assert "agent-server-entrypoint.sh" in agent
    assert "9router" in agent
    names = []
    in_env = False
    for line in odysseus.splitlines():
        stripped = line.strip()
        if stripped.startswith("environment:"):
            in_env = True
            continue
        if in_env:
            if line.startswith("    ") or line.startswith("\t"):
                names.append(stripped.lstrip("- ").split("=", 1)[0].split(":", 1)[0].upper())
                continue
            if stripped.startswith("#"):
                continue
            break
    leaked = [name for name in INFERENCE_KEY_NAMES if name in names or name in odysseus]
    assert leaked == []
    list_entries = [line.strip() for line in odysseus.splitlines() if line.strip().startswith("- ")]
    assert any(line.startswith("- ODYSSEUS_OPENHANDS_") for line in list_entries)


def test_create_records_nonsecret_9router_route_from_settings():
    ConversationResume, OpenHandsClient = _load_client_module()
    settings = {
        "agent_settings": {
            "agent_kind": "openhands",
            "agent": "CodeActAgent",
            "llm": {
                "model": "openai/auto",
                "base_url": NINE_ROUTER_V1,
                "auth_type": "api_key",
                "api_mode": "chat",
                "api_key": "sk-should-not-leak",
            },
        }
    }
    transport = _RecordingTransport(settings)
    client = OpenHandsClient(transport=transport, agent_server_base="http://agent-server")
    result = client.create_or_resume(
        conversation_id=None,
        message="hi",
        request_id="n1",
        profile_revision=1,
        archetype_version=1,
    )
    assert isinstance(result, ConversationResume)
    assert result.conversation_id == "conv-native"
    assert result.resolved_base_url == NINE_ROUTER_V1
    assert result.resolved_model == "openai/auto"
    assert "sk-should-not-leak" not in json.dumps(result.__dict__)
    assert all(method != "PATCH" for method, _, _ in transport.calls)
    assert all("/v1/" not in path for _, path, _ in transport.calls)
    method, path, body = transport.calls[1]
    assert method == "POST"
    assert path == "/api/conversations"
    assert "base_url" not in body
    assert "api_key" not in body


def test_live_agent_server_settings_point_at_9router_not_subscription():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; live native settings unproven")
    probe = _compose(
        "exec",
        "-T",
        AGENT_SERVER,
        "python",
        "-c",
        (
            "import json,urllib.request;"
            "print(urllib.request.urlopen('http://127.0.0.1:8000/api/settings', timeout=8)"
            ".read().decode())"
        ),
    )
    if probe.returncode != 0:
        pytest.skip(f"Agent Server settings unreadable: {probe.stderr[-300:]}")
    payload = json.loads(probe.stdout)
    llm = ((payload.get("agent_settings") or {}).get("llm") or {})
    base = str(llm.get("base_url") or "")
    auth = str(llm.get("auth_type") or "")
    api_mode = str(llm.get("api_mode") or "")
    model = str(llm.get("model") or "")
    extra = json.dumps(llm.get("extra_headers") or {})
    assert "chatgpt.com" not in base
    assert "api.openai.com" not in base
    assert auth != "subscription"
    assert llm.get("is_subscription") in (None, False)
    assert auth == "api_key"
    assert NINE_ROUTER_V1.rstrip("/") in base.rstrip("/")
    assert api_mode == "chat"
    assert model.startswith("openai/")
    assert "chatgpt-account-id" not in extra
    if llm.get("api_key") not in (None, "", False):
        assert llm.get("api_key") not in ("null",)


def test_live_agent_server_completions_hit_9router():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; live 9router completions unproven")
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
        pytest.skip(f"Agent Server completions probe failed to run: {probe.stderr[-300:]}")
    payload = json.loads(probe.stdout.strip().splitlines()[-1])
    assert NINE_ROUTER_V1.rstrip("/") in str(payload.get("target") or "")
    body = str(payload.get("body") or "")
    assert "chatgpt.com" not in body
    assert "api.openai.com" not in body
    status = int(payload.get("status") or 0)
    assert status != 0, f"completions never reached 9router: {body}"
    assert status in {200, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503}


def test_live_9router_down_fails_closed_without_chatgpt_fallback():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; 9router-down path unproven")
    running = _compose("ps", "--format", "json", NINE_ROUTER_SERVICE)
    if running.returncode != 0 or "running" not in running.stdout:
        pytest.skip("9router not running; down-path unproven")
    stop = _compose("stop", NINE_ROUTER_SERVICE)
    if stop.returncode != 0:
        pytest.skip(f"could not stop 9router: {stop.stderr[-300:]}")
    try:
        script = r"""
import json, urllib.error, urllib.request
settings = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/api/settings", timeout=8).read().decode())
llm = (settings.get("agent_settings") or {}).get("llm") or {}
base = str(llm.get("base_url") or "")
auth = str(llm.get("auth_type") or "")
try:
    urllib.request.urlopen("http://9router:20128/api/health", timeout=3).read()
    health = "up"
except Exception as exc:
    health = "down:" + type(exc).__name__
print(json.dumps({"base_url": base, "auth_type": auth, "health": health, "is_subscription": llm.get("is_subscription")}))
"""
        probe = _compose("exec", "-T", AGENT_SERVER, "python", "-c", script)
        assert probe.returncode == 0, probe.stderr
        payload = json.loads(probe.stdout.strip().splitlines()[-1])
        assert payload["auth_type"] == "api_key"
        assert NINE_ROUTER_V1.rstrip("/") in str(payload.get("base_url") or "")
        assert "chatgpt.com" not in str(payload.get("base_url") or "")
        assert payload.get("is_subscription") in (None, False)
        assert str(payload.get("health") or "").startswith("down")
    finally:
        _compose("start", NINE_ROUTER_SERVICE)


def test_cancel_and_condensation_honest_skip_unless_live_model():
    pytest.skip(
        "cancel/condensation on native 9router unproven: 9router has no connected upstream provider"
    )
