"""Cookie firmada que el hub deja tras validar usuario+clave de Navidrome.

El proxy de /music la lee y reenvía el usuario de Navidrome en X-Authenticated-User
(ReverseProxyUserHeader). Sin cookie válida, el proxy no envía la cabecera y
Navidrome muestra su propio login.

Formato: base64url(json{u, i, e}) "." base64url(HMAC-SHA256(clave, payload)).
u = usuario de Navidrome, i = id de la invitación del miembro, e = expiración (epoch s).
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

COOKIE_NAME = "odysseus_music"
COOKIE_PATH = "/music"
TTL_SEC = 180 * 86400

_KEY_NAME = "music_sso.key"


def _key() -> bytes:
    path = Path(os.getenv("ODYSSEUS_DATA_DIR", "data")).expanduser() / _KEY_NAME
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


def issue(user: str, inv_id: str) -> str:
    body = {"u": user, "i": inv_id, "e": int(time.time()) + TTL_SEC}
    payload = _b64(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_sign(payload)}"


def read(cookie: str | None) -> dict | None:
    """Claims {u, i} si la firma y la expiración son válidas; None si no."""
    if not cookie or "." not in cookie:
        return None
    payload, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        body = json.loads(_unb64(payload))
    except ValueError:
        return None
    if not isinstance(body, dict) or int(body.get("e") or 0) < time.time():
        return None
    user, inv_id = body.get("u"), body.get("i")
    if not isinstance(user, str) or not user or not isinstance(inv_id, str) or not inv_id:
        return None
    return {"u": user, "i": inv_id}
