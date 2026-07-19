# src/copilot.py
"""GitHub Copilot provider support.

Copilot exposes an OpenAI-compatible API at ``https://api.githubcopilot.com``
(``/chat/completions`` + ``/models``). Authentication is a GitHub OAuth
**device flow**: the user authorises a device code in their browser and we
receive a long-lived ``access_token`` that is sent directly as
``Authorization: Bearer <token>`` — there is no separate Copilot-token
exchange and no refresh (mirrors how editors / opencode talk to Copilot).

The only provider-specific wrinkle beyond the bearer token is a handful of
required request headers (API version, intent, an editor-style User-Agent,
and ``x-initiator`` for agent-vs-user request accounting). Those live in
:func:`copilot_headers`.

This module holds the constants + pure helpers; the HTTP device-flow calls
live in :mod:`routes.copilot_routes` so they can be auth-gated.
"""

import os
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# GitHub OAuth client id used for the device flow. Copilot's token endpoint
# only accepts client ids that GitHub has allow-listed for Copilot access, so
# we reuse the public VS Code client id (the de-facto standard third-party
# clients use). Override via env if you register your own allow-listed app.
COPILOT_CLIENT_ID = os.environ.get(
    "ODYSSEUS_COPILOT_CLIENT_ID", "01ab8ac9400c4e429b23"
)

# Dated API version header required by the Copilot API (models + chat).
COPILOT_API_VERSION = os.environ.get(
    "ODYSSEUS_COPILOT_API_VERSION", "2026-06-01"
)

# Public Copilot API base. GitHub Enterprise uses ``copilot-api.<domain>``.
COPILOT_BASE = "https://api.githubcopilot.com"

# Copilot wants an editor-like User-Agent + integration id. These identify the
# client to GitHub; keep them stable.
COPILOT_USER_AGENT = os.environ.get(
    "ODYSSEUS_COPILOT_USER_AGENT", "Odysseus/1.0"
)
COPILOT_INTEGRATION_ID = os.environ.get(
    "ODYSSEUS_COPILOT_INTEGRATION_ID", "vscode-chat"
)
COPILOT_EDITOR_VERSION = os.environ.get(
    "ODYSSEUS_COPILOT_EDITOR_VERSION", "Odysseus/1.0"
)

# OAuth scope requested during the device flow.
COPILOT_SCOPE = "read:user"

# Default GitHub host for the device flow (public github.com).
GITHUB_HOST = "github.com"


def device_code_url(host: str = GITHUB_HOST) -> str:
    return f"https://{host}/login/device/code"


def access_token_url(host: str = GITHUB_HOST) -> str:
    return f"https://{host}/login/oauth/access_token"


def normalize_domain(url: str) -> str:
    """Strip scheme/trailing slash from a GitHub Enterprise URL or domain."""
    return (url or "").replace("https://", "").replace("http://", "").rstrip("/")


def enterprise_base(enterprise_url: Optional[str]) -> str:
    """Return the Copilot API base for a deployment.

    Public github.com → ``https://api.githubcopilot.com``.
    Enterprise <domain> → ``https://copilot-api.<domain>``.
    """
    if not enterprise_url:
        return COPILOT_BASE
    return f"https://copilot-api.{normalize_domain(enterprise_url)}"


def is_copilot_base(url: Optional[str]) -> bool:
    """True if a base URL points at the Copilot API (public or enterprise)."""
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    if not host:
        return False
    # Public: api.githubcopilot.com (or any *.githubcopilot.com).
    if host == "githubcopilot.com" or host.endswith(".githubcopilot.com"):
        return True
    # Enterprise: copilot-api.<domain>.
    if host.startswith("copilot-api."):
        return True
    return False


def copilot_headers(
    api_key: Optional[str],
    *,
    agent: bool = False,
    vision: bool = False,
) -> Dict[str, str]:
    """Build the Copilot-specific request headers.

    Args:
        api_key: the GitHub device-flow access token (sent as Bearer).
        agent:   request originates from the agent loop (a tool-driven turn)
                 rather than a direct user message. Sets ``x-initiator`` for
                 Copilot's agent-vs-user request accounting.
        vision:  the request carries an image part.
    """
    headers: Dict[str, str] = {
        "X-GitHub-Api-Version": COPILOT_API_VERSION,
        "Openai-Intent": "conversation-edits",
        "User-Agent": COPILOT_USER_AGENT,
        "Editor-Version": COPILOT_EDITOR_VERSION,
        "Copilot-Integration-Id": COPILOT_INTEGRATION_ID,
        "x-initiator": "agent" if agent else "user",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if vision:
        headers["Copilot-Vision-Request"] = "true"
    return headers


# ---------------------------------------------------------------------------
# Device-flow OAuth (pure HTTP; orchestration lives in routes.copilot_routes)
# ---------------------------------------------------------------------------

def _oauth_post_headers() -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": COPILOT_USER_AGENT,
    }


def request_device_code(host: str = GITHUB_HOST, *, timeout: float = 10.0) -> Dict:
    """Start the device flow. Returns GitHub's
    ``{device_code, user_code, verification_uri, expires_in, interval}``.
    """
    r = httpx.post(
        device_code_url(host),
        headers=_oauth_post_headers(),
        json={"client_id": COPILOT_CLIENT_ID, "scope": COPILOT_SCOPE},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def poll_access_token(host: str, device_code: str, *, timeout: float = 10.0) -> Dict:
    """Poll once for the access token. GitHub returns HTTP 200 with an
    ``error`` field (``authorization_pending``/``slow_down``) while the user
    hasn't authorised yet, or ``{access_token, ...}`` once they have.
    """
    r = httpx.post(
        access_token_url(host),
        headers=_oauth_post_headers(),
        json={
            "client_id": COPILOT_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def fetch_models(base: str, token: str, *, timeout: float = 15.0) -> List[Dict]:
    """Fetch Copilot's model catalogue, filtered to picker-enabled models.

    Returns a list of ``{id, tool_calls, vision}`` dicts. Falls back to the
    full list if no model advertises ``model_picker_enabled`` (defensive
    against API-shape drift).
    """
    url = base.rstrip("/") + "/models"
    r = httpx.get(url, headers=copilot_headers(token), timeout=timeout)
    r.raise_for_status()
    data = (r.json() or {}).get("data") or []

    def _parse(item: Dict) -> Optional[Dict]:
        mid = item.get("id")
        if not mid:
            return None
        supports = ((item.get("capabilities") or {}).get("supports")) or {}
        return {
            "id": mid,
            "tool_calls": bool(supports.get("tool_calls")),
            "vision": bool(supports.get("vision")),
            "picker": bool(item.get("model_picker_enabled")),
        }

    parsed = [p for p in (_parse(it) for it in data) if p]
    picker = [p for p in parsed if p["picker"]]
    chosen = picker or parsed
    for p in chosen:
        p.pop("picker", None)
    return chosen


# ---------------------------------------------------------------------------
# Per-request header flags
# ---------------------------------------------------------------------------

_IMAGE_PART_TYPES = ("image_url", "input_image", "image")


def request_flags(messages) -> tuple:
    """Derive ``(agent, vision)`` from an OpenAI-style message list.

    Mirrors opencode's logic:
      * ``agent`` — the last message is *not* a plain user message (i.e. it's a
        tool result / assistant follow-up), so Copilot should treat the request
        as agent-initiated for request accounting.
      * ``vision`` — any message carries an image content part.
    """
    msgs = messages or []
    last = msgs[-1] if msgs else None
    # A message element can be a non-dict (clients send `"messages": ["hi"]`);
    # the vision loop below already guards each element with isinstance, so do
    # the same here rather than call .get() on a bare string.
    agent = isinstance(last, dict) and last.get("role") != "user"
    vision = False
    for m in msgs:
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, list) and any(
            isinstance(p, dict) and p.get("type") in _IMAGE_PART_TYPES for p in content
        ):
            vision = True
            break
    return agent, vision


def apply_request_headers(headers: Dict[str, str], messages) -> Dict[str, str]:
    """Set ``x-initiator`` / ``Copilot-Vision-Request`` on a header dict based
    on the outgoing messages. Mutates and returns ``headers``."""
    agent, vision = request_flags(messages)
    headers["x-initiator"] = "agent" if agent else "user"
    if vision:
        headers["Copilot-Vision-Request"] = "true"
    return headers

