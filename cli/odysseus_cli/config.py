"""Configuration for the Odysseus CLI.

Resolution order (highest priority first):
  1. CLI flags (handled in cli.py)
  2. Environment variables (ODYSSEUS_CLI_*)
  3. ~/.odysseus/cli.toml
  4. Built-in defaults

The CLI runs on the *host* (not inside Docker), so the default endpoint
points at a local Ollama on localhost — not host.docker.internal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

try:  # Python 3.11+ stdlib; fall back gracefully if missing.
    import tomllib  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

CONFIG_PATH = Path.home() / ".odysseus" / "cli.toml"

# Default local model server. Ollama speaks the OpenAI-compatible API at /v1.
DEFAULT_ENDPOINT = "http://localhost:11434/v1"
# Default model. Swap to another local model (e.g. qwen2.5-coder:7b) via
# --model, ~/.odysseus/cli.toml, or ODYSSEUS_CLI_MODEL once one is pulled.
DEFAULT_MODEL = "gemma4:12b"

# Approval policies for tools that touch the system.
#   "ask"   — prompt before every bash / write / edit (Claude Code style)
#   "auto"  — never prompt (a.k.a. --yolo); use only in throwaway dirs
#   "deny"  — block all system-mutating tools (read-only agent)
APPROVAL_ASK = "ask"
APPROVAL_AUTO = "auto"
APPROVAL_DENY = "deny"

# Tools that mutate the host (shell / filesystem). Gated by the approval policy.
MUTATING_TOOLS = {"bash", "python", "write_file", "edit_document"}


def to_chat_completions_url(base: str) -> str:
    """Normalize a user-supplied endpoint to the full chat-completions URL.

    The Odysseus agent loop POSTs directly to the URL it is given (it does not
    append a path), so we expand the friendly forms here:
        http://host:11434              -> http://host:11434/v1/chat/completions
        http://host:11434/v1           -> http://host:11434/v1/chat/completions
        http://host:11434/v1/chat/completions -> unchanged
    """
    u = base.rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    if u.endswith("/v1"):
        return u + "/chat/completions"
    return u + "/v1/chat/completions"


@dataclass(frozen=True)
class CliConfig:
    endpoint: str = DEFAULT_ENDPOINT
    model: str = DEFAULT_MODEL
    approval: str = APPROVAL_ASK
    temperature: float = 0.2
    max_tokens: int = 4096
    max_rounds: int = 20
    context_length: int = 32768
    # Project root the agent's file/shell tools are scoped to.
    project_root: Path = field(default_factory=Path.cwd)
    # Owner string handed to the agent loop. An admin owner unlocks the full
    # tool surface (bash/python/file ops) past the server-side gate.
    owner: Optional[str] = None
    api_key: Optional[str] = None

    def with_overrides(self, **kwargs) -> "CliConfig":
        """Return a new config with the given non-None overrides applied."""
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **clean)


def _load_file(path: Path) -> dict:
    if tomllib is None or not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _from_env() -> dict:
    """Read ODYSSEUS_CLI_* environment overrides."""
    env = os.environ
    out: dict = {}
    if v := env.get("ODYSSEUS_CLI_ENDPOINT"):
        out["endpoint"] = v
    if v := env.get("ODYSSEUS_CLI_MODEL"):
        out["model"] = v
    if v := env.get("ODYSSEUS_CLI_APPROVAL"):
        out["approval"] = v
    if v := env.get("ODYSSEUS_CLI_API_KEY"):
        out["api_key"] = v
    if v := env.get("ODYSSEUS_CLI_OWNER"):
        out["owner"] = v
    return out


def load_config() -> CliConfig:
    """Build the effective config from file + env (flags applied later)."""
    data = _load_file(CONFIG_PATH)
    cfg = CliConfig()

    file_overrides = {
        k: data[k]
        for k in ("endpoint", "model", "approval", "owner", "api_key")
        if k in data
    }
    for num in ("temperature", "max_tokens", "max_rounds", "context_length"):
        if num in data:
            file_overrides[num] = data[num]

    cfg = cfg.with_overrides(**file_overrides)
    cfg = cfg.with_overrides(**_from_env())
    return cfg
