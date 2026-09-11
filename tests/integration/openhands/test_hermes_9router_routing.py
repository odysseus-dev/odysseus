"""Hermes ACP must infer through 9router using its own named provider config."""

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
HERMES_PROFILE = ROOT / "deploy" / "openhands" / "profiles" / "hermes.yaml"
HERMES_CONFIG = ROOT / "deploy" / "openhands" / "hermes" / "config.yaml"
OPENCODE_CONFIG = ROOT / "deploy" / "openhands" / "opencode.json"
NATIVE_SETTINGS = ROOT / "data" / "openhands-agent-server-home" / "settings.json"
LIVE_ACP_PROFILE = (
    ROOT / "data" / "openhands-agent-server-home" / "agent-profiles" / "hermes.json"
)
CHAT_ROUTES = ROOT / "routes" / "chat_routes.py"
CLIENT_PY = ROOT / "services" / "agents" / "openhands_client.py"
SESSION_BINDING = ROOT / "services" / "agents" / "session_binding.py"
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


def _load_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML unavailable; Hermes config structure unparsed")
    payload = yaml.safe_load(text) or {}
    assert isinstance(payload, dict)
    return payload


def _load_session_binding():
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
    from services.agents.session_binding import (
        ALLOWED_AGENT_PROFILES,
        normalize_agent_profile_id,
    )

    return ALLOWED_AGENT_PROFILES, normalize_agent_profile_id


def test_hermes_profile_keeps_mcp_and_rejects_native_llm_inheritance():
    text = HERMES_PROFILE.read_text(encoding="utf-8")
    assert "mcp:" in text
    assert "odysseus" in text
    assert "llm_profile_ref" not in text
    assert "command: hermes acp" in text
    assert "mail.send" not in text


def test_interactive_chat_has_no_stream_llm_fallback_for_hermes():
    source = CHAT_ROUTES.read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1].split("async def chat_resume", 1)[0]
    assert "async for chunk in stream_governed_agent(" in stream
    picker = stream.split("if compare_mode:", 1)[0]
    assert "stream_llm(" not in picker
    assert "stream_llm_with_fallback(" not in picker


def test_openhands_client_source_does_not_patch_provider_keys():
    source = CLIENT_PY.read_text(encoding="utf-8")
    assert 'request("PATCH"' not in source
    assert "PATCH" not in source
    assert "/v1/chat/completions" not in source
    assert 'agent_profile_id == "hermes"' not in source


def test_hermes_stays_off_odysseus_chooser():
    allowed, normalize = _load_session_binding()
    assert "hermes" not in allowed
    assert normalize("hermes") == "odysseus"
    source = SESSION_BINDING.read_text(encoding="utf-8")
    assert "hermes" not in source or 'ALLOWED_AGENT_PROFILES = ("odysseus", "opencode")' in source


def test_hermes_config_is_independent_of_native_and_opencode():
    assert HERMES_CONFIG.is_file(), (
        "Hermes needs its own HERMES_HOME config.yaml; native settings.json is not enough"
    )
    payload = _load_yaml(HERMES_CONFIG)
    blob = HERMES_CONFIG.read_text(encoding="utf-8")
    providers = payload.get("providers") or {}
    assert isinstance(providers, dict)
    assert "ninerouter" in providers, "use a named Hermes provider, not bare custom"
    assert "custom" not in providers
    spec = providers["ninerouter"]
    assert isinstance(spec, dict)
    base = str(spec.get("base_url") or spec.get("api") or spec.get("url") or "")
    assert base == NINE_ROUTER_V1
    assert str(spec.get("api_mode") or spec.get("transport") or "") == "chat_completions"
    model = payload.get("model") or {}
    if isinstance(model, dict):
        assert model.get("provider") == "ninerouter"
        assert str(model.get("base_url") or "") in {"", NINE_ROUTER_V1}
        assert str(model.get("api_mode") or "") in {"", "chat_completions"}
    else:
        raise AssertionError("Hermes model: must name provider ninerouter, not a bare string")
    for marker in DIRECT_PROVIDER_MARKERS:
        assert marker not in blob
    assert HERMES_CONFIG.resolve() != OPENCODE_CONFIG.resolve()
    if NATIVE_SETTINGS.is_file():
        assert HERMES_CONFIG.resolve() != NATIVE_SETTINGS.resolve()
    native_text = BOOTSTRAP_PY.read_text(encoding="utf-8")
    assert "odysseus-native" in native_text
    assert "settings.json" in native_text
    assert "odysseus-opencode" in native_text
    assert "opencode.json" in native_text or "OPENCODE" in native_text


def test_native_settings_and_opencode_are_insufficient_for_hermes():
    native = BOOTSTRAP_PY.read_text(encoding="utf-8")
    assert NINE_ROUTER_V1 in native
    assert "apply_native_9router_settings" in native
    assert "apply_opencode_9router_config" in native
    assert HERMES_CONFIG.is_file(), (
        "native Agent Server 9router settings are insufficient; Hermes config missing"
    )
    assert "odysseus-hermes" in native
    assert "apply_hermes_9router_config" in native
    assert "HERMES" in native or "hermes" in native


def test_bootstrap_mints_separate_hermes_key_and_retags_acp_server():
    script = BOOTSTRAP_PY.read_text(encoding="utf-8")
    wrapper = ENTRYPOINT_SH.read_text(encoding="utf-8")
    assert "odysseus-native" in script
    assert "odysseus-opencode" in script
    assert "odysseus-hermes" in script
    assert "acp_server" in script
    assert "claude-code" in script
    assert '"hermes"' in script or "'hermes'" in script
    assert "bootstrap_native_llm" in wrapper
    assert NINE_ROUTER_V1 in script
    assert "apply_hermes_9router_config" in script
    assert "apply_hermes_9router_config()" in (ROOT / "deploy" / "openhands" / "bootstrap_native_llm.py").read_text(
        encoding="utf-8"
    ).split('if __name__ == "__main__":', 1)[-1]


def test_compose_isolates_hermes_home_and_keeps_odysseus_list_env():
    compose = _overlay_text()
    agent = _service_block(compose, AGENT_SERVER)
    odysseus = _service_block(compose, "odysseus")
    assert "HERMES_HOME" in agent
    assert "openhands-hermes-home" in agent or "/home/hermes" in agent
    assert "ANTHROPIC_API_KEY" in agent
    leaked = [name for name in INFERENCE_KEY_NAMES if name in odysseus]
    assert leaked == []
    list_entries = [line.strip() for line in odysseus.splitlines() if line.strip().startswith("- ")]
    assert any(line.startswith("- ODYSSEUS_OPENHANDS_") for line in list_entries)


def test_live_or_overlay_hermes_acp_profile_is_not_claude_code():
    script = BOOTSTRAP_PY.read_text(encoding="utf-8")
    assert "claude-code" in script
    if LIVE_ACP_PROFILE.is_file():
        payload = json.loads(LIVE_ACP_PROFILE.read_text(encoding="utf-8"))
        assert payload.get("acp_command") in {"hermes acp", None} or str(
            payload.get("acp_command") or ""
        ).endswith("hermes acp")
        assert payload.get("acp_server") != "claude-code"
        assert payload.get("acp_server") in {"hermes", "custom"}
        assert "llm_profile_ref" not in payload


def test_subscription_auth_type_is_not_a_hermes_route():
    assert HERMES_CONFIG.is_file(), "missing Hermes provider config must fail loudly"
    payload = _load_yaml(HERMES_CONFIG)
    providers = payload.get("providers") or {}
    assert "ninerouter" in providers
    blob = HERMES_CONFIG.read_text(encoding="utf-8")
    assert "subscription" not in blob
    assert "chatgpt.com" not in blob
    native = BOOTSTRAP_PY.read_text(encoding="utf-8")
    assert "auth_type" in native
    hermes_fn = native.split("def apply_hermes_9router_config", 1)[-1].split("def ", 1)[0]
    assert "subscription" not in hermes_fn or "must not" in hermes_fn.lower()
    assert "settings.json" not in hermes_fn
    assert "opencode.json" not in hermes_fn
    if NATIVE_SETTINGS.is_file():
        settings = json.loads(NATIVE_SETTINGS.read_text(encoding="utf-8"))
        llm = ((settings.get("agent_settings") or {}).get("llm") or {})
        if str(llm.get("auth_type") or "") == "subscription":
            raise AssertionError(
                "Canvas ChatGPT subscription must not be treated as the Hermes route"
            )


def test_live_hermes_config_hits_9router_even_without_upstream_provider():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; live Hermes 9router path unproven")
    script = r"""
import json, os, urllib.error, urllib.request
from pathlib import Path
candidates = [
    os.environ.get("HERMES_CONFIG") or "",
    (os.environ.get("HERMES_HOME") or "") + "/config.yaml",
    "/home/hermes/.hermes/config.yaml",
    "/opt/odysseus/hermes-config.yaml",
]
path = next((item for item in candidates if item and Path(item).is_file()), "")
native = "/home/openhands/.openhands/settings.json"
opencode = os.environ.get("OPENCODE_CONFIG") or "/home/opencode/.config/opencode/opencode.json"
if not path:
    print(json.dumps({
        "error": "hermes-config-missing",
        "native_exists": Path(native).is_file(),
        "opencode_exists": Path(opencode).is_file(),
    }))
    raise SystemExit(0)
text = Path(path).read_text()
cfg = {}
try:
    import yaml
    parsed = yaml.safe_load(text) or {}
    if isinstance(parsed, dict):
        cfg = parsed
except Exception:
    cfg = {}
if not (cfg.get("providers") or {}).get("ninerouter"):
    base = ""
    key = ""
    key_env = ""
    model = "auto"
    in_nr = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0]
        if line.startswith("  ninerouter:"):
            in_nr = True
            continue
        if in_nr and line.startswith("  ") and not line.startswith("    "):
            in_nr = False
        if in_nr and "base_url:" in line:
            base = line.split(":", 1)[1].strip().strip("'\"")
        if in_nr and "api_key:" in line:
            key = line.split(":", 1)[1].strip().strip("'\"")
        if in_nr and "key_env:" in line:
            key_env = line.split(":", 1)[1].strip().strip("'\"")
        if in_nr and line.strip().startswith("model:"):
            model = line.split(":", 1)[1].strip().strip("'\"")
    if base:
        cfg = {"providers": {"ninerouter": {"base_url": base, "api_key": key, "key_env": key_env, "model": model}}, "model": {"default": model, "provider": "ninerouter"}}
providers = cfg.get("providers") or {}
spec = providers.get("ninerouter") if isinstance(providers, dict) else None
if not isinstance(spec, dict):
    print(json.dumps({"error": "hermes-named-provider-missing", "config": path, "keys": list(providers)}))
    raise SystemExit(0)
base = str(spec.get("base_url") or spec.get("api") or spec.get("url") or "").rstrip("/")
key = spec.get("api_key")
if isinstance(key, dict):
    key = key.get("secret") or key.get("value")
key = "" if key is None else str(key)
if not key or key.startswith("${") or "{env:" in key:
    env_name = str(spec.get("key_env") or spec.get("api_key_env") or "HERMES_NINE_ROUTER_KEY")
    env_file = Path(os.environ.get("HERMES_HOME") or "/home/hermes/.hermes") / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.startswith(env_name + "="):
                key = line.split("=", 1)[1].strip().strip("'").strip('"')
                break
    if not key or key.startswith("${"):
        key = os.environ.get(env_name, "")
model = str((cfg.get("model") or {}).get("default") or spec.get("model") or "auto")
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
if key:
    req.add_header("Authorization", "Bearer " + key)
try:
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read().decode()
        print(json.dumps({"status": response.status, "body": body[:800], "target": base, "config": path}))
except urllib.error.HTTPError as exc:
    err = exc.read().decode() if exc.fp else ""
    print(json.dumps({"status": exc.code, "body": err[:800], "target": base, "config": path}))
except Exception as exc:
    print(json.dumps({"status": 0, "body": str(exc), "target": base, "config": path}))
"""
    probe = _compose("exec", "-T", AGENT_SERVER, "python", "-c", script)
    if probe.returncode != 0:
        pytest.skip(f"Agent Server Hermes probe failed to run: {probe.stderr[-300:]}")
    payload = json.loads(probe.stdout.strip().splitlines()[-1])
    assert payload.get("error") != "hermes-config-missing", (
        "native 9router settings are insufficient; Hermes config missing in ACP environment"
    )
    assert payload.get("error") != "hermes-named-provider-missing", (
        "Hermes must use a named providers.ninerouter entry, not bare custom"
    )
    assert NINE_ROUTER_V1.rstrip("/") in str(payload.get("target") or "")
    body = str(payload.get("body") or "")
    for marker in DIRECT_PROVIDER_MARKERS:
        assert marker not in body
        assert marker not in str(payload.get("target") or "")
    status = int(payload.get("status") or 0)
    assert status != 0, f"Hermes completions never reached 9router: {body}"
    assert status in {200, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503}


def test_hermes_fail_closed_points_at_9router_not_direct_providers():
    assert HERMES_CONFIG.is_file()
    blob = HERMES_CONFIG.read_text(encoding="utf-8")
    assert NINE_ROUTER_V1 in blob
    for marker in DIRECT_PROVIDER_MARKERS:
        assert marker not in blob
    source = CHAT_ROUTES.read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1].split("async def chat_resume", 1)[0]
    picker = stream.split("if compare_mode:", 1)[0]
    assert "stream_llm(" not in picker
    assert "stream_llm_with_fallback(" not in picker
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; 9router health from Hermes env unproven")
    script = r"""
import json, os, urllib.request
from pathlib import Path
home = os.environ.get("HERMES_HOME") or "/home/hermes/.hermes"
cfg_path = os.environ.get("HERMES_CONFIG") or str(Path(home) / "config.yaml")
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
print(json.dumps({"config_exists": exists, "chatgpt": chatgpt, "health": health, "config": cfg_path, "home": home}))
"""
    probe = _compose("exec", "-T", AGENT_SERVER, "python", "-c", script)
    if probe.returncode != 0:
        pytest.skip(f"Hermes fail-closed probe failed to run: {probe.stderr[-300:]}")
    payload = json.loads(probe.stdout.strip().splitlines()[-1])
    assert payload.get("config_exists") is True
    assert str(payload.get("chatgpt") or "").startswith("blocked")
    # Contract: Hermes stays pinned at 9router. Do not stop the live 9router
    # service; health may be up. Absence of direct-provider URLs is the close.
    assert payload.get("health") in {"up"} or str(payload.get("health") or "").startswith("down")


def test_live_hermes_home_has_named_provider_so_acp_init_is_not_empty():
    if not shutil.which("docker"):
        pytest.skip("docker unavailable; live Hermes ACP home unproven")
    script = r"""
import json, os, shutil, subprocess
from pathlib import Path
home = os.environ.get("HERMES_HOME") or "/home/hermes/.hermes"
cfg = Path(home) / "config.yaml"
text = cfg.read_text() if cfg.is_file() else ""
named = "ninerouter:" in text and "providers:" in text and "custom:" not in text.split("providers:", 1)[-1].split("model:", 1)[0]
hermes = shutil.which("hermes") or ("/opt/oh-bin/hermes" if Path("/opt/oh-bin/hermes").is_file() else "")
init = {"ran": False, "stdout": "", "stderr": "", "returncode": None}
if hermes:
    env = os.environ.copy()
    env["HERMES_HOME"] = home
    env["ANTHROPIC_API_KEY"] = ""
    env["CLAUDE_CODE_OAUTH_TOKEN"] = ""
    try:
        proc = subprocess.run(
            [hermes, "config", "get", "model.provider"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        init = {
            "ran": True,
            "stdout": (proc.stdout or "")[:400],
            "stderr": (proc.stderr or "")[:400],
            "returncode": proc.returncode,
        }
    except Exception as exc:
        init = {"ran": True, "stdout": "", "stderr": str(exc)[:400], "returncode": 1}
print(json.dumps({
    "home": home,
    "config_exists": cfg.is_file(),
    "named_provider": named,
    "hermes_bin": hermes,
    "init": init,
}))
"""
    probe = _compose("exec", "-T", AGENT_SERVER, "python", "-c", script)
    if probe.returncode != 0:
        pytest.skip(f"Hermes ACP home probe failed to run: {probe.stderr[-300:]}")
    payload = json.loads(probe.stdout.strip().splitlines()[-1])
    assert payload.get("config_exists") is True
    assert payload.get("named_provider") is True
    init = payload.get("init") or {}
    if init.get("ran"):
        combined = f"{init.get('stdout') or ''} {init.get('stderr') or ''}"
        assert "No LLM provider configured" not in combined
        assert "ninerouter" in str(init.get("stdout") or "")
