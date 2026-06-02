"""Shared pairing helpers for the mobile companion.

Used by both the in-process admin endpoint (companion/routes.py) and the
standalone CLI (scripts/pair_mobile.py). Keeping the token minting + LAN
discovery here means the two entry points stay byte-for-byte consistent.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import uuid

import bcrypt

PAIRING_VERSION = 1
COMPANION_SCOPE = "chat"


def default_port() -> int:
    """Best guess at the port the server is reachable on. The actual serving
    port can be overridden at launch (APP_PORT, or 7860 on macOS), so callers
    that know the real request port should pass it explicitly."""
    try:
        return int(os.environ.get("APP_PORT", "7000"))
    except ValueError:
        return 7000


def lan_ip_candidates() -> list[str]:
    """Return likely LAN IPv4 addresses for this host, best candidate first.

    The UDP-connect trick reveals the address the OS would use to reach the
    default gateway — i.e. the one a phone on the same Wi-Fi should target. We
    also sweep getaddrinfo for any extra private-range addresses (multi-homed
    hosts, VPNs) and de-dupe, dropping loopback."""
    candidates: list[str] = []

    def _add(ip: str | None) -> None:
        if ip and ip not in candidates and not ip.startswith("127."):
            candidates.append(ip)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packets are actually sent; this just resolves the egress interface.
        s.connect(("8.8.8.8", 80))
        _add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            _add(info[4][0])
    except OSError:
        pass

    return candidates


def find_admin_user() -> str | None:
    """Resolve an admin username from data/auth.json (schema uses is_admin),
    falling back to the first user. Mirrors core/database.py."""
    auth_path = os.path.join("data", "auth.json")
    try:
        with open(auth_path, "r", encoding="utf-8") as f:
            users = (json.load(f) or {}).get("users", {})
    except (OSError, json.JSONDecodeError):
        return None
    for uname, udata in users.items():
        if udata.get("is_admin") is True:
            return uname
    return next(iter(users), None)


def mint_token(owner: str, name: str = "odysseus-mobile") -> tuple[str, str]:
    """Create a chat-scoped API token row and return (token_id, raw_token).

    The raw token is shown ONCE — only its bcrypt hash is stored. Matches the
    minting logic in routes/api_token_routes.py so cookie- and CLI-minted
    tokens are indistinguishable to the auth middleware.
    """
    from core.database import get_db_session, ApiToken

    raw_token = "ody_" + secrets.token_urlsafe(32)
    token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()
    token_id = str(uuid.uuid4())[:8]

    with get_db_session() as db:
        db.add(ApiToken(
            id=token_id,
            owner=owner,
            name=name,
            token_hash=token_hash,
            token_prefix=raw_token[:8],
            scopes=COMPANION_SCOPE,
            is_active=True,
        ))
    return token_id, raw_token


def pairing_payload(host: str, port: int, token: str) -> dict:
    """The exact JSON the phone scans / accepts. Keep keys stable — the mobile
    client parses this verbatim."""
    return {"v": PAIRING_VERSION, "host": host, "port": port, "token": token}


def pairing_qr_png_data_uri(payload: dict) -> str | None:
    """Render the pairing payload as a QR code, returned as a data: URI for an
    <img>. Returns None if the optional qrcode dep is unavailable."""
    try:
        import base64
        import io

        import qrcode

        img = qrcode.make(json.dumps(payload, separators=(",", ":")))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{b64}"
    except Exception:
        return None
