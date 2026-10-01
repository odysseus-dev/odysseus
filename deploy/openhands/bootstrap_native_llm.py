"""Overwrite Agent Server LLM settings so native turns use 9router, not ChatGPT.

Persisted ``auth_type: subscription`` at chatgpt.com/backend-api/codex must not
remain the silent default. Compose OPENAI_API_KEY cannot switch that route.

TEMPORARY BRIDGE: ``_mint_virtual_key`` writes 9router sqlite ``apiKeys``.
Preferred path is Agent Server → supported 9router control/API → scoped
virtual key. Deletion condition: mint exclusively via POST /api/keys (or
another official control API) and drop the DATA_DIR mount.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


log = logging.getLogger("bootstrap_native_llm")

NINE_ROUTER_V1 = "http://9router:20128/v1"
NINE_ROUTER_HEALTH = "http://9router:20128/api/health"
# Spike 2026-09-11: 9router /v1/models ids are "{provider}/{model}" (kr/, alicode-intl/,
# openai/, ...). Bare aliases like auto/fast are absent and resolve as provider
# "openai". OpenHands LiteLLM custom base needs the openai/ prefix so the
# catalog id is what 9router receives after the first slash is stripped.
NATIVE_LITELLM_PREFIX = "openai/"
# LiteLLM needs openai/; 9router then receives catalog id after the first slash.
# Bare auto/openai/auto 404s (provider openai, no API key). gpt-6-astra 400s on Codex.
NATIVE_LITELLM_MODEL = "openai/cx/gpt-5.5"
_SKIP_CATALOG = ("gpt-6-astra", "-review")
_PREFER_CATALOG = ("cx/gpt-5.5", "cx/gpt-5.4", "cx/gpt-5.4-mini")
KEY_NAME = "odysseus-native"
MACHINE_ID = "odysseusnative1"
OPENCODE_KEY_NAME = "odysseus-opencode"
OPENCODE_MACHINE_ID = "odysseusopencode1"
API_KEY_SECRET = os.environ.get("API_KEY_SECRET", "endpoint-proxy-api-key-secret")
SETTINGS_PATH = Path(
    os.environ.get(
        "OPENHANDS_SETTINGS_PATH",
        "/home/openhands/.openhands/settings.json",
    )
)
OPENCODE_CONFIG_PATH = Path(
    os.environ.get(
        "OPENCODE_CONFIG_PATH",
        "/home/opencode/.config/opencode/opencode.json",
    )
)
OPENCODE_TEMPLATE_PATH = Path(
    os.environ.get(
        "OPENCODE_TEMPLATE_PATH",
        "/opt/odysseus/opencode.json",
    )
)
OPENCODE_ACP_PROFILE_PATH = Path(
    os.environ.get(
        "OPENCODE_ACP_PROFILE_PATH",
        "/home/openhands/.openhands/agent-profiles/opencode.json",
    )
)
HERMES_KEY_NAME = "odysseus-hermes"
HERMES_MACHINE_ID = "odysseushermes1"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/home/hermes/.hermes"))
HERMES_CONFIG_PATH = Path(
    os.environ.get("HERMES_CONFIG_PATH", str(HERMES_HOME / "config.yaml"))
)
HERMES_TEMPLATE_PATH = Path(
    os.environ.get(
        "HERMES_TEMPLATE_PATH",
        "/opt/odysseus/hermes-config.yaml",
    )
)
HERMES_ACP_PROFILE_PATH = Path(
    os.environ.get(
        "HERMES_ACP_PROFILE_PATH",
        "/home/openhands/.openhands/agent-profiles/hermes.json",
    )
)
HERMES_KEY_ENV = "HERMES_NINE_ROUTER_KEY"
NINE_ROUTER_DB = Path(
    os.environ.get(
        "NINE_ROUTER_SQLITE_PATH",
        "/opt/odysseus/9router-data/db/data.sqlite",
    )
)


def _run_sudo(args: list[str]) -> None:
    """Run a privileged helper when the Agent Server user cannot write the volume.

    Parameters
    ----------
    args
        Command arguments after ``sudo -n``.
    """

    subprocess.run(["sudo", "-n", *args], check=True)


def _chown_tree(path: Path, uid: int = 10001, gid: int = 10001) -> None:
    """Give the Agent Server user the bind-mounted home tree.

    Host overlay data is often uid 1000 mode 0700. Agent Server runs as
    10001. Without a recursive chown, GET /api/settings PermissionErrors and
    chat Native shows Agent run failed before completion.
    """

    path.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        for root, dirs, files in os.walk(path):
            os.chown(root, uid, gid)
            os.chmod(root, 0o700)
            for name in dirs + files:
                target = Path(root) / name
                os.chown(target, uid, gid)
        return
    _run_sudo(["chown", "-R", f"{uid}:{gid}", str(path)])
    _run_sudo(["chmod", "700", str(path)])


def _wait_http(url: str, timeout: float = 60.0) -> None:
    """Block until an HTTP GET succeeds or the timeout expires.

    Parameters
    ----------
    url
        Absolute URL to poll.
    timeout
        Seconds to wait before failing closed.
    """

    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=3).read()
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(1)
    raise RuntimeError(f"timeout waiting for {url}: {last}")


def _mint_virtual_key(name: str = KEY_NAME, machine_id: str = MACHINE_ID) -> str:
    """Return an existing named 9router key or insert a new one.

    Parameters
    ----------
    name
        9router ``apiKeys.name``. Native and OpenCode use different names.
    machine_id
        Machine suffix baked into the virtual key.

    Returns
    -------
    str
        Virtual inference key stored only in 9router DATA_DIR and runtime config.
    """

    deadline = time.time() + 60
    while time.time() < deadline and not NINE_ROUTER_DB.is_file():
        time.sleep(1)
    if not NINE_ROUTER_DB.is_file():
        raise RuntimeError(f"9router sqlite missing: {NINE_ROUTER_DB}")
    conn = sqlite3.connect(str(NINE_ROUTER_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        return _ensure_key_row(conn, name=name, machine_id=machine_id)
    finally:
        conn.close()


def _ensure_key_row(
    conn: sqlite3.Connection,
    name: str = KEY_NAME,
    machine_id: str = MACHINE_ID,
) -> str:
    """Reuse or insert the named virtual key.

    Parameters
    ----------
    conn
        Open 9router sqlite connection.
    name
        9router ``apiKeys.name``.
    machine_id
        Machine suffix baked into the virtual key.

    Returns
    -------
    str
        Active virtual key for ``name``.
    """

    row = conn.execute(
        "SELECT key FROM apiKeys WHERE name = ? AND isActive = 1 LIMIT 1",
        (name,),
    ).fetchone()
    if row and row["key"]:
        return str(row["key"])
    key_id = uuid.uuid4().hex[:6]
    crc = hmac.new(
        API_KEY_SECRET.encode(),
        (machine_id + key_id).encode(),
        hashlib.sha256,
    ).hexdigest()[:8]
    key = f"sk-{machine_id}-{key_id}-{crc}"
    conn.execute(
        "INSERT INTO apiKeys (id, key, name, machineId, isActive, createdAt) VALUES (?, ?, ?, ?, 1, ?)",
        (
            key_id,
            key,
            name,
            machine_id,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return key


def _pick_litellm_model(virtual_key: str) -> str:
    """Return ``openai/{catalog_id}`` for a ChatGPT/Codex catalog model that works.

    Overlay 9router catalog ids look like ``cx/gpt-5.5``. LiteLLM must see
    ``openai/cx/gpt-5.5`` so it uses the OpenAI-compatible client; 9router then
    gets ``cx/gpt-5.5``. ``openai/auto`` 404s (no OpenAI API key). ``cx/gpt-6-astra``
    400s on ChatGPT Codex.
    """

    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        NINE_ROUTER_V1 + "/models",
        headers={"Authorization": f"Bearer {virtual_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return NATIVE_LITELLM_MODEL
    ids = [str(item.get("id") or "") for item in (payload.get("data") or [])]
    ids = [item for item in ids if item]
    return _litellm_id_from_catalog(ids)


def _litellm_id_from_catalog(ids: list[str]) -> str:
    """Pick a working overlay catalog id and wrap it for LiteLLM."""

    if not ids:
        return NATIVE_LITELLM_MODEL
    chosen = next((item for item in _PREFER_CATALOG if item in ids), "")
    if not chosen:
        chosen = next(
            (
                item
                for item in ids
                if not any(skip in item for skip in _SKIP_CATALOG)
            ),
            ids[0],
        )
    if chosen.startswith(NATIVE_LITELLM_PREFIX):
        return chosen
    return NATIVE_LITELLM_PREFIX + chosen


def _rewrite_llm(settings: dict[str, Any], virtual_key: str) -> dict[str, Any]:
    """Replace ChatGPT subscription fields with a 9router API-key profile.

    Parameters
    ----------
    settings
        Existing Agent Server settings document.
    virtual_key
        9router virtual key. Never logged.

    Returns
    -------
    dict
        Mutated settings pointing at ``http://9router:20128/v1``.
    """

    agent = settings.setdefault("agent_settings", {})
    llm = dict(agent.get("llm") or {})
    llm["model"] = _pick_litellm_model(virtual_key)
    llm["api_key"] = virtual_key
    llm["auth_type"] = "api_key"
    llm["subscription_vendor"] = None
    llm["base_url"] = NINE_ROUTER_V1
    llm["api_mode"] = "chat"
    llm["is_subscription"] = False
    llm["provider_connection_id"] = None
    extra = llm.get("extra_headers")
    if isinstance(extra, dict):
        llm["extra_headers"] = {
            name: value
            for name, value in extra.items()
            if "chatgpt" not in name.lower() and name != "originator"
        }
    else:
        llm["extra_headers"] = {}
    agent["llm"] = llm
    return settings


def _reexec_as_root() -> None:
    """Re-run this script under passwordless sudo so volume files are writable."""

    if os.geteuid() == 0:
        return
    os.execvp("sudo", ["sudo", "-n", sys.executable, *sys.argv])


def apply_native_9router_settings() -> None:
    """Mint a 9router virtual key and overwrite persisted Agent Server LLM settings."""

    _reexec_as_root()
    _chown_tree(SETTINGS_PATH.parent)
    _wait_http(NINE_ROUTER_HEALTH)
    virtual_key = _mint_virtual_key()
    if SETTINGS_PATH.is_file():
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    else:
        settings = {
            "schema_version": 2,
            "agent_settings": {"schema_version": 5, "agent_kind": "openhands", "agent": "CodeActAgent"},
        }
    rewritten = _rewrite_llm(settings, virtual_key)
    tmp = Path("/tmp/odysseus-native-settings.json")
    tmp.write_text(json.dumps(rewritten, indent=2) + "\n", encoding="utf-8")
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.replace(SETTINGS_PATH)
    except OSError:
        _run_sudo(["cp", str(tmp), str(SETTINGS_PATH)])
    if os.geteuid() == 0:
        os.chown(SETTINGS_PATH, 10001, 10001)
        os.chmod(SETTINGS_PATH, 0o600)
    else:
        _run_sudo(["chown", "10001:10001", str(SETTINGS_PATH)])
        _run_sudo(["chmod", "600", str(SETTINGS_PATH)])
    # GET /api/settings redacts llm.api_key to **********. Odysseus POSTs
    # conversations with this sidecar so 9router is not called without a key.
    sidecar = SETTINGS_PATH.parent / "native-llm-api-key"
    sidecar.write_text(virtual_key, encoding="utf-8")
    if os.geteuid() == 0:
        os.chown(sidecar, 10001, 10001)
        os.chmod(sidecar, 0o644)
    else:
        _run_sudo(["chown", "10001:10001", str(sidecar)])
        _run_sudo(["chmod", "644", str(sidecar)])
    log.info(
        "native llm now %s model=%s auth_type=api_key",
        NINE_ROUTER_V1,
        rewritten.get("agent_settings", {}).get("llm", {}).get("model"),
    )


def _write_owned_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Write a text file onto a runtime volume as the Agent Server user.

    Parameters
    ----------
    path
        Destination path on a mounted volume.
    text
        Full file contents.
    mode
        File mode after replace.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path("/tmp") / f"odysseus-{path.name}"
    tmp.write_text(text, encoding="utf-8")
    try:
        tmp.replace(path)
    except OSError:
        _run_sudo(["cp", str(tmp), str(path)])
    if os.geteuid() == 0:
        os.chown(path, 10001, 10001)
        os.chmod(path, mode)
        os.chown(path.parent, 10001, 10001)
    else:
        _run_sudo(["chown", "10001:10001", str(path), str(path.parent)])
        _run_sudo(["chmod", oct(mode)[2:], str(path)])


def _write_owned_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    """Write JSON onto a runtime volume as the Agent Server user.

    Parameters
    ----------
    path
        Destination path on a mounted volume.
    payload
        JSON-serializable document.
    mode
        File mode after replace.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path("/tmp") / f"odysseus-{path.name}"
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        tmp.replace(path)
    except OSError:
        _run_sudo(["cp", str(tmp), str(path)])
    if os.geteuid() == 0:
        os.chown(path, 10001, 10001)
        os.chmod(path, mode)
        os.chown(path.parent, 10001, 10001)
    else:
        _run_sudo(["chown", "10001:10001", str(path), str(path.parent)])
        _run_sudo(["chmod", oct(mode)[2:], str(path)])


def _pick_opencode_model(virtual_key: str) -> str:
    """Return a 9router catalog id for OpenCode, without the LiteLLM prefix.

    Parameters
    ----------
    virtual_key
        9router virtual key used only for the catalog GET.

    Returns
    -------
    str
        Catalog model id such as ``auto`` or ``kr/claude-opus-5``.
    """

    litellm = _pick_litellm_model(virtual_key)
    if litellm.startswith(NATIVE_LITELLM_PREFIX):
        return litellm[len(NATIVE_LITELLM_PREFIX) :] or "auto"
    return litellm or "auto"


def _opencode_config_payload(virtual_key: str) -> dict[str, Any]:
    """Build OpenCode provider config pointing at 9router.

    Parameters
    ----------
    virtual_key
        OpenCode-only 9router virtual key. Never logged.

    Returns
    -------
    dict
        ``opencode.json`` document with ``options.baseURL`` on 9router.
    """

    if OPENCODE_TEMPLATE_PATH.is_file():
        payload = json.loads(OPENCODE_TEMPLATE_PATH.read_text(encoding="utf-8"))
    else:
        payload = {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "provider": {},
        }
    model_id = _pick_opencode_model(virtual_key)
    provider = payload.setdefault("provider", {})
    spec = dict(provider.get("ninerouter") or {})
    spec["npm"] = "@ai-sdk/openai-compatible"
    spec["name"] = "9router"
    options = dict(spec.get("options") or {})
    options["baseURL"] = NINE_ROUTER_V1
    options["apiKey"] = virtual_key
    spec["options"] = options
    spec["models"] = {model_id: {"name": model_id}}
    provider["ninerouter"] = spec
    payload["provider"] = provider
    payload["model"] = f"ninerouter/{model_id}"
    payload["enabled_providers"] = ["ninerouter"]
    payload["disabled_providers"] = [
        "openai",
        "anthropic",
        "opencode",
        "amazon-bedrock",
        "google",
        "gemini",
    ]
    payload["autoupdate"] = False
    return payload


def _retag_opencode_acp_profile() -> None:
    """Force live OpenCode ACP off the Anthropic ``claude-code`` channel."""

    if OPENCODE_ACP_PROFILE_PATH.is_file():
        payload = json.loads(OPENCODE_ACP_PROFILE_PATH.read_text(encoding="utf-8"))
    else:
        payload = {
            "schema_version": 2,
            "name": "opencode",
            "revision": 0,
            "agent_kind": "acp",
            "acp_command": "opencode acp",
        }
    if payload.get("acp_server") == "claude-code":
        log.info("retag acp_server claude-code -> opencode")
    payload["acp_server"] = "opencode"
    payload["acp_command"] = payload.get("acp_command") or "opencode acp"
    payload.pop("llm_profile_ref", None)
    _write_owned_json(OPENCODE_ACP_PROFILE_PATH, payload, mode=0o644)


def apply_opencode_9router_config() -> None:
    """Mint a separate OpenCode 9router key and write isolated OpenCode config."""

    _reexec_as_root()
    _chown_tree(Path("/home/opencode"))
    _wait_http(NINE_ROUTER_HEALTH)
    virtual_key = _mint_virtual_key(OPENCODE_KEY_NAME, OPENCODE_MACHINE_ID)
    _write_owned_json(OPENCODE_CONFIG_PATH, _opencode_config_payload(virtual_key))
    _retag_opencode_acp_profile()
    log.info("opencode provider now %s key=%s acp_server=opencode", NINE_ROUTER_V1, OPENCODE_KEY_NAME)


def _hermes_config_yaml(virtual_key: str) -> str:
    """Build Hermes v2026.9.7 ``providers:`` YAML pointing at 9router.

    Parameters
    ----------
    virtual_key
        Hermes-only 9router virtual key. Never logged.

    Returns
    -------
    str
        ``config.yaml`` text with named ``ninerouter`` provider.
    """

    model_id = _pick_opencode_model(virtual_key)
    if HERMES_TEMPLATE_PATH.is_file():
        raw = HERMES_TEMPLATE_PATH.read_text(encoding="utf-8")
        if "ninerouter:" not in raw or "providers:" not in raw:
            raise RuntimeError("Hermes template missing named providers.ninerouter")
    return (
        "_config_version: 12\n"
        "model:\n"
        f"  default: {json.dumps(model_id)}\n"
        "  provider: ninerouter\n"
        f"  base_url: {NINE_ROUTER_V1}\n"
        "  api_mode: chat_completions\n"
        "providers:\n"
        "  ninerouter:\n"
        f"    base_url: {NINE_ROUTER_V1}\n"
        f"    api_key: {json.dumps(virtual_key)}\n"
        f"    key_env: {HERMES_KEY_ENV}\n"
        "    api_mode: chat_completions\n"
        f"    model: {json.dumps(model_id)}\n"
    )


def _retag_hermes_acp_profile() -> None:
    """Force live Hermes ACP off the Anthropic ``claude-code`` channel."""

    if HERMES_ACP_PROFILE_PATH.is_file():
        payload = json.loads(HERMES_ACP_PROFILE_PATH.read_text(encoding="utf-8"))
    else:
        payload = {
            "schema_version": 2,
            "name": "hermes",
            "revision": 0,
            "agent_kind": "acp",
            "acp_command": "hermes acp",
        }
    if payload.get("acp_server") == "claude-code":
        log.info("retag acp_server claude-code -> hermes")
    payload["acp_server"] = "hermes"
    payload["acp_command"] = payload.get("acp_command") or "hermes acp"
    payload.pop("llm_profile_ref", None)
    _write_owned_json(HERMES_ACP_PROFILE_PATH, payload, mode=0o644)


def apply_hermes_9router_config() -> None:
    """Mint a separate Hermes 9router key and write isolated HERMES_HOME config."""

    _reexec_as_root()
    home = Path("/home/hermes")
    _chown_tree(home)
    _chown_tree(HERMES_HOME)
    _wait_http(NINE_ROUTER_HEALTH)
    virtual_key = _mint_virtual_key(HERMES_KEY_NAME, HERMES_MACHINE_ID)
    _write_owned_text(HERMES_CONFIG_PATH, _hermes_config_yaml(virtual_key))
    _write_owned_text(HERMES_HOME / ".env", f"{HERMES_KEY_ENV}={virtual_key}\n")
    _retag_hermes_acp_profile()
    log.info("hermes provider now %s key=%s acp_server=hermes", NINE_ROUTER_V1, HERMES_KEY_NAME)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    apply_native_9router_settings()
    apply_opencode_9router_config()
    apply_hermes_9router_config()
