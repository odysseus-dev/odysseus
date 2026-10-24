"""Two-way calendar sync with Office 365 / Outlook.com via Microsoft Graph.

Exchange Online does not speak CalDAV, so `src/caldav_sync.py` cannot reach
an Office 365 mailbox. This module is its Graph-shaped sibling and keeps the
same contract with the rest of the app:

    pull   `sync_msgraph`          → Graph calendars/events into the local DB
    push   `push_event_{create,update,delete}` → local edits back to Graph
    both   `sync_msgraph_direction`

It reuses the calendar schema as-is. `CalendarCal.source` and
`CalendarEvent.origin` carry `"msgraph"`, `remote_href` holds the Graph event
id, `remote_etag` holds its `changeKey`, and `caldav_sync_pending` is the
retry marker. That last column name is CalDAV-flavoured because it predates
this module; it is the generic "unpushed local edit" flag, and renaming it
would cost a migration for no behavioural gain.

Occurrences, not series
-----------------------
The pull uses Graph's `calendarView`, which expands a recurring series into
concrete instances inside the sync window. Each instance is stored as its own
local row with an empty `rrule`, so the local expander leaves them alone and
editing one occurrence in Odysseus edits exactly that occurrence upstream —
which is what Graph's per-instance ids give us for free. Series created
locally still translate their RRULE into a Graph recurrence on push.

Auth
----
Calendar access is its own OAuth connection, separate from the mail one in
`routes/email_helpers.py`. Microsoft issues access tokens per resource and
refuses an authorization request that mixes scopes from two resources, so the
Outlook (IMAP/SMTP) scopes and the Graph `Calendars.ReadWrite` scope cannot
share a consent. Both use the same app registration — the same
MICROSOFT_OAUTH_CLIENT_ID, secret and tenant — so connecting a calendar needs
no extra setup in Entra beyond the delegated Calendars.ReadWrite permission.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# openid/email identify the mailbox we connected; offline_access is what makes
# a refresh token come back at all.
MSGRAPH_CALENDAR_SCOPES = (
    "openid email offline_access "
    "https://graph.microsoft.com/Calendars.ReadWrite"
)

_LOOKBACK_DAYS = 90
_LOOKAHEAD_DAYS = 365
_HTTP_TIMEOUT = 20
# Graph pages calendarView; cap the walk so one enormous calendar cannot spin
# the sync forever.
_MAX_PAGES = 50
_PAGE_SIZE = 200

_CAL_NAMESPACE = uuid.UUID("6f0b9f1e-6a5c-4c2e-9f4a-0c2b7d1e8a35")


# ── Account storage ───────────────────────────────────────────────

def _prefs_owner(owner):
    """The preferences key for *owner*.

    Two owner conventions reach this module. The calendar routes normalize an
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


def _load_msgraph_accounts(owner: str) -> list:
    """Return the Microsoft calendar accounts configured for *owner*."""
    from routes.prefs_routes import _load_for_user

    prefs = _load_for_user(_prefs_owner(owner)) or {}
    return list(prefs.get("msgraph_accounts") or [])


def _save_msgraph_accounts(owner: str, accounts: list) -> None:
    from routes.prefs_routes import _load_for_user, _save_for_user

    key = _prefs_owner(owner)
    prefs = _load_for_user(key) or {}
    prefs["msgraph_accounts"] = accounts
    _save_for_user(key, prefs)


def _find_account(owner: str, account_id: str) -> dict | None:
    for acc in _load_msgraph_accounts(owner):
        if acc.get("id") == account_id:
            return acc
    return None


def _stable_cal_id(remote_id: str, owner: str = "", account_id: str = "") -> str:
    """Deterministic local primary key for a remote Graph calendar.

    Scoped by owner and account so two users — or one user with two connected
    mailboxes — never collide on the same remote calendar id.
    """
    return str(uuid.uuid5(_CAL_NAMESPACE, f"{owner}|{account_id}|{remote_id}"))


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
    """Redeem the stored refresh token and persist the rotated one.

    Microsoft rotates refresh tokens: the response carries a new one and the
    old is spent. Dropping it would strand the connection at the next expiry.
    """
    import httpx
    from src.secret_storage import decrypt, encrypt

    client_id, client_secret = _oauth_client()
    if not (client_id and client_secret):
        return None

    accounts = _load_msgraph_accounts(owner)
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
                "scope": MSGRAPH_CALENDAR_SCOPES,
            },
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        # Never log the response body: it echoes tokens on some errors.
        logger.warning("Microsoft calendar token refresh failed for account=%s: %s",
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
    _save_msgraph_accounts(owner, accounts)
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
    # 120s of headroom so a token does not die mid-request.
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

    `path` is always appended to the fixed GRAPH_BASE, so a value echoed back
    by the server can never redirect a call to another host.
    """
    import httpx

    url = f"{GRAPH_BASE}{path}" if path.startswith("/") else path
    if not url.startswith(GRAPH_BASE):
        raise GraphError(0, "refusing to call a non-Graph URL")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # Ask Graph to render every start/end in UTC so we never have to guess
        # what a naive local time meant.
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


def _graph_paged(token: str, path: str, *, params=None):
    """Yield every item across Graph's @odata.nextLink pagination."""
    body = _graph_request(token, "GET", path, params=params)
    for page in range(_MAX_PAGES):
        for item in body.get("value") or []:
            yield item
        next_link = body.get("@odata.nextLink")
        if not next_link:
            return
        body = _graph_request(token, "GET", next_link)
    logger.warning("Graph pagination stopped at the %d-page cap for %s", _MAX_PAGES, path)


# ── Event conversion ──────────────────────────────────────────────

def _parse_graph_dt(node) -> datetime | None:
    """Parse Graph's {dateTime, timeZone} into a naive UTC datetime.

    We send `Prefer: outlook.timezone="UTC"`, so the values come back in UTC
    and the rest of the app stores naive-UTC. A stray offset is still honoured
    rather than trusted blindly.
    """
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
        # Unknown Windows zone name — treat as UTC rather than dropping the event.
        return dt


def _graph_body_text(event: dict) -> str:
    """Plain-text description, preferring the preview over raw HTML."""
    body = event.get("body") or {}
    content = (body.get("content") or "").strip()
    if (body.get("contentType") or "").lower() == "html" and content:
        preview = (event.get("bodyPreview") or "").strip()
        if preview:
            return preview
        try:
            import nh3

            return nh3.clean(content, tags=set()).strip()
        except Exception:
            return preview
    return content or (event.get("bodyPreview") or "").strip()


def _local_uid_for(event: dict) -> str:
    """Stable local primary key for a Graph event.

    `iCalUId` is the cross-system identifier and is what a CalDAV-sourced copy
    of the same meeting would use, so prefer it; expanded occurrences of a
    series carry a distinct one per instance, which is exactly the granularity
    we store. Fall back to the mailbox-local id when Graph omits it.
    """
    return str(event.get("iCalUId") or event.get("id") or uuid.uuid4())


def graph_event_to_row(event: dict) -> dict | None:
    """Map a Graph event onto the CalendarEvent column set, or None if unusable."""
    start = _parse_graph_dt(event.get("start"))
    if not start:
        return None
    end = _parse_graph_dt(event.get("end"))
    all_day = bool(event.get("isAllDay"))
    if not end or end <= start:
        end = start + (timedelta(days=1) if all_day else timedelta(hours=1))
    location = ((event.get("location") or {}).get("displayName") or "").strip()
    return {
        "uid": _local_uid_for(event),
        "summary": (event.get("subject") or "").strip(),
        "description": _graph_body_text(event),
        "location": location,
        "dtstart": start,
        "dtend": end,
        "all_day": all_day,
        # calendarView hands back concrete instances, so the series rule is
        # already applied — storing it would expand the series a second time.
        "rrule": "",
        "is_utc": not all_day,
        "status": "cancelled" if event.get("isCancelled") else "confirmed",
        "remote_href": str(event.get("id") or "") or None,
        "remote_etag": str(event.get("changeKey") or "") or None,
    }


def row_to_graph_event(ev: dict) -> dict:
    """Map a local event onto a Graph event body for create/update."""
    all_day = bool(ev.get("all_day"))
    start = ev.get("dtstart")
    end = ev.get("dtend")
    if all_day:
        # Graph requires midnight-to-midnight for an all-day event.
        start_s = start.strftime("%Y-%m-%dT00:00:00")
        end_dt = end if end and end.date() > start.date() else start + timedelta(days=1)
        end_s = end_dt.strftime("%Y-%m-%dT00:00:00")
    else:
        start_s = start.strftime("%Y-%m-%dT%H:%M:%S")
        end_s = (end or start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")

    body: dict = {
        "subject": ev.get("summary") or "",
        "body": {"contentType": "text", "content": ev.get("description") or ""},
        "start": {"dateTime": start_s, "timeZone": "UTC"},
        "end": {"dateTime": end_s, "timeZone": "UTC"},
        "isAllDay": all_day,
    }
    if ev.get("location"):
        body["location"] = {"displayName": ev["location"]}
    recurrence = rrule_to_graph_recurrence(ev.get("rrule") or "", start)
    if recurrence:
        body["recurrence"] = recurrence
    return body


# ── RRULE → Graph recurrence ──────────────────────────────────────

_RRULE_DAY_TO_GRAPH = {
    "MO": "monday", "TU": "tuesday", "WE": "wednesday", "TH": "thursday",
    "FR": "friday", "SA": "saturday", "SU": "sunday",
}
_GRAPH_INDEX = {1: "first", 2: "second", 3: "third", 4: "fourth", -1: "last", 5: "last"}


def _parse_rrule(rrule: str) -> dict:
    parts = {}
    for chunk in (rrule or "").replace("RRULE:", "").split(";"):
        key, _, value = chunk.partition("=")
        if key.strip():
            parts[key.strip().upper()] = value.strip()
    return parts


def rrule_to_graph_recurrence(rrule: str, start: datetime) -> dict | None:
    """Translate an iCalendar RRULE into Graph's structured recurrence.

    Covers the shapes the Odysseus UI can produce — DAILY/WEEKLY/MONTHLY/
    YEARLY with INTERVAL, COUNT, UNTIL and BYDAY. Anything else returns None
    and the caller pushes a single event rather than guessing at a pattern and
    silently creating the wrong series.
    """
    parts = _parse_rrule(rrule)
    freq = parts.get("FREQ", "").upper()
    if not freq:
        return None
    interval = max(1, int(parts.get("INTERVAL") or 1)) if (parts.get("INTERVAL") or "1").isdigit() else 1

    days = [
        _RRULE_DAY_TO_GRAPH[d[-2:].upper()]
        for d in (parts.get("BYDAY") or "").split(",")
        if d and d[-2:].upper() in _RRULE_DAY_TO_GRAPH
    ]

    if freq == "DAILY":
        pattern = {"type": "daily", "interval": interval}
    elif freq == "WEEKLY":
        pattern = {
            "type": "weekly",
            "interval": interval,
            "daysOfWeek": days or [start.strftime("%A").lower()],
        }
    elif freq == "MONTHLY":
        if days:
            ordinal = _ordinal_from_byday(parts.get("BYDAY") or "")
            if ordinal is None:
                return None
            pattern = {
                "type": "relativeMonthly", "interval": interval,
                "daysOfWeek": days, "index": ordinal,
            }
        else:
            day = parts.get("BYMONTHDAY") or str(start.day)
            if not day.lstrip("-").isdigit() or int(day) < 1:
                return None
            pattern = {"type": "absoluteMonthly", "interval": interval, "dayOfMonth": int(day)}
    elif freq == "YEARLY":
        month = parts.get("BYMONTH") or str(start.month)
        if not month.isdigit():
            return None
        if days:
            ordinal = _ordinal_from_byday(parts.get("BYDAY") or "")
            if ordinal is None:
                return None
            pattern = {
                "type": "relativeYearly", "interval": interval, "month": int(month),
                "daysOfWeek": days, "index": ordinal,
            }
        else:
            day = parts.get("BYMONTHDAY") or str(start.day)
            if not day.isdigit():
                return None
            pattern = {
                "type": "absoluteYearly", "interval": interval,
                "month": int(month), "dayOfMonth": int(day),
            }
    else:
        return None

    rng: dict = {"type": "noEnd", "startDate": start.strftime("%Y-%m-%d")}
    count = parts.get("COUNT")
    until = parts.get("UNTIL")
    if count and count.isdigit():
        rng = {"type": "numbered", "startDate": rng["startDate"], "numberOfOccurrences": int(count)}
    elif until:
        try:
            until_dt = datetime.strptime(until[:8], "%Y%m%d")
            rng = {"type": "endDate", "startDate": rng["startDate"],
                   "endDate": until_dt.strftime("%Y-%m-%d")}
        except ValueError:
            return None
    return {"pattern": pattern, "range": rng}


def _ordinal_from_byday(byday: str) -> str | None:
    """Graph's `index` word for a BYDAY prefix like `2FR` or `-1SU`."""
    first = (byday or "").split(",")[0].strip().upper()
    prefix = first[:-2]
    if not prefix:
        return "first"
    try:
        return _GRAPH_INDEX.get(int(prefix))
    except ValueError:
        return None


# ── Pull: Graph → local ───────────────────────────────────────────

def _sync_account_blocking(owner: str, account_id: str, token: str) -> dict:
    """Pull one connected mailbox's calendars into the local DB."""
    from core.database import CalendarCal, CalendarEvent, SessionLocal
    from routes.calendar_routes import _ensure_positive_duration

    result = {"calendars": 0, "events": 0, "deleted": 0, "errors": []}

    try:
        calendars = list(_graph_paged(token, "/me/calendars", params={"$top": 50}))
    except GraphError as e:
        result["errors"].append(f"Could not list calendars: {e.message}")
        return result

    window_start = datetime.utcnow() - timedelta(days=_LOOKBACK_DAYS)
    window_end = datetime.utcnow() + timedelta(days=_LOOKAHEAD_DAYS)
    view_params = {
        "startDateTime": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "$top": _PAGE_SIZE,
        "$select": "id,iCalUId,changeKey,subject,bodyPreview,body,location,start,end,isAllDay,isCancelled",
    }

    db = SessionLocal()
    try:
        for remote_cal in calendars:
            remote_id = str(remote_cal.get("id") or "")
            if not remote_id:
                continue
            display_name = (remote_cal.get("name") or "").strip() or "Outlook"
            cal_id = _stable_cal_id(remote_id, owner=owner, account_id=account_id)
            try:
                local_cal = db.query(CalendarCal).filter(
                    CalendarCal.id == cal_id, CalendarCal.owner == owner,
                ).first()
                if not local_cal:
                    local_cal = CalendarCal(
                        id=cal_id, owner=owner, name=display_name,
                        color=(remote_cal.get("hexColor") or "").strip() or "#0f6cbd",
                        source="msgraph", account_id=account_id or None,
                        caldav_base_url=remote_id,
                    )
                    db.add(local_cal)
                    db.commit()
                else:
                    changed = False
                    if local_cal.name != display_name:
                        local_cal.name, changed = display_name, True
                    if account_id and not local_cal.account_id:
                        local_cal.account_id, changed = account_id, True
                    if local_cal.caldav_base_url != remote_id:
                        local_cal.caldav_base_url, changed = remote_id, True
                    if changed:
                        db.commit()
                result["calendars"] += 1

                seen_uids: set = set()
                pending: dict = {}
                fetch_failed = False
                try:
                    events = list(_graph_paged(
                        token, f"/me/calendars/{remote_id}/calendarView", params=view_params,
                    ))
                except GraphError as e:
                    result["errors"].append(f"{display_name}: {e.message}")
                    fetch_failed = True
                    events = []

                for event in events:
                    row = graph_event_to_row(event)
                    if not row:
                        continue
                    uid_val = row["uid"]
                    seen_uids.add(uid_val)
                    row["dtend"] = _ensure_positive_duration(
                        row["dtstart"], row["dtend"], row["all_day"],
                    )

                    existing = pending.get(uid_val) or db.query(CalendarEvent).filter(
                        CalendarEvent.uid == uid_val,
                    ).first()
                    if existing:
                        # A local edit that has not reached Graph yet outranks
                        # the server copy; the push will settle it.
                        if existing.caldav_sync_pending in {"create", "update"}:
                            result["events"] += 1
                            continue
                        existing.calendar_id = local_cal.id
                        for field, value in row.items():
                            if field != "uid":
                                setattr(existing, field, value)
                        existing.origin = "msgraph"
                        existing.caldav_sync_pending = None
                    else:
                        new_ev = CalendarEvent(calendar_id=local_cal.id, origin="msgraph", **row)
                        db.add(new_ev)
                        pending[uid_val] = new_ev
                    result["events"] += 1
                db.commit()

                # Prune what vanished upstream — but only rows we pulled from
                # Graph (origin), inside the window we actually queried, and
                # never after a failed fetch, where an empty result means "we
                # could not look" rather than "the calendar is empty".
                if not fetch_failed and seen_uids:
                    stale = db.query(CalendarEvent).filter(
                        CalendarEvent.calendar_id == local_cal.id,
                        CalendarEvent.origin == "msgraph",
                        CalendarEvent.dtstart >= window_start,
                        CalendarEvent.dtstart <= window_end,
                        CalendarEvent.remote_href.isnot(None),
                        CalendarEvent.caldav_sync_pending.is_(None),
                        ~CalendarEvent.uid.in_(seen_uids),
                    ).all()
                    for ev in stale:
                        db.delete(ev)
                    result["deleted"] += len(stale)
                    db.commit()
            except Exception as e:
                logger.exception("Microsoft calendar sync failed for one calendar")
                result["errors"].append(str(e)[:200])
                db.rollback()
    finally:
        db.close()
    return result


async def sync_msgraph(owner: str) -> dict:
    """Pull every connected Microsoft calendar into the local DB."""
    import asyncio

    accounts = _load_msgraph_accounts(owner)
    if not accounts:
        return {"calendars": 0, "events": 0, "deleted": 0,
                "errors": ["No Microsoft calendar is connected"]}

    totals: dict = {"calendars": 0, "events": 0, "deleted": 0, "errors": []}
    for acc in accounts:
        account_id = acc.get("id") or ""
        label = acc.get("label") or acc.get("email") or account_id
        token = await asyncio.to_thread(_valid_access_token, owner, account_id)
        if not token:
            totals["errors"].append(f"{label}: sign-in expired — reconnect the calendar")
            continue
        try:
            out = await asyncio.to_thread(_sync_account_blocking, owner, account_id, token)
        except Exception as e:
            logger.warning("Microsoft calendar sync failed for account=%s: %s", account_id, e)
            totals["errors"].append(f"{label}: {str(e)[:160]}")
            continue
        for key in ("calendars", "events", "deleted"):
            totals[key] += out.get(key, 0)
        totals["errors"].extend(f"{label}: {err}" for err in out.get("errors") or [])
    return totals


# ── Push: local → Graph ───────────────────────────────────────────

def _event_payload(ev) -> dict:
    return {
        "uid": ev.uid,
        "summary": ev.summary,
        "description": ev.description,
        "location": ev.location,
        "dtstart": ev.dtstart,
        "dtend": ev.dtend,
        "all_day": ev.all_day,
        "is_utc": ev.is_utc,
        "rrule": ev.rrule or "",
        "remote_href": ev.remote_href,
        "remote_etag": ev.remote_etag,
    }


def _load_event_for_writeback(owner: str, uid: str) -> tuple[str, str, dict] | None:
    """(account_id, remote_calendar_id, payload) for a Graph-backed event."""
    from core.database import CalendarCal, CalendarEvent, SessionLocal

    db = SessionLocal()
    try:
        ev = (
            db.query(CalendarEvent).join(CalendarCal)
            .filter(CalendarEvent.uid == uid, CalendarCal.owner == owner)
            .first()
        )
        if not ev or not ev.calendar or ev.calendar.source != "msgraph":
            return None
        return ev.calendar.account_id or "", ev.calendar.caldav_base_url or "", _event_payload(ev)
    finally:
        db.close()


def _persist_push_result(owner: str, uid: str, remote: dict) -> None:
    from core.database import CalendarCal, CalendarEvent, SessionLocal

    db = SessionLocal()
    try:
        ev = (
            db.query(CalendarEvent).join(CalendarCal)
            .filter(CalendarEvent.uid == uid, CalendarCal.owner == owner)
            .first()
        )
        if not ev:
            return
        if remote.get("id"):
            ev.remote_href = str(remote["id"])
        if remote.get("changeKey"):
            ev.remote_etag = str(remote["changeKey"])
        ev.origin = "msgraph"
        ev.caldav_sync_pending = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _mark_pending(owner: str, uid: str, action: str) -> None:
    """Leave a retry marker so the next /sync picks the failed push back up."""
    from core.database import CalendarCal, CalendarEvent, SessionLocal

    db = SessionLocal()
    try:
        ev = (
            db.query(CalendarEvent).join(CalendarCal)
            .filter(CalendarEvent.uid == uid, CalendarCal.owner == owner)
            .first()
        )
        if ev:
            ev.caldav_sync_pending = action
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _push_blocking(owner: str, uid: str, action: str) -> dict:
    loaded = _load_event_for_writeback(owner, uid)
    if not loaded:
        return {"ok": True, "skipped": "not a Microsoft calendar event"}
    account_id, remote_cal_id, ev = loaded

    token = _valid_access_token(owner, account_id)
    if not token:
        return {"ok": False, "error": "sign-in expired — reconnect the calendar"}

    remote_id = ev.get("remote_href") or ""
    try:
        if action == "delete":
            if not remote_id:
                return {"ok": True, "skipped": "never reached Graph"}
            _graph_request(token, "DELETE", f"/me/events/{remote_id}")
            return {"ok": True}

        body = row_to_graph_event(ev)
        if action == "update" and remote_id:
            remote = _graph_request(token, "PATCH", f"/me/events/{remote_id}", json_body=body)
        else:
            path = f"/me/calendars/{remote_cal_id}/events" if remote_cal_id else "/me/events"
            remote = _graph_request(token, "POST", path, json_body=body)
        _persist_push_result(owner, uid, remote or {})
        return {"ok": True}
    except GraphError as e:
        # 404 on update/delete: the event is already gone upstream, which is
        # the state we were driving towards anyway.
        if e.status == 404 and action in {"update", "delete"}:
            return {"ok": True, "skipped": "already gone upstream"}
        return {"ok": False, "error": e.message}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


async def _push(owner: str, uid: str, action: str) -> dict:
    import asyncio

    out = await asyncio.to_thread(_push_blocking, owner, uid, action)
    if not out.get("ok") and not out.get("skipped") and action in {"create", "update"}:
        await asyncio.to_thread(_mark_pending, owner, uid, action)
    return out


async def push_event_create(owner: str, uid: str) -> dict:
    return await _push(owner, uid, "create")


async def push_event_update(owner: str, uid: str) -> dict:
    return await _push(owner, uid, "update")


async def push_event_delete(owner: str, uid: str) -> dict:
    """Delete upstream, driven by the tombstone the route left behind."""
    import asyncio
    from core.database import CalendarDeletedEvent, SessionLocal

    out = await asyncio.to_thread(_push_delete_blocking, owner, uid)
    if out.get("ok"):
        def _clear():
            db = SessionLocal()
            try:
                db.query(CalendarDeletedEvent).filter(
                    CalendarDeletedEvent.uid == uid,
                    CalendarDeletedEvent.owner == owner,
                ).delete()
                db.commit()
            finally:
                db.close()

        await asyncio.to_thread(_clear)
    return out


def _push_delete_blocking(owner: str, uid: str) -> dict:
    """Delete an event that may already be gone from the local table.

    The route deletes the row and leaves a tombstone, so unlike create/update
    this cannot read the event back — the tombstone carries what Graph needs.
    """
    from core.database import CalendarCal, CalendarDeletedEvent, SessionLocal

    db = SessionLocal()
    try:
        tombstone = db.query(CalendarDeletedEvent).filter(
            CalendarDeletedEvent.uid == uid,
            CalendarDeletedEvent.owner == owner,
        ).first()
        if not tombstone:
            return {"ok": True, "skipped": "no tombstone"}
        remote_id = tombstone.remote_href or ""
        cal = db.query(CalendarCal).filter(
            CalendarCal.id == tombstone.calendar_id,
            CalendarCal.owner == owner,
        ).first()
        account_id = (cal.account_id or "") if cal else ""
        is_msgraph = bool(cal and cal.source == "msgraph")
    finally:
        db.close()

    if not is_msgraph:
        return {"ok": True, "skipped": "not a Microsoft calendar event"}
    if not remote_id:
        return {"ok": True, "skipped": "never reached Graph"}

    token = _valid_access_token(owner, account_id)
    if not token:
        return {"ok": False, "error": "sign-in expired — reconnect the calendar"}
    try:
        _graph_request(token, "DELETE", f"/me/events/{remote_id}")
        return {"ok": True}
    except GraphError as e:
        if e.status == 404:
            return {"ok": True, "skipped": "already gone upstream"}
        return {"ok": False, "error": e.message}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _pending_writeback_uids(owner: str) -> tuple[list[str], list[str]]:
    from core.database import CalendarCal, CalendarDeletedEvent, CalendarEvent, SessionLocal

    db = SessionLocal()
    try:
        rows = (
            db.query(CalendarEvent.uid).join(CalendarCal)
            .filter(
                CalendarCal.owner == owner,
                CalendarCal.source == "msgraph",
                (
                    (CalendarEvent.caldav_sync_pending.isnot(None))
                    | (CalendarEvent.remote_href.is_(None))
                ),
            ).all()
        )
        deletes = (
            db.query(CalendarDeletedEvent.uid).join(
                CalendarCal, CalendarCal.id == CalendarDeletedEvent.calendar_id,
            )
            .filter(
                CalendarDeletedEvent.owner == owner,
                CalendarCal.source == "msgraph",
            ).all()
        )
        return [r[0] for r in rows], [r[0] for r in deletes]
    finally:
        db.close()


async def push_pending_events(owner: str) -> dict:
    """Retry every local edit that has not reached Graph yet."""
    result = {"events": 0, "errors": []}
    uids, delete_uids = _pending_writeback_uids(owner)
    for uid in uids:
        try:
            out = await push_event_update(owner, uid)
            if out.get("ok") and not out.get("skipped"):
                result["events"] += 1
            elif not out.get("ok"):
                result["errors"].append(f"{uid}: {str(out.get('error'))[:160]}")
        except Exception as e:
            logger.warning("Microsoft calendar pending push failed for uid=%s: %s", uid, e)
            result["errors"].append(f"{uid}: {str(e)[:160]}")
    for uid in delete_uids:
        try:
            out = await push_event_delete(owner, uid)
            if out.get("ok") and not out.get("skipped"):
                result["events"] += 1
            elif not out.get("ok"):
                result["errors"].append(f"{uid}: {str(out.get('error'))[:160]}")
        except Exception as e:
            logger.warning("Microsoft calendar pending delete failed for uid=%s: %s", uid, e)
            result["errors"].append(f"{uid}: {str(e)[:160]}")
    return result


async def sync_msgraph_direction(owner: str, direction: str = "pull") -> dict:
    """Run the sync in one direction, or both (push first, then pull)."""
    direction = (direction or "pull").strip().lower()
    if direction == "pull":
        return await sync_msgraph(owner)
    if direction == "push":
        return await push_pending_events(owner)
    if direction == "both":
        # Push first so a local edit wins over the copy the pull would
        # otherwise overwrite it with.
        pushed = await push_pending_events(owner)
        pulled = await sync_msgraph(owner)
        return {"push": pushed, "pull": pulled}
    return {"calendars": 0, "events": 0, "deleted": 0,
            "errors": [f"Unsupported sync direction: {direction}"]}
