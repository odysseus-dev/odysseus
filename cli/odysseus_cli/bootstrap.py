"""Bootstrap the Odysseus runtime so the CLI can import the agent core.

Responsibilities:
  * Put the Odysseus repo root on sys.path so ``import src.agent_loop`` works.
  * Provide minimal environment defaults the agent modules expect at import.
  * Unlock the full tool surface (bash/python/file ops). The server's
    tool_security policy gates these for *public* web users; the CLI is an
    explicit local admin tool whose own approval prompts are the real boundary.

Call ``prepare()`` exactly once, before importing anything from ``src``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# cli/odysseus_cli/bootstrap.py  ->  repo root is two parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]

_PREPARED = False


def repo_root() -> Path:
    return REPO_ROOT


def prepare() -> None:
    global _PREPARED
    if _PREPARED:
        return

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    # Minimal env defaults so app modules import cleanly. We point at the repo's
    # data dir for settings/db continuity with the running server.
    data_dir = REPO_ROOT / "data"
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{data_dir / 'app.db'}")
    os.environ.setdefault("AUTH_ENABLED", "false")  # CLI is local; no HTTP auth.
    os.environ.setdefault("ODYSSEUS_INPROCESS_POLLERS", "0")
    os.environ.setdefault("ODYSSEUS_INPROCESS_TASKS", "0")

    _quiet_logging()
    _PREPARED = True


def _quiet_logging() -> None:
    """Silence the app's WARNING/INFO chatter so the CLI output stays clean.

    Optional subsystems (embeddings, fastembed, pyotp, tool-index) log noisy
    warnings when their deps aren't installed; none are fatal for the coding
    agent. Set ODYSSEUS_CLI_DEBUG=1 to restore full logging.
    """
    if os.environ.get("ODYSSEUS_CLI_DEBUG"):
        logging.basicConfig(level=logging.DEBUG)
        return
    # Drop everything below ERROR globally; real errors still surface.
    logging.disable(logging.WARNING)
    logging.getLogger().setLevel(logging.ERROR)


def unlock_tools() -> None:
    """Neutralize the public-user tool block so the local agent has full access.

    Must be called after ``prepare()`` and before running the loop. Patches the
    symbol in both its source module and the agent_loop namespace that imported
    it by name.
    """
    import src.tool_security as ts

    def _no_block(owner=None):  # noqa: ANN001
        return set()

    ts.blocked_tools_for_owner = _no_block
    try:
        import src.agent_loop as al
        if hasattr(al, "blocked_tools_for_owner"):
            al.blocked_tools_for_owner = _no_block
    except Exception:
        pass

    # The agent loop also has a MCP manager hook; ensure it's at least set so
    # get_mcp_manager() returns cleanly (None is handled by the loop).
    try:
        import src.agent_tools as at
        if at.get_mcp_manager() is None:
            at.set_mcp_manager(None)
    except Exception:
        pass
