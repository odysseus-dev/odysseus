"""mcp_oauth.py — generic OAuth for remote (Streamable HTTP) MCP servers.

Bridges the mcp SDK's OAuthClientProvider (RFC 9728 discovery, Dynamic Client
Registration, authorization-code + PKCE, token refresh) to Odysseus's web
callback route. Tokens and the dynamic registration persist per-server,
encrypted, so the interactive flow runs only once.
"""
import asyncio
import json
import logging
import os
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# OAuth redirect URI registered with every authorization server via DCR. Loopback
# is allowed for native/desktop clients (RFC 8252); remote users finish via the
# paste-back flow. Deployments not reachable at http://localhost:7000 (custom
# port, reverse proxy, or public domain) must set OAUTH_REDIRECT_BASE_URL (or
# APP_PUBLIC_URL) to their externally reachable origin so the redirect lands back
# on Odysseus. APP_PORT is intentionally not used: it is only the Docker host
# port-map; the app always listens on 7000 inside the container.
_REDIRECT_BASE = (
    os.environ.get("OAUTH_REDIRECT_BASE_URL")
    or os.environ.get("APP_PUBLIC_URL")
    or "http://localhost:7000"
).rstrip("/")
REDIRECT_URI = f"{_REDIRECT_BASE}/api/mcp/oauth/callback"

# How long the background connect waits for the user to authorize before giving up.
AUTH_WAIT_SECONDS = 300

_pending: Dict[str, asyncio.Future] = {}   # state -> Future[(code, state)]
_pending_ts: Dict[str, float] = {}         # state -> monotonic timestamp, for pruning
_auth_urls: Dict[str, str] = {}            # server_id -> authorization URL


def _prune_stale() -> None:
    """Drop abandoned flows whose authorization window has elapsed so the
    module-level registries don't grow unbounded (e.g. a user who never
    finishes the browser step)."""
    now = time.monotonic()
    for state in [s for s, ts in _pending_ts.items() if now - ts > AUTH_WAIT_SECONDS]:
        fut = _pending.pop(state, None)
        _pending_ts.pop(state, None)
        if fut is not None and not fut.done():
            fut.cancel()


def _discard_pending(state: Optional[str]) -> None:
    if state is None:
        return
    _pending.pop(state, None)
    _pending_ts.pop(state, None)


def register_pending(state: str) -> asyncio.Future:
    _prune_stale()
    fut = asyncio.get_running_loop().create_future()
    _pending[state] = fut
    _pending_ts[state] = time.monotonic()
    return fut


def resolve_pending(state: str, code: str) -> bool:
    fut = _pending.get(state)
    if fut is not None and not fut.done():
        fut.set_result((code, state))
        return True
    return False


def pop_auth_url(server_id: str) -> Optional[str]:
    return _auth_urls.get(server_id)


def clear_auth_url(server_id: str) -> None:
    _auth_urls.pop(server_id, None)


class DbTokenStorage:
    """SDK TokenStorage backed by the encrypted McpServer.oauth_tokens column."""

    def __init__(self, server_id: str, session_factory=None):
        self.server_id = server_id
        if session_factory is None:
            from core.database import SessionLocal
            session_factory = SessionLocal
        self._sf = session_factory

    def _load(self) -> dict:
        from core.database import McpServer
        db = self._sf()
        try:
            srv = db.query(McpServer).filter(McpServer.id == self.server_id).first()
            if srv and srv.oauth_tokens:
                parsed = json.loads(srv.oauth_tokens)
                if isinstance(parsed, dict):
                    return parsed
        finally:
            db.close()
        return {}

    def _update(self, key: str, value: dict) -> None:
        """Load, set one key, and persist the oauth_tokens JSON in a single
        session/commit (avoids the load+save double round-trip per write)."""
        from core.database import McpServer
        db = self._sf()
        try:
            srv = db.query(McpServer).filter(McpServer.id == self.server_id).first()
            if srv is None:
                return
            data = json.loads(srv.oauth_tokens) if srv.oauth_tokens else {}
            if not isinstance(data, dict):
                data = {}
            data[key] = value
            srv.oauth_tokens = json.dumps(data)
            db.commit()
        finally:
            db.close()

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        data = self._load().get("tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens) -> None:
        self._update("tokens", json.loads(tokens.model_dump_json()))

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        data = self._load().get("client_info")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info) -> None:
        self._update("client_info", json.loads(client_info.model_dump_json()))


def build_provider(server_id: str, url: str, on_redirect=None):
    """Construct an OAuthClientProvider that drives the browser flow via the
    Odysseus callback route.

    on_redirect(authorization_url): optional sync callback invoked the moment
    the authorization URL is known (after discovery + DCR). The manager uses it
    to publish 'needs_auth' + auth_url to connection state regardless of how
    long discovery/DCR took.
    """
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    client_metadata = OAuthClientMetadata(
        client_name="Odysseus",
        redirect_uris=[REDIRECT_URI],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        # Leave scope unset: the SDK applies the MCP scope-selection strategy and
        # overwrites this from the server's WWW-Authenticate / protected-resource
        # metadata before building the auth URL. Hardcoding an OIDC scope here
        # would break the many MCP servers that are not OpenID providers.
        scope=None,
        token_endpoint_auth_method="none",
    )

    async def redirect_handler(authorization_url: str) -> None:
        state = (parse_qs(urlparse(authorization_url).query).get("state") or [None])[0]
        if state:
            register_pending(state)
        _auth_urls[server_id] = authorization_url
        if on_redirect is not None:
            try:
                on_redirect(authorization_url)
            except Exception as e:
                logger.warning(f"MCP OAuth on_redirect callback failed: {e}")
        logger.info(f"MCP OAuth: server {server_id} awaiting authorization (state={state})")

    async def callback_handler() -> Tuple[str, Optional[str]]:
        auth_url = _auth_urls.get(server_id)
        state = (parse_qs(urlparse(auth_url).query).get("state") or [None])[0] if auth_url else None
        fut = _pending.get(state)
        if fut is None:
            raise RuntimeError("No pending OAuth flow for this server")
        try:
            code, ret_state = await asyncio.wait_for(fut, timeout=AUTH_WAIT_SECONDS)
            return code, ret_state
        finally:
            _discard_pending(state)
            _auth_urls.pop(server_id, None)

    return OAuthClientProvider(
        server_url=url,
        client_metadata=client_metadata,
        storage=DbTokenStorage(server_id),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
