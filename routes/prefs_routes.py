"""User preferences API — per-user key/value store backed by a JSON file."""
import json
from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from core.atomic_io import atomic_write_json, store_transaction
from src.auth_helpers import get_current_user
from src.constants import USER_PREFS_FILE

PREFS_FILE = USER_PREFS_FILE
_MAX_PATCH_KEYS = 32
_FOREGROUND_POLICY_KEYS = (
    "foreground_fallback_enabled",
    "foreground_model_fallbacks",
)


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
    """Load preferences for a specific user."""
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


@store_transaction(lambda: PREFS_FILE)
def _update_for_user(user: Optional[str], updates: dict) -> dict:
    """Transactionally update a subset of preferences for a user."""
    all_prefs = _load()
    if user is None:
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
                for key, val in updates.items():
                    if key in _FOREGROUND_POLICY_KEYS:
                        if val is None:
                            all_prefs.pop(key, None)
                        else:
                            all_prefs[key] = val
                    else:
                        if val is None:
                            existing_named.pop(key, None)
                        else:
                            existing_named[key] = val
                users[first_key] = existing_named
                _save(all_prefs)
                res = dict(existing_named)
                for key in _FOREGROUND_POLICY_KEYS:
                    res.pop(key, None)
                    if key in all_prefs:
                        res[key] = all_prefs[key]
                return res
        all_prefs = dict(all_prefs)
        for key, val in updates.items():
            if val is None:
                all_prefs.pop(key, None)
            else:
                all_prefs[key] = val
        _save(all_prefs)
        return dict(all_prefs)

    if not isinstance(all_prefs.get("_users"), dict):
        all_prefs = dict(all_prefs)
        all_prefs["_users"] = {}
    users = all_prefs["_users"]
    user_prefs = users.get(user)
    user_prefs = dict(user_prefs) if isinstance(user_prefs, dict) else {}
    for key, val in updates.items():
        if val is None:
            user_prefs.pop(key, None)
        else:
            user_prefs[key] = val
    users[user] = user_prefs
    _save(all_prefs)
    return dict(user_prefs)


async def _update_off_loop(user: Optional[str], updates: dict) -> dict:
    """Run the whole locked read-modify-write on a worker thread.

    ``_update_for_user`` blocks on a cross-process file lock, which must not
    happen on the event-loop thread. The entire transaction runs in one worker
    call, so the lock still covers the read, merge, and atomic replace.

    A worker thread cannot be interrupted. If the awaiting request is
    cancelled, the request stops waiting but the queued transaction still
    runs to completion exactly once and releases the lock, so the store is
    never left locked or half written. The write may therefore be applied
    even though no response is delivered to the cancelled caller.
    """
    return await run_in_threadpool(_update_for_user, user, updates)


@store_transaction(lambda: PREFS_FILE)
def _mutate_for_user(user: Optional[str], mutator) -> Any:
    """Transactionally mutate preferences for a user under the store lock.

    ``mutator`` receives the user's mutable preference dictionary. Any
    modifications made to the dictionary are persisted to the store.
    If ``mutator`` raises an exception, the transaction is aborted and
    nothing is written.
    """
    all_prefs = _load()
    if user is None:
        users = all_prefs.get("_users")
        if isinstance(users, dict):
            first_key = next(iter(users), None)
            if first_key is not None:
                existing_named = users.get(first_key)
                target = (
                    dict(existing_named)
                    if isinstance(existing_named, dict)
                    else {}
                )
                res = mutator(target)
                for key in _FOREGROUND_POLICY_KEYS:
                    if key in target:
                        all_prefs[key] = target.pop(key)
                users[first_key] = target
                _save(all_prefs)
                return res
        all_prefs = dict(all_prefs)
        res = mutator(all_prefs)
        _save(all_prefs)
        return res

    if not isinstance(all_prefs.get("_users"), dict):
        all_prefs = dict(all_prefs)
        all_prefs["_users"] = {}
    users = all_prefs["_users"]
    user_prefs = users.get(user)
    target = dict(user_prefs) if isinstance(user_prefs, dict) else {}
    res = mutator(target)
    users[user] = target
    _save(all_prefs)
    return res


async def _mutate_off_loop(user: Optional[str], mutator) -> Any:
    """Run a transactional mutation on a worker thread off the event loop."""
    return await run_in_threadpool(_mutate_for_user, user, mutator)


@store_transaction(lambda: PREFS_FILE)
def _save_for_user(user: Optional[str], prefs: dict):
    """Save preferences for a specific user."""
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
        val = body.get("value") if isinstance(body, dict) else None
        prefs = await _update_off_loop(user, {key: val})
        return {"key": key, "value": prefs.get(key)}

    @router.patch("")
    async def set_prefs(request: Request, body: dict):
        """Write several preferences for the caller in one store write.

        Body: ``{"values": {key: value, ...}}``. A ``None`` value removes the
        key, matching ``PUT /api/prefs/{key}``. Related keys such as an
        endpoint and its model are applied together, so a failed request
        cannot leave half of a pair saved.
        """
        values = body.get("values") if isinstance(body, dict) else None
        if not isinstance(values, dict) or not values:
            raise HTTPException(400, "values must be a non-empty object")
        if len(values) > _MAX_PATCH_KEYS:
            raise HTTPException(400, f"at most {_MAX_PATCH_KEYS} preferences per request")
        if not all(isinstance(key, str) and key.strip() for key in values):
            raise HTTPException(400, "preference keys must be non-empty strings")
        user = get_current_user(request)
        prefs = await _update_off_loop(user, values)
        return {"values": {key: prefs.get(key) for key in values}}

    return router
