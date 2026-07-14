"""
diffusion_server.py — lightweight diffusion API wrapper helpers.

This file intentionally keeps its security helpers import-light so the test
suite can AST-extract and execute them without pulling in torch/diffusers.
"""

from __future__ import annotations

from typing import Iterable, List, Optional


# Default-deny: do NOT allow cross-origin reads by default.
_DEFAULT_CORS_ORIGINS: list[str] = []

# Loopback + localhost allowlist: baseline for local-only servers.
_DEFAULT_ALLOWED_HOSTS: list[str] = ["127.0.0.1", "localhost", "::1"]


def _compute_allowed_hosts(bind_host: str, extras: Optional[Iterable[str]] = None) -> list[str]:
    """Compute TrustedHost allowlist in stable order.

    - Includes the bind host as the first entry (even for 0.0.0.0).
    - Includes loopback defaults (127.0.0.1, localhost, ::1).
    - Extras are appended in order after stripping; blanks are ignored.
    - Duplicates are removed while preserving first-seen order.
    - A wildcard "*" is never added implicitly; if explicitly provided, it is kept.
    """

    items: list[str] = []
    first = str(bind_host or "").strip()
    if first:
        items.append(first)
    items.extend(_DEFAULT_ALLOWED_HOSTS)
    if extras:
        for x in extras:
            s = str(x or "").strip()
            if s:
                items.append(s)

    seen: set[str] = set()
    out: list[str] = []
    for s in items:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _compute_cors_origins(extras: Optional[Iterable[str]] = None) -> list[str]:
    """Compute CORS allowlist in stable order (default-deny)."""

    items: list[str] = list(_DEFAULT_CORS_ORIGINS)
    if extras:
        for x in extras:
            s = str(x or "").strip()
            if s:
                items.append(s)
    seen: set[str] = set()
    out: list[str] = []
    for s in items:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _configure_security_middleware(app, allowed_hosts: List[str], allowed_origins: List[str]) -> None:
    """Wire TrustedHost + (optional) CORS onto a FastAPI app.

    Ordering matters:
    - TrustedHost must be installed.
    - CORS, when enabled, must be outermost (added last) so it wraps TrustedHost.

    Idempotent before serving: re-running replaces the middleware stack.
    Refuses to mutate after the middleware stack is built (app has started serving).
    """

    if getattr(app, "middleware_stack", None) is not None:
        raise RuntimeError("Cannot reconfigure middleware after stack is built")

    # Reset any prior config (idempotent pre-serve).
    app.user_middleware = []
    app.middleware_stack = None

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts or []))
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

