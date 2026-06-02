"""mcp_oauth.py — generic OAuth for remote (Streamable HTTP) MCP servers.

Bridges the mcp SDK's OAuthClientProvider (RFC 9728 discovery, Dynamic Client
Registration, authorization-code + PKCE, token refresh) to Odysseus's web
callback route. Tokens and the dynamic registration persist per-server,
encrypted, so the interactive flow runs only once.
"""
import asyncio
import json
import logging
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# Loopback redirect is allowed for native/desktop OAuth clients (RFC 8252).
# Remote users complete the flow via paste-back, like the Google MCP path.
REDIRECT_URI = "http://localhost:7000/api/mcp/oauth/callback"

# How long the background connect waits for the user to authorize before giving up.
AUTH_WAIT_SECONDS = 300

_pending: Dict[str, asyncio.Future] = {}   # state -> Future[(code, state)]
_auth_urls: Dict[str, str] = {}            # server_id -> authorization URL


def register_pending(state: str) -> asyncio.Future:
    fut = asyncio.get_running_loop().create_future()
    _pending[state] = fut
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
                return json.loads(srv.oauth_tokens)
        finally:
            db.close()
        return {}

    def _save(self, data: dict) -> None:
        from core.database import McpServer
        db = self._sf()
        try:
            srv = db.query(McpServer).filter(McpServer.id == self.server_id).first()
            if srv is not None:
                srv.oauth_tokens = json.dumps(data)
                db.commit()
        finally:
            db.close()

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        data = self._load().get("tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens) -> None:
        data = self._load()
        data["tokens"] = json.loads(tokens.model_dump_json())
        self._save(data)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        data = self._load().get("client_info")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info) -> None:
        data = self._load()
        data["client_info"] = json.loads(client_info.model_dump_json())
        self._save(data)


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
        scope="openid email offline_access",
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
            _pending.pop(state, None)
            _auth_urls.pop(server_id, None)

    return OAuthClientProvider(
        server_url=url,
        client_metadata=client_metadata,
        storage=DbTokenStorage(server_id),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
