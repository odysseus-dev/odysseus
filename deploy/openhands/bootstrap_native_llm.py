"""Overwrite Agent Server LLM settings so native turns use 9router, not ChatGPT.

Persisted ``auth_type: subscription`` at chatgpt.com/backend-api/codex must not
remain the silent default. Compose OPENAI_API_KEY cannot switch that route.
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
NATIVE_LITELLM_MODEL = "openai/auto"
KEY_NAME = "odysseus-native"
MACHINE_ID = "odysseusnative1"
API_KEY_SECRET = os.environ.get("API_KEY_SECRET", "endpoint-proxy-api-key-secret")
SETTINGS_PATH = Path(
    os.environ.get(
        "OPENHANDS_SETTINGS_PATH",
        "/home/openhands/.openhands/settings.json",
    )
)
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


def _mint_virtual_key() -> str:
    """Return an existing odysseus-native 9router key or insert a new one.

    Returns
    -------
    str
        Virtual inference key stored only in 9router DATA_DIR and Agent Server settings.
    """

    deadline = time.time() + 60
    while time.time() < deadline and not NINE_ROUTER_DB.is_file():
        time.sleep(1)
    if not NINE_ROUTER_DB.is_file():
        raise RuntimeError(f"9router sqlite missing: {NINE_ROUTER_DB}")
    conn = sqlite3.connect(str(NINE_ROUTER_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        return _ensure_key_row(conn)
    finally:
        conn.close()


def _ensure_key_row(conn: sqlite3.Connection) -> str:
    """Reuse or insert the named virtual key.

    Parameters
    ----------
    conn
        Open 9router sqlite connection.

    Returns
    -------
    str
        Active virtual key for ``odysseus-native``.
    """

    row = conn.execute(
        "SELECT key FROM apiKeys WHERE name = ? AND isActive = 1 LIMIT 1",
        (KEY_NAME,),
    ).fetchone()
    if row and row["key"]:
        return str(row["key"])
    key_id = uuid.uuid4().hex[:6]
    crc = hmac.new(
        API_KEY_SECRET.encode(),
        (MACHINE_ID + key_id).encode(),
        hashlib.sha256,
    ).hexdigest()[:8]
    key = f"sk-{MACHINE_ID}-{key_id}-{crc}"
    conn.execute(
        "INSERT INTO apiKeys (id, key, name, machineId, isActive, createdAt) VALUES (?, ?, ?, ?, 1, ?)",
        (
            key_id,
            key,
            KEY_NAME,
            MACHINE_ID,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return key


def _pick_litellm_model(virtual_key: str) -> str:
    """Choose ``openai/{catalog_id}`` from the live 9router catalog.

    Parameters
    ----------
    virtual_key
        9router virtual key used only for the catalog GET.

    Returns
    -------
    str
        LiteLLM model id. Falls back to ``openai/auto`` if catalog is empty.
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
    if not ids:
        return NATIVE_LITELLM_MODEL
    chosen = next((item for item in ids if item.startswith("kr/")), ids[0])
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
    log.info(
        "native llm now %s model=%s auth_type=api_key",
        NINE_ROUTER_V1,
        rewritten.get("agent_settings", {}).get("llm", {}).get("model"),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    apply_native_9router_settings()
