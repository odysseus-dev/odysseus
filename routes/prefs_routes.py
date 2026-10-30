"""User preferences API — per-user key/value store backed by a JSON file."""
import json
import threading
from typing import Iterable, Optional
from fastapi import APIRouter, Request
from core.atomic_io import atomic_write_json
from src.auth_helpers import get_current_user
from src.constants import USER_PREFS_FILE

PREFS_FILE = USER_PREFS_FILE
_FOREGROUND_POLICY_KEYS = (
    "foreground_fallback_enabled",
    "foreground_model_fallbacks",
)

# Serializes the read-modify-write in _update_for_user against itself and
# against a whole-map _save_for_user. Reentrant because _update_for_user
# calls _save_for_user while already holding it.
_PREFS_LOCK = threading.RLock()


def _load():
    """Load the raw prefs file (internal use only)."""
    try:
        with open(PREFS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(prefs):
    atomic_write_json(PREFS_FILE, prefs, indent=2)


def _load_for_user(user: Optional[str] = None) -> dict:
    """Load preferences for a specific user.

    Reads take the lock so they cannot observe a write in progress. That
    matters on Windows, where os.replace onto a path another thread currently
    holds open raises PermissionError rather than swapping the file silently.
    """
    with _PREFS_LOCK:
        return _load_for_user_locked(user)


def _load_for_user_locked(user: Optional[str] = None) -> dict:
    all_prefs = _load()
    users = all_prefs.get("_users")
    if isinstance(users, dict):
        if user is None:
            # Auth disabled — return first user's prefs for backward compat
            prefs = dict(next(iter(users.values()), {}))
            # Foreground fallback consent is never borrowed from a named
            # owner. Auth-disabled operation has a separate flat/root opt-in
            # that remains inert when authentication is enabled again.
            for key in _FOREGROUND_POLICY_KEYS:
                prefs.pop(key, None)
                if key in all_prefs:
                    prefs[key] = all_prefs[key]
            return prefs
        prefs = users.get(user, {})
        return dict(prefs) if isinstance(prefs, dict) else {}
    # A legacy flat store belongs only to auth-disabled single-user mode.
    # Copying it into the first named user's new `_users` record during an
    # auth transition would silently transfer another user's preferences and,
    # critically, foreground fallback consent. Named owners therefore start
    # with an empty record and must write their own preferences explicitly.
    return dict(all_prefs) if user is None else {}


def _save_for_user(user: Optional[str], prefs: dict):
    """Replace a user's stored preferences with `prefs`.

    This publishes the caller's map wholesale. Prefer _update_for_user when
    you only mean to change some of the keys.
    """
    with _PREFS_LOCK:
        _save_for_user_locked(user, prefs)


def _save_for_user_locked(user: Optional[str], prefs: dict):
    all_prefs = _load()
    if user is None:
        # Auth disabled. If the store is already multi-user (e.g. auth was
        # turned off on a deployment that previously ran multi-user), writing
        # `prefs` flat would overwrite the whole `_users` map and destroy every
        # other user's preferences. Instead write back into the same (first)
        # slot _load_for_user(None) reads from, preserving the others.
        users = all_prefs.get("_users")
        if isinstance(users, dict):
            first_key = next(iter(users), None)
            if first_key is not None:
                existing_named = users.get(first_key)
                existing_named = (
                    dict(existing_named)
                    if isinstance(existing_named, dict)
                    else {}
                )
                named_foreground = {
                    key: existing_named[key]
                    for key in _FOREGROUND_POLICY_KEYS
                    if key in existing_named
                }
                users[first_key] = {
                    key: value
                    for key, value in prefs.items()
                    if key not in _FOREGROUND_POLICY_KEYS
                }
                users[first_key].update(named_foreground)
                for key in _FOREGROUND_POLICY_KEYS:
                    if key in prefs:
                        all_prefs[key] = prefs[key]
                _save(all_prefs)
                return
        _save(prefs)
        return
    if not isinstance(all_prefs.get("_users"), dict):
        # Preserve the flat single-user object as inert legacy data while
        # creating the first named-owner namespace. In particular, historical
        # fallback values must not be deleted or copied into the new owner.
        all_prefs = dict(all_prefs)
        all_prefs["_users"] = {}
    all_prefs["_users"][user] = prefs
    _save(all_prefs)


def _update_for_user(
    user: Optional[str],
    patch: Optional[dict] = None,
    *,
    remove: Iterable[str] = (),
) -> dict:
    """Merge `patch` into a user's stored preferences and return the result.

    Callers used to load a snapshot, change one key in it, and hand the whole
    map back to _save_for_user. Two overlapping updates to different keys then
    raced: the second save republished a snapshot taken before the first one
    landed, so a write that had reported success disappeared. The file stayed
    valid JSON and nothing was logged, which is what made it silent.

    Re-reading and merging inside the lock means an update only ever publishes
    its own keys against whatever is persisted at that moment, so independent
    changes survive each other regardless of ordering.
    """
    with _PREFS_LOCK:
        current = _load_for_user_locked(user)
        for key in remove:
            current.pop(key, None)
        if patch:
            current.update(patch)
        _save_for_user_locked(user, current)
        return current


def setup_prefs_routes():
    router = APIRouter(prefix="/api/prefs", tags=["preferences"])

    @router.get("")
    async def get_all_prefs(request: Request):
        user = get_current_user(request)
        return _load_for_user(user)

    @router.get("/{key}")
    async def get_pref(request: Request, key: str):
        user = get_current_user(request)
        prefs = _load_for_user(user)
        return {"key": key, "value": prefs.get(key)}

    @router.put("/{key}")
    async def set_pref(request: Request, key: str, body: dict):
        user = get_current_user(request)
        prefs = _update_for_user(user, {key: body.get("value")})
        return {"key": key, "value": prefs.get(key)}

    return router
