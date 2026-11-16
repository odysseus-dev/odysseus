"""Home Assistant credential delegation via the Supervisor auth API.

Opt-in (ODYSSEUS_HASS_AUTH=1) single sign-on for the Home Assistant OS
add-on deployment: the login form accepts Home Assistant username/password
pairs, which are verified by the Supervisor's ``POST /auth`` endpoint (the
add-on declares ``auth_api: true`` in its config.yaml, so Supervisor grants
access to that endpoint and injects SUPERVISOR_TOKEN into the container).

Security shape:
  - Credentials are sent to Supervisor over the container-local network only;
    no third party is involved. Supervisor asks Home Assistant core, which
    enforces its own rate limiting and auth-provider rules.
  - The Supervisor token is never logged or exposed to the client.
  - Failure to reach Supervisor fails CLOSED (login is refused), so an
    outage can never be mistaken for a successful HA login.
  - Local Odysseus credentials are always tried first, so this only ever
    ADDS a way in — it cannot lock out local accounts.
"""

import logging
import os
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

SUPERVISOR_URL = os.getenv("SUPERVISOR_URL", "http://supervisor").rstrip("/")
# Bound the delegation call so a hung Supervisor can't pin request workers.
AUTH_TIMEOUT = float(os.getenv("ODYSSEUS_HASS_AUTH_TIMEOUT", "5") or 5)

# Home Assistant group ID granted to users in the administrator group.
HA_ADMIN_GROUP = "system-admin"

_TRUTHY = {"1", "true", "yes", "on"}


def _supervisor_token() -> str:
    # Newer Supervisors set SUPERVISOR_TOKEN; very old ones used HASSIO_TOKEN.
    return os.getenv("SUPERVISOR_TOKEN") or os.getenv("HASSIO_TOKEN") or ""


def hass_auth_enabled() -> bool:
    """True when the operator enabled HA credential delegation AND the
    Supervisor handed us a token. Missing token (e.g. add-on built without
    ``auth_api: true``, or a non-HA deployment) disables the feature instead
    of turning every login attempt into a dead network call."""
    if os.getenv("ODYSSEUS_HASS_AUTH", "false").strip().lower() not in _TRUTHY:
        return False
    if not _supervisor_token():
        logger.warning(
            "ODYSSEUS_HASS_AUTH is enabled but no Supervisor token was injected; "
            "Home Assistant credential login is disabled"
        )
        return False
    return True


async def supervisor_check_login(
    username: str, password: str
) -> Tuple[bool, Optional[list]]:
    """Verify username/password against Home Assistant via Supervisor.

    Returns ``(ok, group_ids)``. ``group_ids`` is Home Assistant's group list
    for the user (used to decide admin); ``None`` when unknown. Any transport
    or unexpected response is treated as a failed login, never a success.
    """
    token = _supervisor_token()
    if not token:
        return False, None
    try:
        async with httpx.AsyncClient(timeout=AUTH_TIMEOUT) as client:
            resp = await client.post(
                f"{SUPERVISOR_URL}/auth",
                json={"username": username, "password": password},
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("Supervisor auth request failed: %s", exc)
        return False, None
    if resp.status_code != 200:
        # 401 = Supervisor/Home Assistant rejected the credentials. Anything
        # else is a Supervisor-side error — log it, but still fail closed.
        if resp.status_code != 401:
            logger.warning(
                "Supervisor auth endpoint returned HTTP %s", resp.status_code
            )
        return False, None
    try:
        groups = resp.json().get("group_ids") or []
    except ValueError:
        logger.warning("Supervisor auth endpoint returned invalid JSON")
        return True, None
    if not isinstance(groups, list):
        groups = []
    return True, [str(g) for g in groups]


def hass_user_is_admin(group_ids: Optional[list]) -> bool:
    """Whether Supervisor-reported HA groups mark the user as an admin."""
    return bool(group_ids) and HA_ADMIN_GROUP in (group_ids or [])


# ---------------------------------------------------------------------------
# Supervisor ingress identity (sidebar panel single sign-on)
# ---------------------------------------------------------------------------

# Headers the Supervisor injects on proxied ingress requests after filtering
# any client-supplied versions (supervisor/api/ingress.py:_init_header).
HEADER_REMOTE_USER_ID = "X-Remote-User-Id"
HEADER_REMOTE_USER_NAME = "X-Remote-User-Name"
HEADER_REMOTE_USER_GROUPS = "X-Remote-User-Groups"

_SUPERVISOR_IP_CACHE: Optional[str] = None


def _supervisor_ip() -> Optional[str]:
    """Resolve the Supervisor's IP on the add-on network, cached.

    Only requests originating from this address are trusted to carry the
    X-Remote-User-* identity headers: on the directly exposed port a LAN
    client could otherwise forge them. DNS here is the Supervisor's own
    container name resolution; overridable for unusual deployments via
    ODYSSEUS_INGRESS_TRUSTED_IP. Failure to resolve disables ingress SSO
    (fail closed), it never falls back to trusting the headers.
    """
    global _SUPERVISOR_IP_CACHE
    import socket

    if _SUPERVISOR_IP_CACHE is not None:
        return _SUPERVISOR_IP_CACHE or None
    override = os.getenv("ODYSSEUS_INGRESS_TRUSTED_IP", "").strip()
    if override:
        _SUPERVISOR_IP_CACHE = override
        return override
    try:
        _SUPERVISOR_IP_CACHE = socket.gethostbyname("supervisor")
    except OSError:
        logger.debug("Could not resolve 'supervisor'; ingress SSO disabled")
        _SUPERVISOR_IP_CACHE = ""
    return _SUPERVISOR_IP_CACHE or None


def ingress_identity(request) -> Optional[tuple]:
    """Return (username, group_ids) for a Supervisor-proxied ingress request.

    None unless the connection provably originated from the Supervisor
    container AND it injected a user identity — the panel iframe's request
    carries these only when a Home Assistant user is actually signed in on
    the frontend that rendered it.
    """
    if not hass_auth_enabled():
        return None
    name = request.headers.get(HEADER_REMOTE_USER_NAME, "").strip()
    if not name:
        return None
    # Trust boundary: only the Supervisor's own connections may assert
    # identity headers. Everything else (direct port access) is spoofable.
    peer = request.client.host if request.client else None
    trusted = _supervisor_ip()
    if not peer or not trusted or peer != trusted:
        return None
    groups = [
        g.strip()
        for g in request.headers.get(HEADER_REMOTE_USER_GROUPS, "").split(",")
        if g.strip()
    ]
    return name, groups


def ensure_ha_user(auth_manager, username: str, ha_admin: bool) -> bool:
    """Provision (or verify) the Odysseus account for a verified HA user.

    Same policy as the credential-delegation login: the very first account
    on an unconfigured instance becomes the admin; afterwards HA admins map
    to Odysseus admins and everyone else to a regular account. Local
    passwords are never set from HA material — a random secret is stored so
    the account has no usable local credential.
    """
    from core.auth import RESERVED_USERNAMES

    username = (username or "").strip().lower()
    if not username or username in RESERVED_USERNAMES:
        return False
    if username in auth_manager.users:
        return True
    import secrets as _secrets

    first_account = not auth_manager.is_configured
    is_admin = first_account or ha_admin
    return auth_manager.create_user(
        username,
        _secrets.token_hex(32) + "!Aa1",
        is_admin=is_admin,
    )