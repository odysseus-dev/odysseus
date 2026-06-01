"""Microsoft Graph mail backend.

Microsoft 365 / Outlook accounts authenticate with OAuth2 and are accessed
through the Graph REST API rather than IMAP/SMTP — Microsoft has disabled Basic
Auth for Exchange Online and some tenants block IMAP entirely. This module is
the Graph counterpart to the IMAP code in `routes/email_helpers.py`: it returns
the *same dict shapes* the IMAP sync helpers do, so route handlers and the
frontend stay unchanged.

The OAuth token plumbing (refresh, expiry) lives in `routes/email_helpers.py`
(`_get_valid_microsoft_token` / `_refresh_microsoft_token`); this module only
consumes a valid access token.

Selected by `_get_email_config(...)["oauth_provider"] == "microsoft"`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parseaddr, formataddr

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# App folder name (as the IMAP side names them) → Graph well-known folder id.
# Anything not listed is resolved by displayName via /mailFolders at call time.
_WELL_KNOWN = {
    "inbox": "inbox",
    "sent": "sentitems",
    "sent items": "sentitems",
    "sent mail": "sentitems",
    "drafts": "drafts",
    "draft": "drafts",
    "trash": "deleteditems",
    "bin": "deleteditems",
    "deleted": "deleteditems",
    "deleted items": "deleteditems",
    "spam": "junkemail",
    "junk": "junkemail",
    "junk email": "junkemail",
    "archive": "archive",
}

# The friendly folder names we surface to the UI (parallel to IMAP's folder list).
_WELL_KNOWN_DISPLAY = ["INBOX", "Sent", "Drafts", "Archive", "Spam", "Trash"]


class GraphError(Exception):
    """Raised for non-recoverable Graph API failures (after a refresh retry)."""


class GraphClient:
    """Thin authenticated Graph HTTP client.

    Injects the bearer token, and on a 401 forces one token refresh + retry —
    covering tokens revoked or invalidated before their stored expiry.
    """

    def __init__(self, account_id: str, owner: str = ""):
        self.account_id = account_id
        self.owner = owner

    def _config(self) -> dict:
        from routes.email_helpers import _get_email_config
        return _get_email_config(self.account_id, owner=self.owner)

    def _token(self, force_refresh: bool = False) -> str:
        from routes.email_helpers import _get_valid_microsoft_token, _refresh_microsoft_token
        cfg = self._config()
        acct = cfg.get("account_id") or self.account_id
        token = (
            _refresh_microsoft_token(acct) if force_refresh
            else _get_valid_microsoft_token(acct, cfg)
        )
        if not token:
            raise GraphError("Microsoft OAuth token unavailable — reconnect the account in Settings → Integrations")
        return token

    def request(self, method: str, path: str, *, params: dict | None = None,
                json: dict | None = None, data: str | None = None,
                content_type: str | None = None, count: bool = False,
                raw_bytes: bool = False):
        """Perform a Graph request.

        Returns parsed JSON, or the raw `bytes` when `raw_bytes` (e.g.
        attachment `$value`), or {} for an empty/204 response. `data` +
        `content_type` send a non-JSON body (used for base64 MIME /sendMail).
        """
        import httpx
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"

        def _do(token: str):
            headers = {"Authorization": f"Bearer {token}"}
            if count:  # $count / advanced query needs eventual consistency.
                headers["ConsistencyLevel"] = "eventual"
            if content_type:
                headers["Content-Type"] = content_type
            return httpx.request(method, url, headers=headers, params=params,
                                 json=json, content=data, timeout=60)

        resp = _do(self._token())
        if resp.status_code == 401:
            resp = _do(self._token(force_refresh=True))
        if resp.status_code >= 400:
            detail = resp.text[:300]
            logger.warning(f"Graph {method} {url} -> {resp.status_code}: {detail}")
            raise GraphError(f"Graph API error {resp.status_code}")
        if raw_bytes:
            return resp.content
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()


def _addr_pair(recipient: dict | None) -> tuple[str, str]:
    """(name, address) from a Graph emailAddress wrapper."""
    if not recipient:
        return ("", "")
    ea = recipient.get("emailAddress") or {}
    return (ea.get("name") or "", ea.get("address") or "")


def _join_addrs(recipients: list | None) -> str:
    out = []
    for r in recipients or []:
        name, addr = _addr_pair(r)
        if addr:
            out.append(formataddr((name, addr)) if name else addr)
    return ", ".join(out)


def _parse_dt(iso: str) -> tuple[str, str, float]:
    """Graph receivedDateTime (ISO-8601 Z) → (iso, display, epoch)."""
    if not iso:
        return ("", "", 0.0)
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt.isoformat(), dt.strftime("%a, %d %b %Y %H:%M"), dt.timestamp())
    except Exception:
        return (iso, iso, 0.0)


# Fields needed to render a list row — keep the $select tight for speed.
_LIST_SELECT = (
    "id,internetMessageId,subject,from,toRecipients,ccRecipients,"
    "receivedDateTime,isRead,hasAttachments,flag"
)
_READ_SELECT = (
    "id,internetMessageId,subject,from,toRecipients,ccRecipients,"
    "receivedDateTime,isRead,hasAttachments,flag,body"
)


class GraphBackend:
    """Graph-backed mail operations mirroring the IMAP sync helpers' output."""

    def __init__(self, cfg: dict, owner: str = ""):
        self.cfg = cfg
        self.account_id = cfg.get("account_id")
        self.client = GraphClient(self.account_id, owner=owner)

    # ── folder resolution ──
    def _folder_id(self, folder: str) -> str:
        key = (folder or "INBOX").strip().lower()
        if key in _WELL_KNOWN:
            return _WELL_KNOWN[key]
        if not folder or key == "inbox":
            return "inbox"
        # Resolve a custom folder by display name.
        try:
            data = self.client.request("GET", "/me/mailFolders",
                                       params={"$top": "100", "$select": "id,displayName"})
            for f in data.get("value", []):
                if (f.get("displayName") or "").strip().lower() == key:
                    return f["id"]
        except GraphError:
            pass
        return "inbox"

    def list_folders(self) -> dict:
        """Return display names for navigation (well-known + any custom ones)."""
        names = list(_WELL_KNOWN_DISPLAY)
        try:
            data = self.client.request("GET", "/me/mailFolders",
                                       params={"$top": "100", "$select": "id,displayName"})
            for f in data.get("value", []):
                dn = (f.get("displayName") or "").strip()
                if dn and dn not in names and dn.lower() not in _WELL_KNOWN:
                    names.append(dn)
        except GraphError as e:
            return {"folders": names, "error": str(e)}
        return {"folders": names}

    # ── message → app dict ──
    def _to_list_dict(self, m: dict) -> dict:
        fname, faddr = _addr_pair(m.get("from"))
        iso, disp, epoch = _parse_dt(m.get("receivedDateTime", ""))
        flag = (m.get("flag") or {}).get("flagStatus")
        return {
            "uid": m.get("id", ""),
            "message_id": (m.get("internetMessageId") or m.get("id") or "").strip(),
            "subject": m.get("subject") or "(no subject)",
            "from_name": fname or faddr,
            "from_address": faddr,
            "to": _join_addrs(m.get("toRecipients")),
            "cc": _join_addrs(m.get("ccRecipients")),
            "date": iso,
            "date_display": disp,
            "date_epoch": epoch,
            "size": 0,  # Graph list payload omits size; not shown in the row.
            "is_read": bool(m.get("isRead")),
            "is_answered": False,
            "is_flagged": flag == "flagged",
            "flags": [],
            "has_attachments": bool(m.get("hasAttachments")),
            "tags": [],
            "is_spam_verdict": False,
        }

    def list_emails(self, folder: str, limit: int, offset: int, filter_: str,
                    from_addr: str | None = None,
                    has_attachments_only: bool = False) -> dict:
        fid = self._folder_id(folder)
        params = {
            "$top": str(int(limit)),
            "$skip": str(int(offset)),
            "$count": "true",
            "$select": _LIST_SELECT,
            "$orderby": "receivedDateTime desc",
        }
        filters = []
        if filter_ == "unread":
            filters.append("isRead eq false")
        if has_attachments_only:
            filters.append("hasAttachments eq true")
        if from_addr:
            esc = from_addr.replace("'", "''")
            filters.append(f"from/emailAddress/address eq '{esc}'")
        if filters:
            params["$filter"] = " and ".join(filters)
        try:
            data = self.client.request(
                "GET", f"/me/mailFolders/{fid}/messages", params=params, count=True)
        except GraphError as e:
            return {"emails": [], "total": 0, "folder": folder, "error": str(e)}
        emails = [self._to_list_dict(m) for m in data.get("value", [])]
        total = int(data.get("@odata.count", len(emails) + offset))
        return {"emails": emails, "total": total, "folder": folder, "offset": offset}

    def read_email(self, uid: str, folder: str, mark_seen: bool = True) -> dict:
        try:
            m = self.client.request("GET", f"/me/messages/{uid}",
                                    params={"$select": _READ_SELECT})
        except GraphError as e:
            return {"error": str(e)}
        fname, faddr = _addr_pair(m.get("from"))
        iso, _disp, _epoch = _parse_dt(m.get("receivedDateTime", ""))
        body = m.get("body") or {}
        if (body.get("contentType") or "").lower() == "html":
            body_html = body.get("content") or ""
            body_text = ""
        else:
            body_html = ""
            body_text = body.get("content") or ""
        attachments = self._list_attachments(uid) if m.get("hasAttachments") else []
        if mark_seen and not m.get("isRead"):
            try:
                self.set_read(uid, True)
            except GraphError:
                pass
        return {
            "uid": m.get("id", uid),
            "message_id": (m.get("internetMessageId") or m.get("id") or "").strip(),
            "subject": m.get("subject") or "(no subject)",
            "from_name": fname or faddr,
            "from_address": faddr,
            "to": _join_addrs(m.get("toRecipients")),
            "cc": _join_addrs(m.get("ccRecipients")),
            "date": iso,
            "in_reply_to": "",
            "references": "",
            "body": body_text,
            "body_html": body_html,
            "attachments": attachments,
            "cached_summary": None,
            "cached_ai_reply": None,
            "boundaries": None,
            "thread_turns": None,
            "sender_signature": None,
        }

    def _list_attachments(self, uid: str) -> list:
        try:
            data = self.client.request(
                "GET", f"/me/messages/{uid}/attachments",
                params={"$select": "id,name,contentType,size,isInline"})
        except GraphError:
            return []
        out = []
        for idx, att in enumerate(data.get("value", [])):
            out.append({
                "index": idx,
                "filename": att.get("name") or f"attachment_{idx}",
                "content_type": att.get("contentType") or "application/octet-stream",
                "size": int(att.get("size") or 0),
                "is_inline": bool(att.get("isInline")),
            })
        return out

    def set_read(self, uid: str, read: bool) -> bool:
        self.client.request("PATCH", f"/me/messages/{uid}", json={"isRead": bool(read)})
        return True

    # ── write ops ──
    def send_mime(self, raw: bytes) -> None:
        """Send a fully-built RFC822 MIME message via Graph.

        Graph's /sendMail accepts a base64 MIME body with a text/plain
        content type, so we reuse the existing MIME construction (attachments,
        plain+HTML alternatives, headers) untouched. Graph saves to Sent
        Items itself, so callers skip the IMAP Sent-append.
        """
        import base64
        b64 = base64.b64encode(raw).decode()
        self.client.request("POST", "/me/sendMail", data=b64, content_type="text/plain")

    def move(self, uid: str, dest_folder: str) -> bool:
        """Move a message to a destination folder (by app folder name)."""
        dest_id = self._folder_id(dest_folder)
        self.client.request("POST", f"/me/messages/{uid}/move",
                            json={"destinationId": dest_id})
        return True

    def delete(self, uid: str, permanent: bool = False) -> bool:
        """Trash a message (move to Deleted Items) or hard-delete it."""
        if permanent:
            self.client.request("DELETE", f"/me/messages/{uid}")
        else:
            self.client.request("POST", f"/me/messages/{uid}/move",
                                json={"destinationId": "deleteditems"})
        return True

    def get_attachment(self, uid: str, index: int) -> tuple[str, str, bytes]:
        """Return (filename, content_type, bytes) for the Nth attachment.

        Indexed to match `_list_attachments` ordering. Uses `$value` so large
        files stream as raw bytes rather than base64-in-JSON.
        """
        import base64
        data = self.client.request(
            "GET", f"/me/messages/{uid}/attachments",
            params={"$select": "id,name,contentType,size,isInline,@odata.type"})
        items = data.get("value", [])
        if index < 0 or index >= len(items):
            raise GraphError(f"Attachment index {index} not found")
        att = items[index]
        name = att.get("name") or f"attachment_{index}"
        ctype = att.get("contentType") or "application/octet-stream"
        # fileAttachment carries inline base64 `contentBytes`; fetch the full
        # item (the list $select may omit it) and decode.
        full = self.client.request("GET", f"/me/messages/{uid}/attachments/{att['id']}")
        raw = base64.b64decode(full.get("contentBytes", "")) if full.get("contentBytes") else b""
        return (name, ctype, raw)
