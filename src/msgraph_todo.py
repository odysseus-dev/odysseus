"""Two-way task sync with Microsoft To Do via Microsoft Graph.

Odysseus keeps its to-dos in the Notes module: a `Note` with
`note_type="todo"` holds a title, a body, a due date, a repeat rule and a
checklist of `{text, done}` items. Microsoft To Do keeps a `todoTask` with a
title, a body, `dueDateTime`, a recurrence and a collection of
`checklistItems`. The two line up almost field for field, so this module maps
them directly rather than inventing a third model:

    To Do list        ↔  the note's label (a tag in the Notes tag bar)
    todoTask          ↔  a note with note_type="todo"
    checklistItems    ↔  the note's items[]
    dueDateTime       ↔  due_date (date only)
    reminderDateTime  ↔  due_date (when it carries a time)
    recurrence        ↔  repeat
    status completed  ↔  archived
    importance high   ↔  pinned

`archived` is the completion flag because that is what "done" already means
for a note: it leaves the grid and lands in the Archive. Nothing new had to
be added to the notes UI for a task to be tickable from a phone.

Scope of the sync
-----------------
Only `note_type` in `SYNCED_NOTE_TYPES` is pushed. A plain note, a drawing or
a goal stays local — writing every scratch note into someone's task list
would be a surprise, and none of those shapes survives the round trip
intact. Tasks pulled from To Do always land as `todo` notes.

Auth
----
Its own OAuth connection, separate from mail and calendar. Microsoft issues
access tokens per resource, so `Tasks.ReadWrite` is consented on its own
against the same app registration, with its own callback. Same rotation rule
as everywhere else: the refresh token that comes back replaces the one we
spent, or the connection dies at the next expiry.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

MSGRAPH_TODO_SCOPES = (
    "openid email offline_access "
    "https://graph.microsoft.com/Tasks.ReadWrite"
)

# Note types that represent a task. Everything else is local-only.
SYNCED_NOTE_TYPES = ("todo", "checklist")

_HTTP_TIMEOUT = 20
_MAX_PAGES = 25
_PAGE_SIZE = 100
# A list keeps its completed tasks forever. Pulling all of them would grow
# the local Archive without bound, so old ones are left upstream.
_COMPLETED_LOOKBACK_DAYS = 30

_TODO_NAMESPACE = uuid.UUID("2b7f1c04-5a3e-4f8d-9c61-7d0e4a9b2f13")


# ── Account storage ───────────────────────────────────────────────

def _prefs_owner(owner):
    """The preferences key for *owner*.

    Two owner conventions reach this module. The notes routes normalize an
    anonymous caller to ODYSSEUS_FALLBACK_OWNER so rows have a stable owner to
    filter on, while the OAuth routes see the bare "" that `require_user`
    returns when auth is disabled or unconfigured. Preferences have a third
    convention for that same single-user case: None, meaning the flat/first
    record.

    All three are the same person, so map them onto the one key prefs uses.
    Without this an account connected through the OAuth route lands under ""
    and the sync, asking for "owner@localhost", finds nothing — and reports
    no error, because "no account connected" is not a failure. That is a
    silent no-op, which is the worst shape this bug can take.
    """
    name = (owner or "").strip()
    fallback = os.environ.get("ODYSSEUS_FALLBACK_OWNER", "owner@localhost")
    if not name or name == fallback:
        return None
    return name


def _load_mstodo_accounts(owner: str) -> list:
    """Return the Microsoft To Do accounts configured for *owner*."""
    from routes.prefs_routes import _load_for_user

    prefs = _load_for_user(_prefs_owner(owner)) or {}
    return list(prefs.get("mstodo_accounts") or [])


def _save_mstodo_accounts(owner: str, accounts: list) -> None:
    from routes.prefs_routes import _load_for_user, _save_for_user

    key = _prefs_owner(owner)
    prefs = _load_for_user(key) or {}
    prefs["mstodo_accounts"] = accounts
    _save_for_user(key, prefs)


def _find_account(owner: str, account_id: str) -> dict | None:
    for acc in _load_mstodo_accounts(owner):
        if acc.get("id") == account_id:
            return acc
    return None


def _stable_note_id(remote_id: str, owner: str = "", account_id: str = "") -> str:
    """Deterministic local primary key for a remote To Do task.

    Scoped by owner and account so re-pulling is idempotent and two users —
    or one user with two connected mailboxes — never collide on a shared id.
    """
    return str(uuid.uuid5(_TODO_NAMESPACE, f"{owner}|{account_id}|{remote_id}"))


# ── OAuth ─────────────────────────────────────────────────────────

def _oauth_client() -> tuple[str, str]:
    return (
        os.environ.get("MICROSOFT_OAUTH_CLIENT_ID", "").strip(),
        os.environ.get("MICROSOFT_OAUTH_CLIENT_SECRET", "").strip(),
    )


def _token_url() -> str:
    from routes.email_helpers import microsoft_oauth_token_url

    return microsoft_oauth_token_url()


def _authorize_url() -> str:
    from routes.email_helpers import microsoft_oauth_authorize_url

    return microsoft_oauth_authorize_url()


def _refresh_access_token(owner: str, account_id: str) -> str | None:
    """Redeem the stored refresh token and persist the rotated one."""
    import httpx
    from src.secret_storage import decrypt, encrypt

    client_id, client_secret = _oauth_client()
    if not (client_id and client_secret):
        return None

    accounts = _load_mstodo_accounts(owner)
    idx = next((i for i, a in enumerate(accounts) if a.get("id") == account_id), None)
    if idx is None:
        return None
    acc = dict(accounts[idx])
    try:
        refresh_token = decrypt(acc.get("refresh_token") or "")
    except Exception:
        refresh_token = ""
    if not refresh_token:
        return None

    try:
        resp = httpx.post(
            _token_url(),
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": MSGRAPH_TODO_SCOPES,
            },
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        # Never log the response body: it echoes tokens on some errors.
        logger.warning("Microsoft To Do token refresh failed for account=%s: %s",
                       account_id, type(e).__name__)
        return None

    access_token = data.get("access_token") or ""
    if not access_token:
        return None
    acc["access_token"] = encrypt(access_token)
    if data.get("refresh_token"):
        acc["refresh_token"] = encrypt(data["refresh_token"])
    acc["token_expiry"] = str(int(time.time()) + int(data.get("expires_in") or 3600))
    accounts[idx] = acc
    _save_mstodo_accounts(owner, accounts)
    return access_token


def _valid_access_token(owner: str, account_id: str) -> str | None:
    """A non-expired access token, refreshing when it is close to expiry."""
    from src.secret_storage import decrypt

    acc = _find_account(owner, account_id)
    if not acc:
        return None
    try:
        expiry = int(acc.get("token_expiry") or 0)
    except (TypeError, ValueError):
        expiry = 0
    if expiry - 120 > time.time():
        try:
            token = decrypt(acc.get("access_token") or "")
        except Exception:
            token = ""
        if token:
            return token
    return _refresh_access_token(owner, account_id)


# ── Graph HTTP ────────────────────────────────────────────────────

class GraphError(RuntimeError):
    """A Graph call that came back non-2xx."""

    def __init__(self, status: int, message: str):
        super().__init__(f"Graph {status}: {message}")
        self.status = status
        self.message = message


def _graph_request(token: str, method: str, path: str, *, params=None, json_body=None):
    """Call Graph and return the decoded body ({} for 204).

    `path` is appended to the fixed GRAPH_BASE, so a URL echoed back by the
    server can never redirect a call — with our bearer token — to another
    host.
    """
    import httpx

    url = f"{GRAPH_BASE}{path}" if path.startswith("/") else path
    if not url.startswith(GRAPH_BASE):
        raise GraphError(0, "refusing to call a non-Graph URL")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # Render every date/time in UTC so a naive value never has to be guessed.
        "Prefer": 'outlook.timezone="UTC"',
    }
    resp = httpx.request(
        method, url, headers=headers, params=params, json=json_body,
        timeout=_HTTP_TIMEOUT, follow_redirects=False,
    )
    if resp.status_code == 204 or not (resp.content or b"").strip():
        if resp.status_code >= 400:
            raise GraphError(resp.status_code, resp.reason_phrase or "request failed")
        return {}
    try:
        body = resp.json()
    except Exception:
        if resp.status_code >= 400:
            raise GraphError(resp.status_code, "request failed")
        return {}
    if resp.status_code >= 400:
        detail = ""
        if isinstance(body, dict):
            detail = str((body.get("error") or {}).get("message") or "")[:200]
        raise GraphError(resp.status_code, detail or "request failed")
    return body


def _graph_paged(token: str, path: str, *, params=None) -> tuple[list, bool]:
    """Walk @odata.nextLink and return (items, truncated).

    `truncated` matters more than the items: a caller that prunes what it did
    not see must not prune after a walk that stopped at the page cap.
    """
    items: list = []
    body = _graph_request(token, "GET", path, params=params)
    for _ in range(_MAX_PAGES):
        items.extend(body.get("value") or [])
        next_link = body.get("@odata.nextLink")
        if not next_link:
            return items, False
        body = _graph_request(token, "GET", next_link)
    logger.warning("Graph pagination stopped at the %d-page cap for %s", _MAX_PAGES, path)
    return items, True


# ── Lists ↔ note labels ───────────────────────────────────────────

def label_for_list(display_name: str) -> str:
    """Turn a To Do list name into a single note tag.

    Note labels are a space-separated tag string, so a list called "Work
    Stuff" has to collapse to one token or it would read back as two tags.
    """
    name = (display_name or "").strip()
    name = name.replace("#", "")
    name = re.sub(r"\s+", "-", name)
    return name.strip("-")


def _list_index(lists: list) -> tuple[dict, str]:
    """({tag: list_id}, default_list_id) for the connected account."""
    by_tag: dict = {}
    default_id = ""
    for lst in lists or []:
        list_id = str(lst.get("id") or "")
        if not list_id:
            continue
        if (lst.get("wellknownListName") or "") == "defaultList" and not default_id:
            default_id = list_id
        tag = label_for_list(lst.get("displayName") or "")
        if tag:
            by_tag.setdefault(tag.lower(), list_id)
    if not default_id and lists:
        default_id = str((lists[0] or {}).get("id") or "")
    return by_tag, default_id


def note_tags(label: str) -> list:
    """The individual tags stored in a note's space-separated label."""
    return [t for t in re.split(r"\s+", (label or "").strip()) if t]


def merge_label(existing_label: str, list_tag: str, list_tags: set) -> str | None:
    """The label a synced note should carry after a pull.

    The list tag is authoritative for which list the task is in, but a tag
    the user added here ("urgent", "home") is theirs — replacing the whole
    label with the list name would silently eat it at every sync. So: drop
    any tag that names a *different* list, keep everything else, and make
    sure this list's tag is present.
    """
    lowered = {t.lower() for t in list_tags}
    kept = [
        t for t in note_tags(existing_label)
        if t.lower() not in lowered or (list_tag and t.lower() == list_tag.lower())
    ]
    if list_tag and list_tag.lower() not in {t.lower() for t in kept}:
        kept.insert(0, list_tag)
    return " ".join(kept) or None


def choose_list_for_note(label: str, lists: list) -> str:
    """The To Do list a locally-created note belongs in.

    First tag that names an existing list wins; otherwise the default list.
    A tag is never turned into a new To Do list — creating lists in someone
    else's account off the back of a typo is not a side effect worth having.
    """
    by_tag, default_id = _list_index(lists)
    for tag in note_tags(label):
        hit = by_tag.get(tag.lower())
        if hit:
            return hit
    return default_id


# ── Task conversion ───────────────────────────────────────────────

def _parse_graph_dt(node) -> datetime | None:
    """Parse Graph's {dateTime, timeZone} into a naive UTC datetime."""
    if not isinstance(node, dict):
        return None
    raw = (node.get("dateTime") or "").strip()
    if not raw:
        return None
    # Graph sends 7-digit fractional seconds; datetime tops out at 6.
    if "." in raw:
        head, _, frac = raw.partition(".")
        digits = "".join(c for c in frac if c.isdigit())[:6]
        rest = frac[len(digits):].lstrip("0123456789")
        raw = f"{head}.{digits}{rest}" if digits else head + rest
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    tz_name = (node.get("timeZone") or "").strip().upper()
    if tz_name in {"UTC", "GMT", "GMT STANDARD TIME"} or not tz_name:
        return dt
    try:
        from zoneinfo import ZoneInfo

        return dt.replace(tzinfo=ZoneInfo(node["timeZone"])).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return dt


def _graph_dt(dt: datetime) -> dict:
    """Graph's dateTimeTimeZone shape for a naive UTC datetime."""
    return {"dateTime": dt.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}


def _body_text(task: dict) -> str:
    """Plain-text body, stripping HTML when To Do stored it that way."""
    body = task.get("body") or {}
    content = (body.get("content") or "").strip()
    if not content:
        return ""
    if (body.get("contentType") or "").lower() != "html":
        return content
    try:
        import nh3

        return nh3.clean(content, tags=set()).strip()
    except Exception:
        # No sanitizer available: drop the markup rather than store raw HTML
        # in a field the notes UI renders.
        return re.sub(r"<[^>]+>", "", content).strip()


_REPEAT_FROM_PATTERN = {
    "daily": "daily",
    "weekly": "weekly",
    "absolutemonthly": "monthly",
    "relativemonthly": "monthly",
    "absoluteyearly": "yearly",
    "relativeyearly": "yearly",
}

_GRAPH_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday",
                   "friday", "saturday", "sunday")


def repeat_from_recurrence(recurrence) -> str:
    """A note `repeat` value for a Graph patternedRecurrence."""
    if not isinstance(recurrence, dict):
        return "none"
    pattern_type = str(((recurrence.get("pattern") or {}).get("type") or "")).lower()
    return _REPEAT_FROM_PATTERN.get(pattern_type, "none")


def recurrence_from_repeat(repeat: str, anchor: datetime | None) -> dict | None:
    """A Graph patternedRecurrence for a note `repeat` value.

    Graph anchors every range to a start date, and To Do rejects a recurrence
    on a task with no due date, so an unanchored repeat is dropped rather
    than pushed against an invented date.
    """
    repeat = (repeat or "none").strip().lower()
    if repeat in {"", "none"} or anchor is None:
        return None
    if repeat == "daily":
        pattern = {"type": "daily", "interval": 1}
    elif repeat == "weekly":
        pattern = {"type": "weekly", "interval": 1,
                   "daysOfWeek": [_GRAPH_WEEKDAYS[anchor.weekday()]]}
    elif repeat == "monthly":
        pattern = {"type": "absoluteMonthly", "interval": 1, "dayOfMonth": anchor.day}
    elif repeat == "yearly":
        pattern = {"type": "absoluteYearly", "interval": 1,
                   "dayOfMonth": anchor.day, "month": anchor.month}
    else:
        return None
    return {"pattern": pattern,
            "range": {"type": "noEnd", "startDate": anchor.strftime("%Y-%m-%d")}}


def _due_from_task(task: dict) -> str:
    """The note `due_date` string for a Graph task.

    A reminder carries the time of day; `dueDateTime` alone is a calendar
    day, and the notes UI treats a value with no `T` as exactly that.
    """
    if task.get("isReminderOn"):
        reminder = _parse_graph_dt(task.get("reminderDateTime"))
        if reminder:
            return reminder.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"
    due = _parse_graph_dt(task.get("dueDateTime"))
    if due:
        return due.strftime("%Y-%m-%d")
    return ""


def _parse_due(due_date: str) -> tuple[datetime | None, bool]:
    """(naive UTC datetime, has_time) for a note's stored due_date."""
    raw = (due_date or "").strip()
    if not raw:
        return None, False
    has_time = bool(re.search(r"T\d{2}:\d{2}", raw))
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt, has_time


def checklist_from_task(task: dict) -> list:
    """The note items[] for a task's expanded checklistItems."""
    items = []
    for entry in task.get("checklistItems") or []:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("displayName") or "").strip()
        if not text:
            continue
        items.append({"text": text, "done": bool(entry.get("isChecked"))})
    return items


def graph_task_to_row(task: dict, *, list_tag: str = "") -> dict | None:
    """The Note column values for one Graph todoTask."""
    remote_id = str(task.get("id") or "")
    if not remote_id:
        return None
    items = checklist_from_task(task)
    status = str(task.get("status") or "").lower()
    return {
        "title": str(task.get("title") or "").strip(),
        "content": _body_text(task),
        "items": json.dumps(items) if items else None,
        "note_type": "todo",
        "label": list_tag or None,
        "pinned": str(task.get("importance") or "").lower() == "high",
        "archived": status == "completed",
        "due_date": _due_from_task(task) or None,
        "repeat": repeat_from_recurrence(task.get("recurrence")),
        "remote_id": remote_id,
        "remote_etag": str(task.get("@odata.etag") or "") or None,
    }


def row_to_graph_task(note: dict) -> dict:
    """The Graph body for a local note. Checklist items are sent separately."""
    body: dict = {
        "title": (note.get("title") or "").strip() or "(untitled)",
        "body": {"content": note.get("content") or "", "contentType": "text"},
        "importance": "high" if note.get("pinned") else "normal",
        "status": "completed" if note.get("archived") else "notStarted",
    }
    due, has_time = _parse_due(note.get("due_date") or "")
    if due:
        body["dueDateTime"] = _graph_dt(due.replace(hour=0, minute=0, second=0, microsecond=0))
        if has_time:
            body["reminderDateTime"] = _graph_dt(due)
            body["isReminderOn"] = True
        else:
            body["isReminderOn"] = False
        recurrence = recurrence_from_repeat(note.get("repeat") or "none", due)
        if recurrence:
            body["recurrence"] = recurrence
    else:
        body["isReminderOn"] = False
    return body


def _note_items(note) -> list:
    try:
        parsed = json.loads(note.items) if note.items else []
    except (TypeError, ValueError):
        return []
    return [i for i in parsed if isinstance(i, dict)]


# ── Owner scoping ─────────────────────────────────────────────────

def _scope_owner(query, owner: str):
    """Filter a Note query to *owner*, treating NULL and "" as the same.

    The notes routes store `require_user(request) or None`, so a single-user
    install writes NULL while prefs and the sync entry points may carry "".
    Matching on equality alone would make the whole sync a silent no-op for
    exactly the installs that need it most.
    """
    from core.database import Note
    from sqlalchemy import or_

    if owner:
        return query.filter(Note.owner == owner)
    return query.filter(or_(Note.owner.is_(None), Note.owner == ""))


# ── Pull: Graph → local ───────────────────────────────────────────

def _apply_row(note, row: dict) -> bool:
    """Copy changed fields onto a note. Returns whether anything moved.

    Assigning unconditionally would bump `updated_at` on every note at every
    sync, and the active notes view is ordered by it — a no-op pull would
    reshuffle the user's board.
    """
    changed = False
    for field, value in row.items():
        # The etag moves whenever the server touches the task, including for
        # things we do not mirror. Writing it on its own would bump
        # `updated_at` and reorder the board for no visible reason, so it
        # rides along with a real change instead of counting as one.
        if field == "remote_etag":
            continue
        if getattr(note, field, None) != value:
            setattr(note, field, value)
            changed = True
    if changed and row.get("remote_etag") is not None:
        note.remote_etag = row["remote_etag"]
    return changed


def _sync_account_blocking(owner: str, account_id: str, token: str) -> dict:
    """Pull one connected account's To Do lists into the local notes table."""
    from core.database import Note, SessionLocal
    from sqlalchemy.orm.attributes import flag_modified

    result = {"lists": 0, "tasks": 0, "deleted": 0, "errors": []}

    try:
        lists, _ = _graph_paged(token, "/me/todo/lists", params={"$top": 50})
    except GraphError as e:
        result["errors"].append(f"Could not read the task lists: {e.message}")
        return result

    cutoff = datetime.utcnow() - timedelta(days=_COMPLETED_LOOKBACK_DAYS)
    # Every list's tag, so a pull can tell "this note is tagged with another
    # list" apart from "the user tagged this note themselves".
    all_list_tags = {
        label_for_list(l.get("displayName") or "")
        for l in lists
        if (l.get("wellknownListName") or "") != "defaultList"
    } - {""}
    db = SessionLocal()
    try:
        for remote_list in lists:
            list_id = str(remote_list.get("id") or "")
            if not list_id:
                continue
            display = (remote_list.get("displayName") or "").strip() or "Tasks"
            # The default list is just "Tasks"; tagging every note with it
            # would put a meaningless chip on the whole board.
            is_default = (remote_list.get("wellknownListName") or "") == "defaultList"
            list_tag = "" if is_default else label_for_list(display)
            result["lists"] += 1

            fetch_failed = False
            truncated = False
            try:
                tasks, truncated = _graph_paged(
                    token, f"/me/todo/lists/{list_id}/tasks",
                    params={"$top": _PAGE_SIZE, "$expand": "checklistItems"},
                )
            except GraphError as e:
                result["errors"].append(f"{display}: {e.message}")
                fetch_failed, tasks = True, []

            seen: set = set()
            try:
                for task in tasks:
                    row = graph_task_to_row(task, list_tag=list_tag)
                    if not row:
                        continue
                    remote_id = row["remote_id"]
                    # Seen either way: a long-completed task is still upstream,
                    # so the prune below must not treat it as vanished.
                    seen.add(remote_id)
                    if row["archived"]:
                        touched = _parse_graph_dt(
                            {"dateTime": task.get("lastModifiedDateTime") or ""}
                        )
                        if touched and touched < cutoff:
                            continue

                    note_id = _stable_note_id(remote_id, owner, account_id)
                    existing = _scope_owner(db.query(Note), owner).filter(
                        Note.remote_id == remote_id,
                        Note.todo_account_id == account_id,
                    ).first() or _scope_owner(db.query(Note), owner).filter(
                        Note.id == note_id,
                    ).first()

                    if existing:
                        # A local edit that has not reached Graph yet outranks
                        # the server copy; the push will settle it.
                        if existing.todo_sync_pending in {"create", "update"}:
                            result["tasks"] += 1
                            continue
                        row["label"] = merge_label(existing.label, list_tag, all_list_tags)
                        had_items = existing.items
                        changed = _apply_row(existing, row)
                        if existing.remote_list_id != list_id:
                            existing.remote_list_id, changed = list_id, True
                        if existing.todo_account_id != account_id:
                            existing.todo_account_id, changed = account_id, True
                        if existing.origin != "mstodo":
                            existing.origin, changed = "mstodo", True
                        if changed and had_items != existing.items:
                            flag_modified(existing, "items")
                        if changed:
                            existing.todo_sync_pending = None
                    else:
                        db.add(Note(
                            id=note_id,
                            owner=owner or None,
                            source="user",
                            origin="mstodo",
                            remote_list_id=list_id,
                            todo_account_id=account_id,
                            **row,
                        ))
                    result["tasks"] += 1
                db.commit()
            except Exception as e:
                logger.exception("Microsoft To Do sync failed for one list")
                result["errors"].append(f"{display}: {str(e)[:200]}")
                db.rollback()
                continue

            # Prune what vanished upstream — only rows we pulled from Graph,
            # only for this account and list, never with an unpushed local
            # edit, and never after a failed or truncated walk. An empty list
            # that came back 200 is a real answer, not a failure, so the last
            # task being ticked off a phone still clears the note here.
            if fetch_failed or truncated:
                continue
            try:
                stale_q = _scope_owner(db.query(Note), owner).filter(
                    Note.origin == "mstodo",
                    Note.todo_account_id == account_id,
                    Note.remote_list_id == list_id,
                    Note.remote_id.isnot(None),
                    Note.todo_sync_pending.is_(None),
                )
                if seen:
                    stale_q = stale_q.filter(~Note.remote_id.in_(seen))
                stale = stale_q.all()
                for note in stale:
                    db.delete(note)
                result["deleted"] += len(stale)
                db.commit()
            except Exception as e:
                logger.warning("Microsoft To Do prune failed for list=%s: %s", display, e)
                db.rollback()
    finally:
        db.close()
    return result


async def sync_mstodo(owner: str) -> dict:
    """Pull every connected Microsoft To Do account into the notes table."""
    import asyncio

    accounts = _load_mstodo_accounts(owner)
    if not accounts:
        return {"lists": 0, "tasks": 0, "deleted": 0,
                "errors": ["No Microsoft To Do account is connected"]}

    totals: dict = {"lists": 0, "tasks": 0, "deleted": 0, "errors": []}
    for acc in accounts:
        account_id = acc.get("id") or ""
        label = acc.get("label") or acc.get("email") or account_id
        token = await asyncio.to_thread(_valid_access_token, owner, account_id)
        if not token:
            totals["errors"].append(f"{label}: sign-in expired — reconnect Microsoft To Do")
            continue
        try:
            out = await asyncio.to_thread(_sync_account_blocking, owner, account_id, token)
        except Exception as e:
            logger.warning("Microsoft To Do sync failed for account=%s: %s", account_id, e)
            totals["errors"].append(f"{label}: {str(e)[:160]}")
            continue
        for key in ("lists", "tasks", "deleted"):
            totals[key] += out.get(key, 0)
        totals["errors"].extend(f"{label}: {err}" for err in out.get("errors") or [])
    return totals


# ── Push: local → Graph ───────────────────────────────────────────

def note_should_sync(note) -> bool:
    """Whether a note is a task, and so belongs in Microsoft To Do."""
    return str(getattr(note, "note_type", "") or "").strip().lower() in SYNCED_NOTE_TYPES


def _note_payload(note) -> dict:
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "items": _note_items(note),
        "label": note.label or "",
        "pinned": bool(note.pinned),
        "archived": bool(note.archived),
        "due_date": note.due_date or "",
        "repeat": note.repeat or "none",
        "remote_id": note.remote_id or "",
        "remote_list_id": note.remote_list_id or "",
        "todo_account_id": note.todo_account_id or "",
    }


def _load_note_for_writeback(owner: str, note_id: str) -> dict | None:
    """The payload for a note that should reach To Do, or None."""
    from core.database import Note, SessionLocal

    db = SessionLocal()
    try:
        note = _scope_owner(db.query(Note), owner).filter(Note.id == note_id).first()
        if not note or not note_should_sync(note):
            return None
        return _note_payload(note)
    finally:
        db.close()


def _default_account_id(owner: str) -> str:
    accounts = _load_mstodo_accounts(owner)
    return str((accounts[0] or {}).get("id") or "") if accounts else ""


def _persist_push_result(owner: str, note_id: str, account_id: str,
                         list_id: str, remote: dict) -> None:
    from core.database import Note, SessionLocal

    db = SessionLocal()
    try:
        note = _scope_owner(db.query(Note), owner).filter(Note.id == note_id).first()
        if not note:
            return
        if remote.get("id"):
            note.remote_id = str(remote["id"])
        if remote.get("@odata.etag"):
            note.remote_etag = str(remote["@odata.etag"])
        note.remote_list_id = list_id or note.remote_list_id
        note.todo_account_id = account_id or note.todo_account_id
        note.origin = "mstodo"
        note.todo_sync_pending = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _mark_pending(owner: str, note_id: str, action: str) -> None:
    """Leave a retry marker so the next sync picks the failed push back up."""
    from core.database import Note, SessionLocal

    db = SessionLocal()
    try:
        note = _scope_owner(db.query(Note), owner).filter(Note.id == note_id).first()
        if note:
            note.todo_sync_pending = action
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _sync_checklist_items(token: str, list_id: str, task_id: str, items: list) -> None:
    """Make the task's steps match the note's items.

    Graph does not accept `checklistItems` on a task PATCH — they are their
    own collection — so they are reconciled by position: update what is
    there, append what is missing, delete the surplus.
    """
    base = f"/me/todo/lists/{list_id}/tasks/{task_id}/checklistItems"
    try:
        existing, _ = _graph_paged(token, base, params={"$top": 100})
    except GraphError:
        existing = []

    wanted = []
    for item in items or []:
        text = str(item.get("text") or "").strip()
        if text:
            wanted.append({"displayName": text, "isChecked": bool(item.get("done"))})

    for idx, want in enumerate(wanted):
        if idx < len(existing):
            current = existing[idx] or {}
            same = (
                str(current.get("displayName") or "") == want["displayName"]
                and bool(current.get("isChecked")) == want["isChecked"]
            )
            if same or not current.get("id"):
                continue
            _graph_request(token, "PATCH", f"{base}/{current['id']}", json_body=want)
        else:
            _graph_request(token, "POST", base, json_body=want)

    for current in existing[len(wanted):]:
        if (current or {}).get("id"):
            _graph_request(token, "DELETE", f"{base}/{current['id']}")


def _push_blocking(owner: str, note_id: str, action: str) -> dict:
    payload = _load_note_for_writeback(owner, note_id)
    if not payload:
        return {"ok": True, "skipped": "not a synced task"}

    account_id = payload["todo_account_id"] or _default_account_id(owner)
    if not account_id:
        return {"ok": True, "skipped": "no Microsoft To Do account is connected"}

    token = _valid_access_token(owner, account_id)
    if not token:
        return {"ok": False, "error": "sign-in expired — reconnect Microsoft To Do"}

    remote_id = payload["remote_id"]
    list_id = payload["remote_list_id"]
    try:
        if not list_id:
            # A task cannot move between lists over the API, so the list is
            # picked once — when the note first reaches To Do — and then kept.
            lists, _ = _graph_paged(token, "/me/todo/lists", params={"$top": 50})
            list_id = choose_list_for_note(payload["label"], lists)
        if not list_id:
            return {"ok": False, "error": "the account has no task list to write to"}

        if action == "delete":
            if not remote_id:
                return {"ok": True, "skipped": "never reached Graph"}
            _graph_request(token, "DELETE", f"/me/todo/lists/{list_id}/tasks/{remote_id}")
            return {"ok": True}

        body = row_to_graph_task(payload)
        if action == "update" and remote_id:
            remote = _graph_request(
                token, "PATCH", f"/me/todo/lists/{list_id}/tasks/{remote_id}", json_body=body,
            )
        else:
            remote = _graph_request(
                token, "POST", f"/me/todo/lists/{list_id}/tasks", json_body=body,
            )
        remote = remote or {}
        task_id = str(remote.get("id") or remote_id or "")
        _persist_push_result(owner, note_id, account_id, list_id, remote)
        if task_id:
            _sync_checklist_items(token, list_id, task_id, payload["items"])
        return {"ok": True}
    except GraphError as e:
        # 404 on update/delete: the task is already gone upstream, which is
        # the state we were driving towards anyway.
        if e.status == 404 and action in {"update", "delete"}:
            return {"ok": True, "skipped": "already gone upstream"}
        return {"ok": False, "error": e.message}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _push_delete_blocking(owner: str, note_id: str) -> dict:
    """Delete a task whose note row is already gone.

    The route deletes the note and leaves a tombstone, so unlike
    create/update this cannot read the note back — the tombstone carries the
    remote ids the call needs.
    """
    from core.database import MsTodoDeletedNote, SessionLocal

    db = SessionLocal()
    try:
        tombstone = db.query(MsTodoDeletedNote).filter(
            MsTodoDeletedNote.id == note_id,
        ).first()
        if not tombstone:
            return {"ok": True, "skipped": "no tombstone"}
        if owner and (tombstone.owner or "") != owner:
            return {"ok": True, "skipped": "no tombstone"}
        remote_id = tombstone.remote_id or ""
        list_id = tombstone.remote_list_id or ""
        account_id = tombstone.account_id or ""
    finally:
        db.close()

    if not remote_id or not list_id:
        return {"ok": True, "skipped": "never reached Graph"}
    account_id = account_id or _default_account_id(owner)
    if not account_id:
        return {"ok": True, "skipped": "no Microsoft To Do account is connected"}

    token = _valid_access_token(owner, account_id)
    if not token:
        return {"ok": False, "error": "sign-in expired — reconnect Microsoft To Do"}
    try:
        _graph_request(token, "DELETE", f"/me/todo/lists/{list_id}/tasks/{remote_id}")
        return {"ok": True}
    except GraphError as e:
        if e.status == 404:
            return {"ok": True, "skipped": "already gone upstream"}
        return {"ok": False, "error": e.message}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _clear_tombstone(owner: str, note_id: str) -> None:
    from core.database import MsTodoDeletedNote, SessionLocal

    db = SessionLocal()
    try:
        db.query(MsTodoDeletedNote).filter(MsTodoDeletedNote.id == note_id).delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def push_note_blocking(owner: str, note_id: str, action: str) -> dict:
    """Write one local change back to To Do. Safe to hand to BackgroundTasks.

    Local SQLite stays authoritative: a failed push only leaves a retry
    marker, so the next sync tries again instead of losing the edit.
    """
    try:
        if action == "delete":
            out = _push_delete_blocking(owner, note_id)
            if out.get("ok"):
                _clear_tombstone(owner, note_id)
        else:
            out = _push_blocking(owner, note_id, action)
    except Exception as e:
        logger.warning("Microsoft To Do %s push failed for note=%s: %s", action, note_id, e)
        out = {"ok": False, "error": str(e)[:200]}
    if not out.get("ok") and not out.get("skipped") and action in {"create", "update"}:
        _mark_pending(owner, note_id, action)
    return out


async def _push(owner: str, note_id: str, action: str) -> dict:
    import asyncio

    return await asyncio.to_thread(push_note_blocking, owner, note_id, action)


async def push_task_create(owner: str, note_id: str) -> dict:
    return await _push(owner, note_id, "create")


async def push_task_update(owner: str, note_id: str) -> dict:
    return await _push(owner, note_id, "update")


async def push_task_delete(owner: str, note_id: str) -> dict:
    return await _push(owner, note_id, "delete")


def _pending_writeback_ids(owner: str) -> tuple[list, list]:
    """(note ids to push, note ids to delete upstream).

    A task note with no `remote_id` has never reached To Do — including every
    task that already existed when the account was first connected, which is
    how the initial upload happens.
    """
    from core.database import MsTodoDeletedNote, Note, SessionLocal
    from sqlalchemy import or_

    db = SessionLocal()
    try:
        rows = _scope_owner(db.query(Note.id), owner).filter(
            Note.note_type.in_(SYNCED_NOTE_TYPES),
            or_(Note.todo_sync_pending.isnot(None), Note.remote_id.is_(None)),
        ).all()
        deletes_q = db.query(MsTodoDeletedNote.id)
        if owner:
            deletes_q = deletes_q.filter(MsTodoDeletedNote.owner == owner)
        else:
            deletes_q = deletes_q.filter(or_(
                MsTodoDeletedNote.owner.is_(None),
                MsTodoDeletedNote.owner == "",
            ))
        deletes = deletes_q.all()
        return [r[0] for r in rows], [r[0] for r in deletes]
    finally:
        db.close()


async def push_pending_tasks(owner: str) -> dict:
    """Retry every local change that has not reached To Do yet."""
    result: dict = {"tasks": 0, "errors": []}
    if not _load_mstodo_accounts(owner):
        return {"tasks": 0, "errors": ["No Microsoft To Do account is connected"]}

    note_ids, delete_ids = _pending_writeback_ids(owner)
    for note_id in note_ids:
        try:
            out = await push_task_update(owner, note_id)
        except Exception as e:
            result["errors"].append(f"{note_id}: {str(e)[:160]}")
            continue
        if out.get("ok") and not out.get("skipped"):
            result["tasks"] += 1
        elif not out.get("ok"):
            result["errors"].append(f"{note_id}: {str(out.get('error'))[:160]}")
    for note_id in delete_ids:
        try:
            out = await push_task_delete(owner, note_id)
        except Exception as e:
            result["errors"].append(f"{note_id}: {str(e)[:160]}")
            continue
        if out.get("ok") and not out.get("skipped"):
            result["tasks"] += 1
        elif not out.get("ok"):
            result["errors"].append(f"{note_id}: {str(out.get('error'))[:160]}")
    return result


async def sync_mstodo_direction(owner: str, direction: str = "pull") -> dict:
    """Run the sync in one direction, or both (push first, then pull)."""
    direction = (direction or "pull").strip().lower()
    if direction == "pull":
        return await sync_mstodo(owner)
    if direction == "push":
        return await push_pending_tasks(owner)
    if direction == "both":
        # Push first so a local edit wins over the copy the pull would
        # otherwise overwrite it with.
        pushed = await push_pending_tasks(owner)
        pulled = await sync_mstodo(owner)
        return {"push": pushed, "pull": pulled}
    return {"lists": 0, "tasks": 0, "deleted": 0,
            "errors": [f"Unsupported sync direction: {direction}"]}
