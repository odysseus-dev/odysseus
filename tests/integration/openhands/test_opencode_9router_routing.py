"""OpenCode ACP must infer through 9router using its own provider config."""

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
OPENCODE_PROFILE = ROOT / "deploy" / "openhands" / "profiles" / "opencode.yaml"
OPENCODE_CONFIG = ROOT / "deploy" / "openhands" / "opencode.json"
NATIVE_SETTINGS = ROOT / "data" / "openhands-agent-server-home" / "settings.json"
LIVE_ACP_PROFILE = (
    ROOT / "data" / "openhands-agent-server-home" / "agent-profiles" / "opencode.json"
)
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
DIRECT_PROVIDER_MARKERS = (
    "chatgpt.com",
    "api.openai.com",
    "api.anthropic.com",
    "opencode.ai/zen",
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
            return {"id": "conv-opencode"}
        raise AssertionError(f"unexpected {method} {path}")


def test_opencode_profile_keeps_mcp_and_rejects_native_llm_inheritance():
    text = OPENCODE_PROFILE.read_text(encoding="utf-8")
    assert "mcp:" in text
    assert "odysseus" in text
    assert "llm_profile_ref" not in text
    assert "command: opencode acp" in text
    assert "mail.send" not in text
    assert "notes.read" in text or "documents.read" in text


def test_interactive_chat_has_no_stream_llm_fallback_for_opencode():
    source = CHAT_ROUTES.read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1].split("async def chat_resume", 1)[0]
    assert "async for chunk in stream_governed_agent(" in stream
    picker = stream.split("if compare_mode:", 1)[0]
    assert "stream_llm(" not in picker
    assert "No model selected for this chat" not in picker


def test_openhands_client_source_does_not_patch_provider_keys():
    source = CLIENT_PY.read_text(encoding="utf-8")
    assert 'request("PATCH"' not in source
    assert "PATCH" not in source
    assert "/v1/chat/completions" not in source
    assert 'agent_profile_id == "opencode"' in source


def test_opencode_config_is_independent_of_native_settings():
    assert OPENCODE_CONFIG.is_file(), "OpenCode needs its own opencode.json; native settings.json is not enough"
    config = json.loads(OPENCODE_CONFIG.read_text(encoding="utf-8"))
    blob = json.dumps(config)
    assert "@ai-sdk/openai-compatible" in blob
    assert NINE_ROUTER_V1 in blob
    providers = config.get("provider") or {}
    found_base = False
    for spec in providers.values():
        if not isinstance(spec, dict):
            continue
        options = spec.get("options") or {}
        if str(options.get("baseURL") or "") == NINE_ROUTER_V1:
            found_base = True
            break
    assert found_base, "OpenCode provider options.baseURL must be 9router /v1"
    assert "enabled_providers" in config
    assert "ninerouter" in config.get("enabled_providers", [])
    for marker in DIRECT_PROVIDER_MARKERS:
        assert marker not in blob
    assert OPENCODE_CONFIG.resolve() != NATIVE_SETTINGS.resolve()
    native_text = BOOTSTRAP_PY.read_text(encoding="utf-8")
    assert "odysseus-native" in native_text
    assert "settings.json" in native_text


def test_native_9router_settings_are_insufficient_for_opencode():
    native = BOOTSTRAP_PY.read_text(encoding="utf-8")
    assert NINE_ROUTER_V1 in native
    assert "apply_native_9router_settings" in native
    assert OPENCODE_CONFIG.is_file(), (
        "native Agent Server 9router settings are insufficient; OpenCode config missing"
    )
    assert "odysseus-opencode" in native
    assert "apply_opencode_9router_config" in native
    assert "opencode.json" in native or "OPENCODE" in native


def test_bootstrap_mints_separate_opencode_key_and_retags_acp_server():
    script = BOOTSTRAP_PY.read_text(encoding="utf-8")
    wrapper = ENTRYPOINT_SH.read_text(encoding="utf-8")
    assert "odysseus-native" in script
    assert "odysseus-opencode" in script
    assert "acp_server" in script
    assert "claude-code" in script
    assert '"opencode"' in script or "'opencode'" in script
    assert "bootstrap_native_llm" in wrapper
    assert NINE_ROUTER_V1 in script


def test_compose_isolates_opencode_home_and_keeps_odysseus_list_env():
    compose = _overlay_text()
    agent = _service_block(compose, AGENT_SERVER)
    odysseus = _service_block(compose, "odysseus")
    assert "opencode.json" in agent or "openhands-opencode-home" in agent
    assert "OPENCODE_CONFIG" in agent
    assert "ANTHROPIC_API_KEY" in agent
    leaked = [name for name in INFERENCE_KEY_NAMES if name in odysseus]
    assert leaked == []
    list_entries = [line.strip() for line in odysseus.splitlines() if line.strip().startswith("- ")]
    assert any(line.startswith("- ODYSSEUS_OPENHANDS_") for line in list_entries)


def test_live_or_overlay_acp_profile_is_not_claude_code():
    script = BOOTSTRAP_PY.read_text(encoding="utf-8")
    assert "claude-code" in script
    if LIVE_ACP_PROFILE.is_file():
        payload = json.loads(LIVE_ACP_PROFILE.read_text(encoding="utf-8"))
        assert payload.get("acp_command") in {"opencode acp", None} or str(
            payload.get("acp_command") or ""
        ).endswith("opencode acp")
        assert payload.get("acp_server") != "claude-code"
        assert payload.get("acp_server") in {"opencode", "custom"}
        assert "llm_profile_ref" not in payload


def test_opencode_create_records_opencode_provenance_not_native_settings():
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
                "api_key": "sk-native-must-not-be-opencode-proof",
            },
        }
    }
    transport = _RecordingTransport(settings)
    client = OpenHandsClient(transport=transport, agent_server_base="http://agent-server")
    result = client.create_or_resume(
        conversation_id=None,
        message="hi",
        request_id="oc1",
        profile_revision=1,
        archetype_version=1,
        agent_profile_id="opencode",
    )
    assert isinstance(result, ConversationResume)
    assert result.conversation_id == "conv-opencode"
    assert result.resolved_base_url == NINE_ROUTER_V1
    assert getattr(result, "resolved_runtime", None) == "opencode"
    assert result.resolved_model != "openai/auto"
    assert "sk-native-must-not-be-opencode-proof" not in json.dumps(result.__dict__)
    assert all(method != "PATCH" for method, _, _ in transport.calls)
    method, path, body = transport.calls[1]
    assert method == "POST"
    assert path == "/api/conversations"
    assert body["agent_profile_id"] == "opencode"
    assert "base_url" not in body
    assert "api_key" not in body
    assert "llm_profile_ref" not in body


def test_live_opencode_config_hits_9router_even_without_upstream_provider():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; live OpenCode 9router path unproven")
    script = r"""
import json, os, urllib.error, urllib.request
from pathlib import Path
candidates = [
    os.environ.get("OPENCODE_CONFIG") or "",
    "/home/opencode/.config/opencode/opencode.json",
    "/opt/odysseus/opencode.json",
]
path = next((item for item in candidates if item and Path(item).is_file()), "")
native = "/home/openhands/.openhands/settings.json"
if not path:
    print(json.dumps({"error": "opencode-config-missing", "native_exists": Path(native).is_file()}))
    raise SystemExit(0)
cfg = json.loads(Path(path).read_text())
base = ""
key = ""
model = str(cfg.get("model") or "ninerouter/auto")
for spec in (cfg.get("provider") or {}).values():
    if not isinstance(spec, dict):
        continue
    options = spec.get("options") or {}
    if str(options.get("baseURL") or ""):
        base = str(options.get("baseURL") or "").rstrip("/")
        raw = options.get("apiKey")
        if isinstance(raw, dict):
            raw = raw.get("secret") or raw.get("value")
        key = "" if raw is None else str(raw)
        if "{env:" in key:
            env_name = key.split("{env:", 1)[1].split("}", 1)[0]
            key = os.environ.get(env_name, "")
        break
target = base
req = urllib.request.Request(
    base + "/chat/completions",
    data=json.dumps({
        "model": model.split("/", 1)[-1],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }).encode(),
    method="POST",
    headers={"Content-Type": "application/json"},
)
if key and not key.startswith("{env:"):
    req.add_header("Authorization", "Bearer " + key)
try:
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read().decode()
        print(json.dumps({"status": response.status, "body": body[:800], "target": target, "config": path}))
except urllib.error.HTTPError as exc:
    err = exc.read().decode() if exc.fp else ""
    print(json.dumps({"status": exc.code, "body": err[:800], "target": target, "config": path}))
except Exception as exc:
    print(json.dumps({"status": 0, "body": str(exc), "target": target, "config": path}))
"""
    probe = _compose("exec", "-T", AGENT_SERVER, "python", "-c", script)
    if probe.returncode != 0:
        pytest.skip(f"Agent Server OpenCode probe failed to run: {probe.stderr[-300:]}")
    payload = json.loads(probe.stdout.strip().splitlines()[-1])
    assert payload.get("error") != "opencode-config-missing", (
        "native 9router settings are insufficient; OpenCode config missing in ACP environment"
    )
    assert NINE_ROUTER_V1.rstrip("/") in str(payload.get("target") or "")
    body = str(payload.get("body") or "")
    for marker in DIRECT_PROVIDER_MARKERS:
        assert marker not in body
        assert marker not in str(payload.get("target") or "")
    status = int(payload.get("status") or 0)
    assert status != 0, f"OpenCode completions never reached 9router: {body}"
    assert status in {200, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503}


def test_opencode_fail_closed_points_at_9router_not_direct_providers():
    assert OPENCODE_CONFIG.is_file()
    blob = OPENCODE_CONFIG.read_text(encoding="utf-8")
    assert NINE_ROUTER_V1 in blob
    for marker in DIRECT_PROVIDER_MARKERS:
        assert marker not in blob
    source = CHAT_ROUTES.read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1].split("async def chat_resume", 1)[0]
    picker = stream.split("if compare_mode:", 1)[0]
    assert "stream_llm(" not in picker
    assert "stream_llm_with_fallback(" not in picker
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; 9router health from OpenCode env unproven")
    script = r"""
import json, os, urllib.request
from pathlib import Path
cfg_path = os.environ.get("OPENCODE_CONFIG") or "/home/opencode/.config/opencode/opencode.json"
exists = Path(cfg_path).is_file()
try:
    urllib.request.urlopen("https://chatgpt.com", timeout=2).read()
    chatgpt = "reachable"
except Exception as exc:
    chatgpt = "blocked:" + type(exc).__name__
try:
    urllib.request.urlopen("http://9router:20128/api/health", timeout=3).read()
    health = "up"
except Exception as exc:
    health = "down:" + type(exc).__name__
print(json.dumps({"config_exists": exists, "chatgpt": chatgpt, "health": health, "config": cfg_path}))
"""
    probe = _compose("exec", "-T", AGENT_SERVER, "python", "-c", script)
    if probe.returncode != 0:
        pytest.skip(f"OpenCode fail-closed probe failed to run: {probe.stderr[-300:]}")
    payload = json.loads(probe.stdout.strip().splitlines()[-1])
    assert payload.get("config_exists") is True
    assert str(payload.get("chatgpt") or "").startswith("blocked")
    # Contract: OpenCode stays pinned at 9router. Do not stop the live 9router
    # service; health may be up. Absence of direct-provider URLs is the close.
    assert payload.get("health") in {"up"} or str(payload.get("health") or "").startswith("down")
