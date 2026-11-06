"""Tickets de corta vida para el reproductor remoto (EventSource y <audio> no envían cabeceras).

Formato: base64url(json{u, s, e}) "." base64url(HMAC-SHA256(clave, payload)).
Alcance (s) acotado a los scopes que se piden; expiración (e) en epoch segundos.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

SCOPE_STREAM = "music:stream"
SCOPE_EVENTS = "music:events"
ALLOWED_SCOPES = frozenset({SCOPE_STREAM, SCOPE_EVENTS})
DEFAULT_TTL_SEC = 600

_KEY_NAME = "music_ticket.key"


def _ttl_sec() -> int:
    try:
        return max(1, int(os.getenv("MUSIC_TICKET_TTL_SEC", DEFAULT_TTL_SEC)))
    except ValueError:
        return DEFAULT_TTL_SEC


def _key_dir() -> Path:
    override = os.getenv("ODYSSEUS_DATA_DIR")
    if override:
        return Path(override).expanduser()
    from src.constants import DATA_DIR

    return Path(DATA_DIR)


def _key() -> bytes:
    path = _key_dir() / _KEY_NAME
    if path.is_file():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            return raw.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_hex(32)
    path.write_text(raw + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return raw.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    return _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())


def issue_ticket(user: str, scopes=ALLOWED_SCOPES, ttl: int | None = None, guest_music: bool = False) -> dict:
    scopes = sorted(set(scopes) & ALLOWED_SCOPES)
    if not scopes:
        raise ValueError("no music scopes requested")
    expires = int(time.time()) + (ttl if ttl is not None else _ttl_sec())
    body = {"u": user, "s": scopes, "e": expires}
    if guest_music:
        body["g"] = True
    payload = _b64(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    return {"ticket": f"{payload}.{_sign(payload)}", "expires_at": expires, "scopes": scopes}


def verify_ticket(token: str | None, scope: str) -> str | None:
    """Devuelve el usuario si la firma, el alcance y la expiración son válidos; si no, None."""
    claims = verify_ticket_claims(token, scope)
    return claims["u"] if claims else None


def verify_ticket_claims(token: str | None, scope: str) -> dict | None:
    """Cuerpo firmado del ticket (u, s, e, g) si es válido para ese alcance; si no, None."""
    if not token or "." not in token or scope not in ALLOWED_SCOPES:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        body = json.loads(_unb64(payload))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    if int(body.get("e", 0)) < time.time():
        return None
    if scope not in (body.get("s") or []):
        return None
    user = body.get("u")
    if not isinstance(user, str) or not user:
        return None
    return body
