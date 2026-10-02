# services/laya/config.py
"""Configuration for the laya decision-engine integration.

laya (https://github.com/NandhaKishorM/laya, Apache-2.0) is a non-autoregressive
"System 1" decision engine. Odysseus talks to it over HTTP (the `laya-serve`
wire protocol) running as a sidecar, so this process never imports torch /
transformers. Everything here is read from the environment at call time so the
feature flag can be toggled without a restart, and so tests can set it per-case.

The integration is OFF by default (`LAYA_ENABLED=false`): with it off nothing in
this package touches the network and the rest of Odysseus behaves exactly as it
did before laya existed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LayaConfig:
    """Resolved laya integration settings.

    Attributes:
        enabled:   master feature flag; when False the service is a no-op.
        url:       base URL of the laya-serve sidecar (no trailing slash).
        api_key:   bearer token sent as ``Authorization: Bearer <key>`` when set.
        timeout:   per-request timeout in seconds (health + predict).
        retries:   extra attempts on *transient* failures only (never on 4xx).
                   Safe to retry because laya has no side effects — it only
                   classifies text and returns probabilities.
        backoff:   base seconds between transient retries (linear).
    """

    enabled: bool = False
    url: str = "http://127.0.0.1:8000"
    api_key: str = ""
    timeout: float = 5.0
    retries: int = 2
    backoff: float = 0.2
    # Per-capability activation. guard_mode gates the prompt guardrail on the
    # chat path: "off" (default, not run), "warn" (run + audit, never blocks),
    # "block" (run + audit + reject flagged requests — the opt-in fail-closed
    # mode). Everything else stays shadow-only until its own milestone.
    guard_mode: str = "off"

    @classmethod
    def from_env(cls) -> "LayaConfig":
        url = (os.getenv("LAYA_URL") or cls.url).strip().rstrip("/")
        return cls(
            enabled=_env_bool("LAYA_ENABLED", cls.enabled),
            url=url or cls.url,
            api_key=(os.getenv("LAYA_API_KEY") or "").strip(),
            timeout=_env_float("LAYA_TIMEOUT", cls.timeout),
            retries=max(0, _env_int("LAYA_RETRIES", cls.retries)),
            backoff=max(0.0, _env_float("LAYA_RETRY_BACKOFF", cls.backoff)),
            guard_mode=_guard_mode("LAYA_GUARD_MODE", cls.guard_mode),
        )


_GUARD_MODES = ("off", "warn", "block")


def _guard_mode(name: str, default: str) -> str:
    raw = (os.getenv(name) or default).strip().lower()
    return raw if raw in _GUARD_MODES else "off"
