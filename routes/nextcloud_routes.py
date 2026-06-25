"""Nextcloud Files routes — read-only WebDAV file explorer.

Per-user account storage mirrors CalDAV: credentials live in the user's prefs
(``nextcloud_accounts`` key via ``routes/prefs_routes.py``), with the app
password encrypted at rest through ``src.secret_storage`` and never returned by
any endpoint. Listings and file reads go through :class:`NextcloudClient`.
"""

import asyncio
import logging
import mimetypes
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.auth_helpers import get_current_user, require_user
from src.constants import NEXTCLOUD_MAX_DOWNLOAD_BYTES
from src.nextcloud_client import NextcloudClient, NextcloudError, validate_nextcloud_url

logger = logging.getLogger(__name__)

PREFS_KEY = "nextcloud_accounts"

# In-process link from an editor Document id to the Nextcloud file it was opened
# from: {doc_id: (account_id, path)}. When such a document is saved, the save is
# mirrored back to Nextcloud via WebDAV PUT (see writeback_doc_if_linked). The
# map is ephemeral — it clears on server restart, so a doc opened in a previous
# process just stops auto-syncing until it is re-opened from the Nextcloud tab.
# (Avoids a schema migration to persist provenance on the Document itself.)
_NC_DOC_LINKS: dict = {}


async def writeback_doc_if_linked(doc_id: str, content: str, owner: Optional[str]) -> Optional[dict]:
    """Mirror a document save back to Nextcloud if the doc is linked.

    Returns None when the doc isn't a Nextcloud-sourced doc (no-op for normal
    documents), otherwise an outcome dict ``{"ok": bool, ...}``. Best-effort:
    never raises — callers invoke it after a successful local save.
    """
    link = _NC_DOC_LINKS.get(doc_id)
    if not link:
        return None
    account_id, path = link
    try:
        account = _find_account(owner, account_id)
        client = _client_for(account)
    except HTTPException as e:
        return {"ok": False, "error": f"account: {e.detail}"}
    try:
        await asyncio.to_thread(client.put_file, path, content.encode("utf-8"))
        return {"ok": True, "path": path}
    except NextcloudError as e:
        return {"ok": False, "error": str(e)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}



async def _require_owner(request: Request) -> Optional[str]:
    """Gate the route on auth, then resolve the prefs owner.

    ``require_user`` raises 401 when auth is on and no one is logged in. For
    prefs we pass the raw current user (``None`` in single-user mode) so
    ``_load_for_user`` reads the shared first-user slot, exactly like CalDAV.
    """
    require_user(request)
    return get_current_user(request)


def _load_accounts(owner: Optional[str]) -> List[dict]:
    from routes.prefs_routes import _load_for_user

    prefs = _load_for_user(owner) or {}
    return list(prefs.get(PREFS_KEY) or [])


def _save_accounts(owner: Optional[str], accounts: List[dict]) -> None:
    from routes.prefs_routes import _load_for_user, _save_for_user

    prefs = _load_for_user(owner) or {}
    prefs[PREFS_KEY] = accounts
    _save_for_user(owner, prefs)


def _redact(account: dict) -> dict:
    """Public view of an account — never includes the app password."""
    return {
        "id": account.get("id"),
        "label": account.get("label", "") or "",
        "base_url": account.get("base_url", "") or "",
        "username": account.get("username", "") or "",
        "configured": bool(account.get("password")),
    }


def _find_account(owner: Optional[str], account_id: str) -> dict:
    for acc in _load_accounts(owner):
        if acc.get("id") == account_id:
            return acc
    raise HTTPException(404, "Nextcloud account not found")


def _client_for(account: dict) -> NextcloudClient:
    # secret_storage is imported lazily: importing it at module top creates a
    # circular import at app startup (matches contacts_routes / caldav_sync).
    from src.secret_storage import decrypt

    try:
        return NextcloudClient(
            account.get("base_url", ""),
            account.get("username", ""),
            decrypt(account.get("password") or ""),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


def _map_nextcloud_error(e: NextcloudError) -> HTTPException:
    status = e.status or 502
    if status in (401, 403):
        return HTTPException(status, str(e))
    if status == 404:
        return HTTPException(404, str(e))
    if status == 413:
        return HTTPException(413, str(e))
    return HTTPException(502, str(e))


def setup_nextcloud_routes() -> APIRouter:
    router = APIRouter(prefix="/api/nextcloud", tags=["nextcloud"])

    # ── Accounts ──

    @router.get("/accounts")
    async def list_accounts(owner: Optional[str] = Depends(_require_owner)):
        return {"accounts": [_redact(a) for a in _load_accounts(owner)]}

    @router.post("/accounts")
    async def create_account(data: dict, owner: Optional[str] = Depends(_require_owner)):
        base_url = (data.get("base_url") or "").strip()
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()
        label = (data.get("label") or "").strip()
        if not (base_url and username and password):
            raise HTTPException(400, "base_url, username, and password are required")
        try:
            base_url = validate_nextcloud_url(base_url)
        except ValueError as e:
            raise HTTPException(400, str(e))
        from src.secret_storage import encrypt

        account = {
            "id": str(uuid.uuid4()),
            "label": label or username,
            "base_url": base_url,
            "username": username,
            "password": encrypt(password),
        }
        accounts = _load_accounts(owner)
        accounts.append(account)
        _save_accounts(owner, accounts)
        return _redact(account)

    @router.put("/accounts/{account_id}")
    async def update_account(account_id: str, data: dict, owner: Optional[str] = Depends(_require_owner)):
        account = _find_account(owner, account_id)
        if "base_url" in data and data["base_url"] is not None:
            try:
                account["base_url"] = validate_nextcloud_url((data["base_url"] or "").strip())
            except ValueError as e:
                raise HTTPException(400, str(e))
        if data.get("username"):
            account["username"] = data["username"].strip()
        if data.get("label"):
            account["label"] = data["label"].strip()
        new_pw = (data.get("password") or "").strip()
        if new_pw:
            from src.secret_storage import encrypt

            account["password"] = encrypt(new_pw)
        # Persist by replacing the matching row.
        accounts = _load_accounts(owner)
        for i, a in enumerate(accounts):
            if a.get("id") == account_id:
                accounts[i] = account
                break
        _save_accounts(owner, accounts)
        return _redact(account)

    @router.delete("/accounts/{account_id}")
    async def delete_account(account_id: str, owner: Optional[str] = Depends(_require_owner)):
        accounts = _load_accounts(owner)
        remaining = [a for a in accounts if a.get("id") != account_id]
        if len(remaining) == len(accounts):
            raise HTTPException(404, "Nextcloud account not found")
        _save_accounts(owner, remaining)
        return {"success": True}

    # ── Browsing ──

    @router.post("/test")
    async def test_connection(data: dict, owner: Optional[str] = Depends(_require_owner)):
        """Verify credentials without saving.

        Accepts an ``account_id`` (test stored creds, owner-scoped), or inline
        ``base_url`` + ``username`` + ``password`` for an unsaved account. When
        ``account_id`` and ``password`` are both given, the inline password is
        tested against the account's stored URL/username — the CalDAV-style
        "test a newly typed password before saving" case.
        """
        from src.secret_storage import decrypt

        account_id = (data.get("account_id") or "").strip()
        password = (data.get("password") or "").strip()
        base_url = (data.get("base_url") or "").strip()
        username = (data.get("username") or "").strip()
        if account_id:
            account = _find_account(owner, account_id)  # 404 if it isn't this owner's
            base_url = base_url or (account.get("base_url") or "")
            username = username or (account.get("username") or "")
            if not password:
                password = decrypt(account.get("password") or "")
        if not (base_url and username and password):
            raise HTTPException(400, "Provide an account_id, or base_url + username + password")
        try:
            client = NextcloudClient(base_url, username, password)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        try:
            await asyncio.to_thread(client.ping)
        except NextcloudError as e:
            return {"ok": False, "error": str(e)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    @router.post("/register")
    async def register_doc_link(data: dict, owner: Optional[str] = Depends(_require_owner)):
        """Link an editor Document to the Nextcloud file it was opened from.

        After this, saves to that document are mirrored back to Nextcloud. The
        account must belong to ``owner`` (validated) so a user can't direct
        writebacks at another user's account.
        """
        doc_id = (data.get("doc_id") or "").strip()
        account_id = (data.get("account") or "").strip()
        path = (data.get("path") or "").strip()
        if not (doc_id and account_id and path):
            raise HTTPException(400, "doc_id, account, and path are required")
        _find_account(owner, account_id)  # owner-scope check (404 if not theirs)
        _NC_DOC_LINKS[doc_id] = (account_id, path)
        return {"ok": True}

    @router.put("/file")
    async def put_file(
        request: Request,
        account: str = Query(...),
        path: str = Query(...),
        owner: Optional[str] = Depends(_require_owner),
    ):
        """Write a file's body back to Nextcloud (direct WebDAV PUT)."""
        raw = await request.body()
        client = _client_for(_find_account(owner, account))
        try:
            await asyncio.to_thread(client.put_file, path, raw)
        except NextcloudError as e:
            raise _map_nextcloud_error(e)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "path": path}

    @router.get("/list")
    async def list_path(
        request: Request,
        account: str = Query(..., description="Account id"),
        path: str = Query("", description="Path relative to the user's Nextcloud home"),
        owner: Optional[str] = Depends(_require_owner),
    ):
        client = _client_for(_find_account(owner, account))
        try:
            entries = await asyncio.to_thread(client.list_dir, path)
        except NextcloudError as e:
            raise _map_nextcloud_error(e)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"account": account, "path": (path or "").strip("/"), "entries": entries}

    @router.get("/stat")
    async def stat_path(
        account: str = Query(...),
        path: str = Query(""),
        owner: Optional[str] = Depends(_require_owner),
    ):
        client = _client_for(_find_account(owner, account))
        try:
            entry = await asyncio.to_thread(client.stat, path)
        except NextcloudError as e:
            raise _map_nextcloud_error(e)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"account": account, "entry": entry}

    @router.get("/file")
    async def get_file(
        account: str = Query(...),
        path: str = Query(...),
        owner: Optional[str] = Depends(_require_owner),
    ):
        client = _client_for(_find_account(owner, account))
        try:
            content, content_type = await asyncio.to_thread(
                client.get_file, path, NEXTCLOUD_MAX_DOWNLOAD_BYTES
            )
        except NextcloudError as e:
            raise _map_nextcloud_error(e)
        except ValueError as e:
            raise HTTPException(400, str(e))
        name = (path or "").rsplit("/", 1)[-1] or "download"
        if not content_type:
            guessed, _ = mimetypes.guess_type(name)
            content_type = guessed or "application/octet-stream"
        return Response(
            content=content,
            media_type=content_type,
            headers={"Content-Disposition": f'inline; filename="{name}"'},
        )

    return router
