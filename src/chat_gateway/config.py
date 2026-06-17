"""Chat Gateway configuration.

Lean and self-contained: reads `data/chat_gateway.yaml` (off by default) rather
than threading new keys through the app-wide settings schema. Env-var
references like ${MATTERMOST_BOT_TOKEN} are expanded so tokens never sit in the
file in plaintext if you don't want them to.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger("chat_gateway")

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value):
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass
class ToolsetGate:
    mode: str = "all"            # all | allow | deny
    allow: List[str] = field(default_factory=list)
    deny: List[str] = field(default_factory=list)


@dataclass
class PlatformConfig:
    name: str
    enabled: bool = False
    require_mention: bool = True
    channels: List[str] = field(default_factory=list)            # empty = any channel the bot is in
    free_response_channels: List[str] = field(default_factory=list)  # answer here WITHOUT a mention
    toolsets: ToolsetGate = field(default_factory=ToolsetGate)
    options: Dict = field(default_factory=dict)          # adapter-specific (base_url, token, ...)


@dataclass
class GatewayConfig:
    enabled: bool = False
    owner: str = ""                                       # Odysseus user the agent acts as
    platforms: Dict[str, PlatformConfig] = field(default_factory=dict)

    def enabled_platforms(self) -> List[PlatformConfig]:
        return [p for p in self.platforms.values() if p.enabled]


def _config_path() -> str:
    # data/ dir relative to the Odysseus repo root, matching how the app stores
    # app.db, settings.json, etc.
    base = os.environ.get("ODYSSEUS_DATA_DIR")
    if base:
        return os.path.join(base, "chat_gateway.yaml")
    here = os.path.dirname(os.path.abspath(__file__))      # src/chat_gateway
    repo_root = os.path.abspath(os.path.join(here, "..", ".."))
    return os.path.join(repo_root, "data", "chat_gateway.yaml")


def load_gateway_config(path: str | None = None) -> GatewayConfig:
    """Load and validate the gateway config. Returns a disabled config if the
    file is absent or malformed (fail-safe: the gateway simply doesn't start)."""
    path = path or _config_path()
    if not os.path.exists(path):
        logger.info("chat_gateway: no config at %s — gateway disabled", path)
        return GatewayConfig()

    try:
        import yaml  # PyYAML is already a dependency of Odysseus
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as e:
        logger.warning("chat_gateway: failed to read %s (%s) — gateway disabled", path, e)
        return GatewayConfig()

    raw = _expand_env(raw)
    cfg = GatewayConfig(
        enabled=bool(raw.get("enabled", False)),
        owner=str(raw.get("owner", "") or ""),
    )
    for name, block in (raw.get("platforms") or {}).items():
        block = block or {}
        ts = block.get("toolsets") or {}
        cfg.platforms[name] = PlatformConfig(
            name=name,
            enabled=bool(block.get("enabled", False)),
            require_mention=bool(block.get("require_mention", True)),
            channels=list(block.get("channels") or []),
            free_response_channels=list(block.get("free_response_channels") or []),
            toolsets=ToolsetGate(
                mode=str(ts.get("mode", "all")),
                allow=list(ts.get("allow") or []),
                deny=list(ts.get("deny") or []),
            ),
            # NB: `channels` is also passed through to the adapter — IRC needs it
            # as the JOIN list (Mattermost/Matrix get added/invited instead).
            options={k: v for k, v in block.items()
                     if k not in ("enabled", "require_mention", "toolsets", "free_response_channels")},
        )
    return cfg
