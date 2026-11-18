"""Quién puede leer la biblioteca de música.

La biblioteca pertenece al owner que la configuró (por defecto el primer usuario
de auth.json). Los demás solo entran si music_shared_with_all_users es true.
La política vive en data/music_access.json; se lee en cada decisión (es barato).
Una invitación con music=true da acceso solo a su sesión (guest_music), no a la cuenta.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

_KEY_SHARED = "music_shared_with_all_users"


def _data_dir() -> Path:
    return Path(os.getenv("ODYSSEUS_DATA_DIR", "data")).expanduser()


def _policy_path() -> Path:
    return _data_dir() / "music_access.json"


def _first_auth_user() -> Optional[str]:
    try:
        with open(_data_dir() / "auth.json", "r", encoding="utf-8") as f:
            users = (json.load(f) or {}).get("users") or {}
        return next(iter(users), None)
    except (OSError, ValueError, StopIteration):
        return None


def get_policy() -> Dict[str, Any]:
    raw: Dict[str, Any] = {}
    try:
        with open(_policy_path(), "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
    except (OSError, ValueError):
        raw = {}
    return {
        "owner": str(raw.get("owner") or "") or _first_auth_user(),
        _KEY_SHARED: bool(raw.get(_KEY_SHARED, False)),
    }


def can_read_library(user: Optional[str], guest_music: bool = False) -> bool:
    """Decisión única para emisión de ticket, consumo y middleware.

    owner → sí · sesión de invitado con música → sí · compartida con todos → sí · resto → no.
    """
    if not user:
        return False
    policy = get_policy()
    if user == policy["owner"]:
        return True
    if guest_music:
        return True
    return bool(policy[_KEY_SHARED])
