"""Terminal agent routes — tmux-backed Codex/Claude/shell sessions.

Provides an HTTP surface for external terminal agents (Codex, Claude Code,
or raw shell sessions) to interact with Odysseus via scoped API tokens.
Reuses the shell_routes infrastructure for pty/tmux session management and
the codex_routes scope-gating pattern for API token enforcement.
"""

import json
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from core.platform_compat import IS_WINDOWS

logger = logging.getLogger(__name__)


def setup_terminal_agent_routes() -> APIRouter:
    """Build and return the terminal agent router.

    Mounted at /api/terminal so external agent processes (Codex, Claude Code,
    tmux-based shell sessions) can reach Odysseus capabilities through scoped
    API tokens without going through the main chat/agent loop.
    """
    router = APIRouter(prefix="/api/terminal", tags=["terminal"])

    @router.get("/health")
    async def health():
        return {"status": "ok", "service": "terminal-agent"}

    @router.get("/capabilities")
    async def capabilities(request: Request):
        token_scopes = set(getattr(request.state, "api_token_scopes", []) or [])
        has_token = bool(getattr(request.state, "api_token", False))

        def scoped(allowed):
            return bool(token_scopes.intersection(allowed)) if has_token else True

        return {
            "integration": "terminal-agent",
            "token_scopes": sorted(token_scopes),
            "tools": {
                "shell": {
                    "exec": scoped({"shell:exec", "shell:*"}),
                    "stream": scoped({"shell:stream", "shell:*"}),
                },
                "tmux": {
                    "list": scoped({"tmux:read", "tmux:*"}),
                    "attach": scoped({"tmux:write", "tmux:*"}),
                    "kill": scoped({"tmux:write", "tmux:*"}),
                },
            },
        }

    @router.get("/sessions")
    async def list_sessions(request: Request):
        """List active tmux sessions."""
        _require_token(request, {"tmux:read", "tmux:*"})
        try:
            import subprocess
            p = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_id}: #{session_name}"],
                capture_output=True, text=True, timeout=5,
            )
            if p.returncode != 0:
                return {"sessions": [], "error": p.stderr.strip() or "no tmux server running"}
            sessions = []
            for line in p.stdout.strip().splitlines():
                if ":" in line:
                    sid, name = line.split(":", 1)
                    sessions.append({"id": sid.strip(), "name": name.strip()})
            return {"sessions": sessions}
        except FileNotFoundError:
            return {"sessions": [], "error": "tmux not installed"}
        except Exception as e:
            return {"sessions": [], "error": str(e)}

    return router


def _require_token(request: Request, required_scopes: set):
    """Enforce API token scopes, or skip check when running without auth."""
    token_scopes = set(getattr(request.state, "api_token_scopes", []) or [])
    has_token = bool(getattr(request.state, "api_token", False))
    if has_token and not token_scopes.intersection(required_scopes):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403,
            detail=f"API token scopes {sorted(token_scopes)} do not include any of {sorted(required_scopes)}",
        )
