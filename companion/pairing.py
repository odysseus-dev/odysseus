"""Shared pairing helpers for the companion bridge.

Token minting + LAN discovery + QR rendering, kept here as small, importable
units so the route layer stays thin and the logic is directly testable.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import socket
import uuid
from urllib.parse import urlsplit

import bcrypt

from src.constants import AUTH_FILE

PAIRING_VERSION = 1
COMPANION_SCOPE = "chat"


_COMPANION_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
    )
)
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def _valid_companion_client_host(host: str) -> bool:
    """Match the host forms supported by the current v1 Expo client."""
    if not host or len(host) > 253 or not host.isascii() or "%" in host:
        return False

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if any(not _DNS_LABEL_RE.fullmatch(label) for label in labels):
            return False
        if any(label.startswith("xn--") for label in labels):
            return False
        # WHATWG URL parsers treat a decimal or ``0x`` single-label hostname
        # as an IPv4 number even though Python's strict ``ipaddress`` parser
        # rejects that spelling.  The v1 client interpolates this host back
        # into a URL, so accepting e.g. ``134744072`` would make the phone send
        # its bearer token to public 8.8.8.8.  Keep DNS labels unambiguous.
        if len(labels) == 1 and (
            labels[0].isdigit()
            or re.fullmatch(r"0x[0-9a-f]*", labels[0]) is not None
        ):
            return False
        return len(labels) == 1 or (len(labels) >= 2 and labels[-1] == "local")

    return isinstance(address, ipaddress.IPv4Address) and any(
        address in network for network in _COMPANION_IPV4_NETWORKS
    )


def parse_companion_base_url(value: str) -> tuple[str, int]:
    """Validate a v1 companion address and return its legacy (host, port).

    The deployed client understands only HTTP plus a LAN-style host and port.
    Reject anything outside that exact contract instead of advertising a URL
    the client would reject, downgrade, or interpret differently.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("COMPANION_BASE_URL must be a canonical HTTP LAN origin")
    if not value.isascii():
        raise ValueError("COMPANION_BASE_URL must contain only ASCII characters")
    if any(
        ord(char) <= 32 or ord(char) == 127 or char in {"\\", "%"}
        for char in value
    ):
        raise ValueError(
            "COMPANION_BASE_URL contains a forbidden character"
        )

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("COMPANION_BASE_URL must be a valid HTTP LAN origin") from exc

    host = parsed.hostname
    if parsed.scheme.lower() != "http" or not parsed.netloc or not host:
        raise ValueError("COMPANION_BASE_URL must be a canonical HTTP LAN origin")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("COMPANION_BASE_URL must not contain credentials")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("COMPANION_BASE_URL must not contain a path, query, or fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("COMPANION_BASE_URL port must be between 1 and 65535")
    if not _valid_companion_client_host(host):
        raise ValueError("COMPANION_BASE_URL host is not supported by companion v1")

    netloc = f"{host}:{port}" if port is not None else host
    origin = f"http://{netloc}"
    if value != origin:
        raise ValueError("COMPANION_BASE_URL must be a canonical HTTP LAN origin")
    return host, port or 80


def configured_companion_origin() -> tuple[str, int] | None:
    """Return the validated operator-configured v1 address, if any."""
    value = os.environ.get("COMPANION_BASE_URL")
    if value is None or value == "":
        return None
    return parse_companion_base_url(value)


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
