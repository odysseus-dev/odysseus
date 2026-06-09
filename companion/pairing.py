"""Shared pairing helpers for the companion bridge.

Token minting + LAN discovery + QR rendering, kept here as small, importable
units so the route layer stays thin and the logic is directly testable.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import uuid

import bcrypt

from src.constants import AUTH_FILE

PAIRING_VERSION = 1
COMPANION_SCOPE = "chat"


def default_port() -> int:
    """Best guess at the port the server is reachable on. Callers that know the
    real request port should pass it explicitly."""
    try:
        return int(os.environ.get("APP_PORT", "7000"))
    except ValueError:
        return 7000


def lan_ip_candidates() -> list[str]:
    """Likely LAN IPv4 addresses for this host, best candidate first.

    The UDP-connect trick reveals the egress interface the OS would use to reach
    the default gateway -- i.e. the address a phone on the same Wi-Fi should
    target. No packets are actually sent. Loopback is dropped.
    """
    candidates: list[str] = []

    def _add(ip):
        if ip and ip not in candidates and not ip.startswith("127."):
            candidates.append(ip)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        _add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            _add(info[4][0])
    except OSError:
        pass

    return candidates


def find_admin_user() -> str | None:
    """Resolve an admin username from data/auth.json (schema uses is_admin),
    falling back to the first user."""
    auth_path = AUTH_FILE
    try:
        with open(auth_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    users = data.get("users") or {}
    if not isinstance(users, dict):
        return None
    for uname, udata in users.items():
        if isinstance(udata, dict) and udata.get("is_admin") is True:
            return uname
    return next(iter(users), None)


def mint_token(owner: str, name: str = "companion") -> tuple[str, str]:
    """Create a chat-scoped API token row and return (token_id, raw_token).

    The raw token is returned ONCE -- only its bcrypt hash + an 8-char prefix
    are persisted. Mirrors routes/api_token_routes.py so cookie- and
    companion-minted tokens are indistinguishable to the auth middleware.
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
    """The exact JSON a client scans / accepts. Keep keys stable."""
    return {"v": PAIRING_VERSION, "host": host, "port": port, "token": token}


def pairing_qr_png_data_uri(payload: dict) -> str | None:
    """Render the pairing payload as a QR `data:` URI for an <img>. Returns None
    if the optional qrcode dep is unavailable."""
    try:
        import base64
        import io

        import qrcode

        img = qrcode.make(json.dumps(payload, separators=(",", ":")))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None
