"""Scrobbling a ListenBrainz (gratis, sin API key de lectura).

Token: `LISTENBRAINZ_TOKEN` o `~/.config/odysseus/listenbrainz.token` (600).
Si no hay token, no hace nada. Los envíos van en hilo aparte y nunca rompen la reproducción.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
_API = "https://api.listenbrainz.org/1/submit-listens"
_TOKEN_FILE = Path.home() / ".config" / "odysseus" / "listenbrainz.token"


def _token() -> Optional[str]:
    tok = (os.getenv("LISTENBRAINZ_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        if _TOKEN_FILE.is_file():
            return _TOKEN_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    return None


def _send(payload: Dict[str, Any], token: str) -> None:
    try:
        import httpx
        r = httpx.post(_API, json=payload, headers={"Authorization": f"Token {token}"}, timeout=8)
        if r.status_code >= 300:
            logger.warning("ListenBrainz %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        logger.debug("ListenBrainz envío falló: %s", exc)


def submit(play: Dict[str, Any]) -> None:
    token = _token()
    if not token:
        return
    artist = str(play.get("artist") or "").strip()
    title = str(play.get("title") or "").strip()
    if not artist or not title:
        return
    ts = int(float(play.get("ts") or 0))
    payload = {
        "listen_type": "single" if play.get("completed") else "playing_now",
        "payload": [{
            "listened_at": ts,
            "track_metadata": {
                "artist_name": artist,
                "track_name": title,
                "release_name": str(play.get("album") or "") or None,
                "additional_info": {"media_player": "Odysseus", "submission_client": "Odysseus"},
            },
        }],
    }
    if not play.get("completed"):
        payload["payload"][0].pop("listened_at", None)
    threading.Thread(target=_send, args=(payload, token), daemon=True).start()
