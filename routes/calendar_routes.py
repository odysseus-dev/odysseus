"""Calendar routes — local SQLite-backed calendar CRUD."""

import logging
import re
import uuid
from datetime import datetime, date, timedelta
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import or_, and_
from dateutil.rrule import rrulestr

from core.database import SessionLocal, CalendarCal, CalendarEvent
from src.auth_helpers import require_user
from src.upload_limits import read_upload_limited, ICS_MAX_BYTES
from src.services.calendar_helpers import (
    _parse_dt_pair, _parse_dt,
    _resolve_base_uid, _ensure_default_calendar,
    FALLBACK_OWNER, _SINGLE_USER_MODE,
)

logger = logging.getLogger(__name__)


def _ics_naive_dtstart(dt):
    """Naive value matching how import_ics STORES CalendarEvent.dtstart.

    Timed tz-aware events are stored as UTC with tzinfo stripped, all-day
    dates as midnight datetimes, naive datetimes unchanged. The ICS dedup
    must compute the same value or a re-import never matches the stored row.
    """
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            from datetime import timezone as _tz
            return dt.astimezone(_tz.utc).replace(tzinfo=None)
        return dt
    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day)
    return dt

# FALLBACK_OWNER and _SINGLE_USER_MODE re-exported from src.services.calendar_helpers


def _require_user(request: Request) -> str:
    """Return the authenticated user. Uses require_user so AUTH_ENABLED=false
    and single-user mode both work: require_user returns "" when auth is
    disabled or unconfigured, and only raises 401 when auth is configured but
    the caller is unauthenticated. Falls back to FALLBACK_OWNER for calendar
    writes so data isn't stored under an empty owner in single-user mode."""
    user = require_user(request)
    if user:
        return user
    # require_user returned "" — auth is off or unconfigured (single-user).
    # Use FALLBACK_OWNER so calendar rows have a stable owner for filtering.
    return FALLBACK_OWNER


def _get_or_404_calendar(db, cal_id: str, owner: str) -> CalendarCal:
    cal = db.query(CalendarCal).filter(CalendarCal.id == cal_id).first()
    if not cal:
        raise HTTPException(404, "Calendar not found")
    # Tighten the legacy null-owner gate (v2 review HIGH-12): if the
    # caller is authenticated AND the calendar's owner is null OR
    # belongs to a different user, treat it as not-found. The previous
    # rule (`if cal.owner and cal.owner != owner`) silently allowed any
    # authenticated user to read/edit any calendar with owner=None.
    if owner and (cal.owner is None or cal.owner != owner):
        raise HTTPException(404, "Calendar not found")
    return cal


def _get_or_404_event(db, uid: str, owner: str) -> CalendarEvent:
    ev = db.query(CalendarEvent).join(CalendarCal).filter(CalendarEvent.uid == uid).first()
    if not ev:
        raise HTTPException(404, "Event not found")
    cal = ev.calendar
    if owner and cal and (cal.owner is None or cal.owner != owner):
        raise HTTPException(404, "Event not found")
    return ev


def _ics_escape(text: str) -> str:
    """Escape a value for an iCalendar TEXT field (RFC 5545 §3.3.11).

    Backslash, semicolon and comma are structural in TEXT values and must be
    escaped, and newlines become a literal ``\\n``. Backslash is escaped first
    so the escapes we add aren't re-escaped.
    """
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _safe_ics_filename(name: str) -> str:
    """Return a conservative .ics filename safe for Content-Disposition."""
    stem = name if isinstance(name, str) else ""
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._-")
    if not stem:
        stem = "calendar"
    return f"{stem[:128]}.ics"


# _resolve_base_uid re-exported from src.services.calendar_helpers

# ── Pydantic models ──

class EventCreate(BaseModel):
    summary: str
    dtstart: str  # ISO 8601
    dtend: Optional[str] = None
    all_day: bool = False
    description: str = ""
    location: str = ""
    calendar_href: Optional[str] = None  # calendar id
    rrule: Optional[str] = None
    color: Optional[str] = None  # per-event color override


class EventUpdate(BaseModel):
    summary: Optional[str] = None
    dtstart: Optional[str] = None
    dtend: Optional[str] = None
    all_day: Optional[bool] = None
    description: Optional[str] = None
    location: Optional[str] = None
    rrule: Optional[str] = None
    color: Optional[str] = None


# ── Helpers ──
import src.services.calendar_helpers as _cal_helpers


def parse_due_for_user(s: str) -> str:
    """Delegate to the service parser using THIS module's `datetime` as the
    clock, so tests that freeze routes.calendar_routes.datetime still work."""
    _cal_helpers._DT_OVERRIDE = datetime
    try:
        return _cal_helpers.parse_due_for_user(s)
    finally:
        _cal_helpers._DT_OVERRIDE = None


# _ensure_default_calendar, parse_due_for_user, _parse_dt_pair, _parse_dt
# re-exported from src.services.calendar_helpers (imported above)

# Per-request user time context. chat_routes sets this from browser timezone
# headers so natural-language times the LLM emits ("today at 9pm") are parsed
# in the user's timezone, not the server's clock. None = unknown, fall back to
# legacy server-local behavior.
from src.user_time import (
    get_user_tz_name,
    get_user_tz_offset,
    now_user_local,
    set_user_tz_name,
    set_user_tz_offset,
    user_timezone,
)


def _event_to_dict(ev: CalendarEvent) -> dict:
    """Convert a CalendarEvent model to the API dict format.

    Timed events whose stored datetimes represent UTC (is_utc=True) are
    serialized with a trailing `Z` so the frontend `new Date()` interprets
    them as absolute UTC and renders in the user's current local time. Legacy
    rows without the flag are emitted as naive ISO (read as local) to avoid
    silently shifting existing events.
    """
    if ev.all_day:
        start_str = ev.dtstart.strftime("%Y-%m-%d")
        end_str = ev.dtend.strftime("%Y-%m-%d")
    else:
        suffix = "Z" if getattr(ev, "is_utc", False) else ""
        start_str = ev.dtstart.isoformat() + suffix
        end_str = ev.dtend.isoformat() + suffix
    return {
        "uid": ev.uid,
        "summary": ev.summary or "",
        "dtstart": start_str,
        "dtend": end_str,
        "all_day": ev.all_day,
        "is_utc": bool(getattr(ev, "is_utc", False)),
        "description": ev.description or "",
        "location": ev.location or "",
        "rrule": ev.rrule or "",
        "calendar": ev.calendar.name if ev.calendar else "",
        "calendar_href": ev.calendar_id,
        "color": ev.color or (ev.calendar.color if ev.calendar else ""),
        "event_type": getattr(ev, "event_type", None),
        "importance": getattr(ev, "importance", None) or "normal",
    }


# ── Recurrence expansion ──

_RRULE_EXPANSION_LIMIT = 1000


def _expand_rrule(
    ev: CalendarEvent, start: datetime, end: datetime
) -> List[dict]:
    """Expand a single recurring CalendarEvent into occurrence dicts.

    Each occurrence gets a stable compound UID of the form
    ``{base_uid}::{date_or_datetime}`` so the frontend can tell
    occurrences apart while the series UID is still recoverable
    for edit/delete targeting.

    Non-recurring events (empty rrule) are returned as a single-item
    list — the caller doesn't need to branch.
    """
    duration = ev.dtend - ev.dtstart

    if not ev.rrule or not ev.rrule.strip():
        # Non-recurring — return the base event as-is. list_events
        # already filters non-recurring rows with the overlap check
        # in SQL, so we don't re-check here.
        d = _event_to_dict(ev)
        d["is_recurrence"] = False
        d["series_uid"] = ev.uid
        d["truncated"] = False
        return [d]

    # Parse the rrule, applying it to the base dtstart.
    rrule_str = ev.rrule
    if ev.dtstart is not None and getattr(ev.dtstart, "tzinfo", None) is None:
        # Events are stored with a naive (UTC) dtstart, but standard .ics
        # exporters (Google/Apple/Outlook/Fastmail) write the bound as an
        # absolute UTC value, e.g. UNTIL=20240105T090000Z. dateutil refuses to
        # mix a tz-aware UNTIL with a naive DTSTART ("RRULE UNTIL values must be
        # specified in UTC when DTSTART is timezone-aware"), so the except branch
        # below would silently collapse the whole series to a single event.
        # Drop the trailing Z so UNTIL matches the naive DTSTART.
        import re as _re
        rrule_str = _re.sub(
            r"(UNTIL=\d{8}(?:T\d{6})?)Z", r"\1", rrule_str, flags=_re.IGNORECASE
        )
    try:
        rule = rrulestr(rrule_str, dtstart=ev.dtstart)
    except Exception as ex:
        logger.warning(
            "Failed to parse rrule=%r for event %s: %s", ev.rrule, ev.uid, ex
        )
        d = _event_to_dict(ev)
        d["is_recurrence"] = False
        d["series_uid"] = ev.uid
        d["truncated"] = False
        # Malformed RRULE rows are fetched by the recurring SQL branch
        # with only dtstart < end_dt — the base event may not actually
        # overlap the window. Only return if it does.
        if ev.dtstart < end and ev.dtend > start:
            return [d]
        return []

    # Expand from start - duration so multi-day / overnight occurrences
    # that start before the window but end inside it are captured
    # (matching non-recurring overlap semantics: dtstart < end AND
    # dtend > start).
    expand_start = start - duration
    results = []
    truncated = False
    base = _event_to_dict(ev)

    for occ_start in rule.xafter(expand_start, inc=True):
        if occ_start >= end:
            break

        occ_end = occ_start + duration

        # Overlap filter: occurrence must intersect [start, end).
        # This enforces exclusive-end semantics (occ_start >= end is
        # excluded) and includes multi-day crossings (occ_end > start).
        if occ_end <= start:
            continue

        if len(results) >= _RRULE_EXPANSION_LIMIT:
            truncated = True
            break

        # Build the compound uid: {base_uid}::{date} or ::{datetime}
        if ev.all_day:
            occ_uid = f"{ev.uid}::{occ_start.strftime('%Y-%m-%d')}"
        else:
            occ_uid = f"{ev.uid}::{occ_start.strftime('%Y-%m-%dT%H:%M')}"

        d = dict(base)
        d["uid"] = occ_uid
        d["series_uid"] = ev.uid
        d["is_recurrence"] = True
        d["truncated"] = False

        if ev.all_day:
            d["dtstart"] = occ_start.strftime("%Y-%m-%d")
            d["dtend"] = occ_end.strftime("%Y-%m-%d")
        else:
            suffix = "Z" if getattr(ev, "is_utc", False) else ""
            d["dtstart"] = occ_start.isoformat() + suffix
            d["dtend"] = occ_end.isoformat() + suffix
            d["is_utc"] = bool(getattr(ev, "is_utc", False))

        results.append(d)

    if truncated:
        for d in results:
            d["truncated"] = True

    return results


# ── Routes ──

def setup_calendar_routes() -> APIRouter:
    router = APIRouter(prefix="/api/calendar", tags=["calendar"])

    # ── CalDAV multi-account helpers ─────────────────────────────────────────

    def _get_caldav_accounts(owner: str) -> list:
        from src.caldav_sync import _load_caldav_accounts
        return _load_caldav_accounts(owner)

    def _save_caldav_accounts(owner: str, accounts: list) -> None:
        from routes.prefs_routes import _load_for_user, _save_for_user
        prefs = _load_for_user(owner) or {}
        prefs["caldav_accounts"] = accounts
        prefs.pop("caldav", None)
        _save_for_user(owner, prefs)

    # ── CalDAV config routes (backward-compat single-account API) ────────────

    @router.get("/config")
    async def get_config(request: Request):
        """Legacy single-account endpoint — returns the first configured account."""
        owner = _require_user(request)
        accounts = _get_caldav_accounts(owner)
        if not accounts:
            return {"url": "", "username": "", "password": "", "has_password": False, "local": True}
        first = accounts[0]
        pw = first.get("password") or ""
        has_pw = False
        if pw:
            try:
                from src.secret_storage import decrypt
                has_pw = bool(decrypt(pw))
            except Exception:
                has_pw = bool(pw)
        return {
            "url": first.get("url", "") or "",
            "username": first.get("username", "") or "",
            "password": "",
            "has_password": has_pw,
            "local": not bool(first.get("url")),
        }

    @router.post("/config")
    async def save_config(request: Request):
        """Legacy single-account endpoint — upserts the first account."""
        owner = _require_user(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        accounts = _get_caldav_accounts(owner)
        if not (body.get("url") or "").strip():
            _save_caldav_accounts(owner, [])
            return {"ok": True, "cleared": True}
        from src.caldav_sync import validate_caldav_url
        try:
            validated_url = validate_caldav_url(body.get("url", ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        if accounts:
            acc = dict(accounts[0])
        else:
            import uuid as _uuid
            acc = {"id": str(_uuid.uuid4()), "label": "CalDAV"}
        acc["url"] = validated_url
        acc["username"] = (body.get("username") or "").strip()
        if body.get("password"):
            from src.secret_storage import encrypt
            acc["password"] = encrypt(body["password"])
        new_accounts = [acc] + (accounts[1:] if len(accounts) > 1 else [])
        _save_caldav_accounts(owner, new_accounts)
        return {"ok": True}

    # ── CalDAV multi-account CRUD ─────────────────────────────────────────────

    @router.get("/config/accounts")
    async def list_caldav_accounts(request: Request):
        """Return all configured CalDAV accounts (passwords never returned)."""
        owner = _require_user(request)
        accounts = _get_caldav_accounts(owner)
        safe = []
        for acc in accounts:
            pw = acc.get("password") or ""
            has_pw = False
            if pw:
                try:
                    from src.secret_storage import decrypt
                    has_pw = bool(decrypt(pw))
                except Exception:
                    has_pw = bool(pw)
            safe.append({
                "id": acc.get("id", ""),
                "label": acc.get("label", "") or acc.get("url", ""),
                "url": acc.get("url", "") or "",
                "username": acc.get("username", "") or "",
                "has_password": has_pw,
            })
        return {"accounts": safe}

    @router.post("/config/accounts")
    async def add_caldav_account(request: Request):
        """Add a new CalDAV account."""
        import uuid as _uuid
        owner = _require_user(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        from src.caldav_sync import validate_caldav_url
        try:
            url = validate_caldav_url(body.get("url", ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not body.get("password"):
            raise HTTPException(400, "Password is required")
        from src.secret_storage import encrypt
        new_acc = {
            "id": str(_uuid.uuid4()),
            "label": (body.get("label") or "").strip() or "CalDAV",
            "url": url,
            "username": (body.get("username") or "").strip(),
            "password": encrypt(body["password"]),
        }
        accounts = _get_caldav_accounts(owner)
        accounts.append(new_acc)
        _save_caldav_accounts(owner, accounts)
        return {"ok": True, "id": new_acc["id"]}

    @router.put("/config/accounts/{account_id}")
    async def update_caldav_account(account_id: str, request: Request):
        """Update an existing CalDAV account by id."""
        owner = _require_user(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        accounts = _get_caldav_accounts(owner)
        idx = next((i for i, a in enumerate(accounts) if a.get("id") == account_id), None)
        if idx is None:
            raise HTTPException(404, "Account not found")
        acc = dict(accounts[idx])
        if body.get("url"):
            from src.caldav_sync import validate_caldav_url
            try:
                acc["url"] = validate_caldav_url(body["url"])
            except ValueError as e:
                raise HTTPException(400, str(e))
        if body.get("label") is not None:
            acc["label"] = (body.get("label") or "").strip() or "CalDAV"
        if body.get("username") is not None:
            acc["username"] = (body.get("username") or "").strip()
        if body.get("password"):
            from src.secret_storage import encrypt
            acc["password"] = encrypt(body["password"])
        accounts[idx] = acc
        _save_caldav_accounts(owner, accounts)
        return {"ok": True}

    @router.delete("/config/accounts/{account_id}")
    async def delete_caldav_account(account_id: str, request: Request):
        """Remove a CalDAV account by id."""
        owner = _require_user(request)
        accounts = _get_caldav_accounts(owner)
        new_accounts = [a for a in accounts if a.get("id") != account_id]
        if len(new_accounts) == len(accounts):
            raise HTTPException(404, "Account not found")
        _save_caldav_accounts(owner, new_accounts)
        return {"ok": True}

    @router.post("/test")
    async def test_connection(request: Request):
        """Probe a CalDAV server with a PROPFIND. Accepts an optional body:
        {url, username, password} to test before saving, or {account_id} to
        test an already-saved account. Falls back to the first saved account
        when nothing is provided."""
        owner = _require_user(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        url = (body.get("url") or "").strip()
        user = (body.get("username") or "").strip()
        pw = body.get("password") or ""
        if not (url and user and pw):
            # Look up a saved account: by id if supplied, else first account.
            accounts = _get_caldav_accounts(owner)
            acc = None
            if body.get("account_id"):
                acc = next((a for a in accounts if a.get("id") == body["account_id"]), None)
            if acc is None and accounts:
                acc = accounts[0]
            if acc:
                url = url or (acc.get("url") or "")
                user = user or (acc.get("username") or "")
                if not pw:
                    pw = acc.get("password") or ""
                    if pw:
                        try:
                            from src.secret_storage import decrypt
                            pw = decrypt(pw)
                        except Exception:
                            pass
        if not (url and user and pw):
            return {"ok": False, "error": "Missing URL, username, or password"}
        from src.caldav_sync import validate_caldav_url
        try:
            url = validate_caldav_url(url)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        import httpx
        propfind_body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/>'
            '</d:prop></d:propfind>'
        )
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=False, trust_env=False) as cx:
                r = await cx.request(
                    "PROPFIND", url,
                    auth=(user, pw),
                    headers={"Depth": "0", "Content-Type": "application/xml"},
                    content=propfind_body,
                )
                # If the server demands Digest (Baïkal default, SabreDAV-based
                # servers, Radicale with htdigest), the Basic attempt above
                # 401s. Retry once with httpx.DigestAuth so this test matches
                # what the real sync does via caldav.DAVClient in
                # src/caldav_sync.py (which negotiates the scheme).
                if r.status_code == 401 and "digest" in r.headers.get("www-authenticate", "").lower():
                    r = await cx.request(
                        "PROPFIND", url,
                        auth=httpx.DigestAuth(user, pw),
                        headers={"Depth": "0", "Content-Type": "application/xml"},
                        content=propfind_body,
                    )
            # 207 = Multi-Status — standard CalDAV success. 200 also
            # acceptable. Anything else (401/403/404/5xx) means trouble.
            if r.status_code in (200, 207):
                return {"ok": True}
            if r.status_code == 401:
                return {"ok": False, "error": "Auth failed — check username/password"}
            if r.status_code == 403:
                return {"ok": False, "error": "Forbidden — user can't access that URL"}
            if r.status_code == 404:
                return {"ok": False, "error": "Not found — check the URL path"}
            if 300 <= r.status_code < 400:
                return {"ok": False, "error": "Redirects are not followed for CalDAV safety; use the final URL"}
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        except httpx.ConnectError as e:
            return {"ok": False, "error": f"Connection refused: {e}"[:200]}
        except httpx.TimeoutException:
            return {"ok": False, "error": "Connection timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @router.post("/sync")
    async def sync_caldav_endpoint(request: Request):
        """Pull events from the configured CalDAV server into local DB.
        Returns counts + any per-calendar errors. Called by the frontend
        on calendar open and by the periodic scheduler loop."""
        owner = _require_user(request)
        from src.caldav_sync import sync_caldav
        return await sync_caldav(owner)


    @router.delete("/calendars/{cal_id}")
    async def delete_calendar(request: Request, cal_id: str):
        owner = _require_user(request)
        db = SessionLocal()
        try:
            cal = _get_or_404_calendar(db, cal_id, owner)
            db.query(CalendarEvent).filter(CalendarEvent.calendar_id == cal_id).delete()
            db.delete(cal)
            db.commit()
            return {"ok": True}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error("Failed to delete calendar %s: %s", cal_id, e)
            raise HTTPException(500, "Failed to delete calendar")
        finally:
            db.close()


    @router.get("/calendars")
    async def list_calendars(request: Request):
        owner = _require_user(request)
        db = SessionLocal()
        try:
            _ensure_default_calendar(db, owner)
            cals = db.query(CalendarCal).filter(CalendarCal.owner == owner).all()
            return {"calendars": [
                {"name": c.name, "href": c.id, "color": c.color, "source": c.source}
                for c in cals
            ]}
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to list calendars: %s", e)
            raise HTTPException(500, "Failed to list calendars")
        finally:
            db.close()

    @router.get("/events")
    async def list_events(request: Request, start: str, end: str, calendar: str = ""):
        owner = _require_user(request)
        try:
            start_dt = _parse_dt(start)
            end_dt = _parse_dt(end)
        except ValueError:
            # A malformed range (e.g. a stray "NaN-NaN-NaN" from the client)
            # shouldn't spam the user with an error notification on every poll —
            # just log it and return no events for this window.
            logger.warning("list_events: unparseable range start=%r end=%r", start, end)
            return {"events": []}
        db = SessionLocal()
        try:
            # Scope events to calendars owned by the caller.
            # Non-recurring events must overlap the query window; recurring
            # events (with RRULE) whose base dtstart is before the window end
            # are fetched so their actual occurrences can be expanded
            # server-side and appear in every year they repeat, not just the
            # DTSTART year.
            q = db.query(CalendarEvent).join(CalendarCal).filter(
                CalendarEvent.status != "cancelled",
                CalendarCal.owner == owner,
                or_(
                    # Non-recurring: event times must overlap the query window
                    and_(
                        or_(CalendarEvent.rrule == "", CalendarEvent.rrule.is_(None)),
                        CalendarEvent.dtstart < end_dt,
                        CalendarEvent.dtend > start_dt,
                    ),
                    # Recurring: dtstart before window end — RRULE expansion
                    # generates the actual occurrences within the window
                    and_(
                        CalendarEvent.rrule.isnot(None),
                        CalendarEvent.rrule != "",
                        CalendarEvent.dtstart < end_dt,
                    ),
                ),
            )
            if calendar:
                q = q.filter(
                    (CalendarEvent.calendar_id == calendar) |
                    (CalendarCal.name == calendar)
                )
            events = q.order_by(CalendarEvent.dtstart).all()

            # Expand recurring events into individual occurrences.
            expanded = []
            for e in events:
                expanded.extend(_expand_rrule(e, start_dt, end_dt))

            # Sort by occurrence start time for consistent frontend ordering.
            truncated = any(e.get("truncated") for e in expanded)
            expanded.sort(key=lambda d: d["dtstart"])
            response: dict = {"events": expanded}
            if truncated:
                response["truncated"] = True
            return response
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to list events: %s", e)
            raise HTTPException(500, "Failed to list events")
        finally:
            db.close()

    @router.post("/events")
    async def create_event(request: Request, data: EventCreate):
        owner = _require_user(request)
        db = SessionLocal()
        try:
            cal = None
            if data.calendar_href:
                cal = db.query(CalendarCal).filter(CalendarCal.id == data.calendar_href).first()
                # Reject calendars that aren't owned by the caller. The
                # previous `if cal and cal.owner and ...` check silently
                # passed null-owner (legacy) rows, letting any authenticated
                # user write events into them. Same null-owner gate as
                # `_get_or_404_calendar`.
                if cal and (cal.owner is None or cal.owner != owner):
                    raise HTTPException(404, "Calendar not found")
            if not cal:
                cal = _ensure_default_calendar(db, owner)

            uid = str(uuid.uuid4())
            # Use the tz-detecting parser so events posted with an offset
            # (e.g. "2026-05-13T10:00:00+09:00" or "...Z") get stored as UTC
            # and flagged for proper Z-suffix on read-back.
            dtstart, _is_utc = _parse_dt_pair(data.dtstart)
            if data.dtend:
                dtend, _end_utc = _parse_dt_pair(data.dtend)
                # If start was tz-aware but end was naive (or vice-versa),
                # trust whichever flag is True — they should match.
                _is_utc = _is_utc or _end_utc
            elif data.all_day:
                dtend = dtstart + timedelta(days=1)
            else:
                dtend = dtstart + timedelta(hours=1)

            ev = CalendarEvent(
                uid=uid,
                calendar_id=cal.id,
                summary=data.summary,
                description=data.description,
                location=data.location,
                dtstart=dtstart,
                dtend=dtend,
                all_day=data.all_day,
                is_utc=_is_utc and not data.all_day,
                rrule=data.rrule or "",
                color=data.color or None,
            )
            db.add(ev)
            db.commit()
            if cal.source == "caldav":
                # Push the new event to the remote so it appears on the user's
                # other devices — the sync is otherwise pull-only (#800).
                from src.caldav_writeback import writeback_event
                await writeback_event(owner, cal.source, cal.id, {
                    "uid": uid, "summary": data.summary, "description": data.description,
                    "location": data.location, "dtstart": dtstart, "dtend": dtend,
                    "all_day": data.all_day, "is_utc": _is_utc and not data.all_day,
                    "rrule": data.rrule or "",
                })
            return {"ok": True, "uid": uid}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error("Failed to create event: %s", e)
            raise HTTPException(500, "Failed to create event")
        finally:
            db.close()

    @router.put("/events/{uid}")
    async def update_event(request: Request, uid: str, data: EventUpdate):
        owner = _require_user(request)
        try:
            base_uid = _resolve_base_uid(uid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db = SessionLocal()
        try:
            ev = _get_or_404_event(db, base_uid, owner)
            if data.summary is not None:
                ev.summary = data.summary
            if data.description is not None:
                ev.description = data.description
            if data.location is not None:
                ev.location = data.location
            if data.dtstart is not None:
                ev.dtstart, _s_utc = _parse_dt_pair(data.dtstart)
                # When the incoming payload carries tz info, mark the row as
                # UTC-stored so the serializer adds Z. Don't flip the flag
                # off if start arrives naive but end was UTC — only escalate.
                if _s_utc:
                    ev.is_utc = True
            if data.dtend is not None:
                ev.dtend, _e_utc = _parse_dt_pair(data.dtend)
                if _e_utc:
                    ev.is_utc = True
            if data.all_day is not None:
                ev.all_day = data.all_day
                if data.all_day:
                    ev.is_utc = False  # all-day stays date-only
            if data.rrule is not None:
                ev.rrule = data.rrule
            if data.color is not None:
                ev.color = data.color if data.color else None
            db.commit()
            cal = db.query(CalendarCal).filter(CalendarCal.id == ev.calendar_id).first()
            if cal and cal.source == "caldav":
                from src.caldav_writeback import writeback_event
                await writeback_event(owner, cal.source, cal.id, {
                    "uid": ev.uid, "summary": ev.summary, "description": ev.description,
                    "location": ev.location, "dtstart": ev.dtstart, "dtend": ev.dtend,
                    "all_day": ev.all_day, "is_utc": ev.is_utc, "rrule": ev.rrule or "",
                })
            return {"ok": True}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error("Failed to update event: %s", e)
            raise HTTPException(500, "Failed to update event")
        finally:
            db.close()

    @router.delete("/events/{uid}")
    async def delete_event(request: Request, uid: str):
        owner = _require_user(request)
        try:
            base_uid = _resolve_base_uid(uid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db = SessionLocal()
        try:
            ev = _get_or_404_event(db, base_uid, owner)
            # Capture what the remote push needs BEFORE the row is gone.
            _cal = db.query(CalendarCal).filter(CalendarCal.id == ev.calendar_id).first()
            _is_caldav = bool(_cal and _cal.source == "caldav")
            _cal_id, _ev_uid = ev.calendar_id, ev.uid
            db.delete(ev)
            db.commit()
            if _is_caldav:
                from src.caldav_writeback import writeback_event
                await writeback_event(owner, "caldav", _cal_id, {"uid": _ev_uid}, delete=True)
            return {"ok": True}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error("Failed to delete event: %s", e)
            raise HTTPException(500, "Failed to delete event")
        finally:
            db.close()

    @router.post("/calendars")
    async def create_calendar(request: Request, name: str = "Imported", color: str = "#5b8abf"):
        owner = _require_user(request)
        db = SessionLocal()
        try:
            cal = CalendarCal(
                id=str(uuid.uuid4()),
                owner=owner,
                name=name,
                color=color,
                source="local",
            )
            db.add(cal)
            db.commit()
            return {"ok": True, "id": cal.id, "name": cal.name, "color": cal.color}
        except Exception as e:
            db.rollback()
            logger.error("Failed to create calendar: %s", e)
            raise HTTPException(500, "Failed to create calendar")
        finally:
            db.close()

    @router.put("/calendars/{cal_id}")
    async def update_calendar(request: Request, cal_id: str, name: str = None, color: str = None):
        owner = _require_user(request)
        db = SessionLocal()
        try:
            cal = _get_or_404_calendar(db, cal_id, owner)
            if name is not None:
                cal.name = name
            if color is not None:
                cal.color = color
            db.commit()
            return {"ok": True}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error("Failed to update calendar: %s", e)
            raise HTTPException(500, "Failed to update calendar")
        finally:
            db.close()


    # Hard cap on ICS upload (ICS_MAX_BYTES, default 10 MB). Loading the whole
    # file into memory is unavoidable with python-icalendar, so an unbounded
    # upload would OOM.

    @router.post("/import")
    async def import_ics(request: Request, file: UploadFile = File(...), calendar_name: str = ""):
        """Import events from an .ics file (scoped to caller's account)."""
        from icalendar import Calendar as iCal

        owner = _require_user(request)
        db = SessionLocal()
        try:
            content = await read_upload_limited(file, ICS_MAX_BYTES, "ICS file")
            try:
                cal_data = iCal.from_ical(content)
            except Exception as e:
                raise HTTPException(400, f"Invalid ICS file: {e}")

            # Sanitize display name — length cap + strip control chars
            raw_name = calendar_name.strip() or (file.filename or "").replace(".ics", "").replace("_", " ").strip() or "Imported"
            cal_display = "".join(c for c in raw_name if c.isprintable())[:120] or "Imported"

            target_cal = db.query(CalendarCal).filter(
                CalendarCal.name == cal_display,
                CalendarCal.owner == owner,
            ).first()
            if not target_cal:
                target_cal = CalendarCal(
                    id=str(uuid.uuid4()),
                    owner=owner,
                    name=cal_display,
                    color="#7c4dff",
                    source="import",
                )
                db.add(target_cal)
                db.commit()
                db.refresh(target_cal)

            imported = skipped = 0
            for comp in cal_data.walk():
                if comp.name != "VEVENT":
                    continue
                # Generate a fresh uid for each import. The old code reused
                # the VEVENT uid from the file, which leaked across users:
                # a uid present on ANY user's calendar caused this user's
                # row to be silently skipped (and enabled enumeration).
                # Using a fresh uuid scopes uniqueness per-row.
                uid_val = str(uuid.uuid4())
                dtstart = comp.get("dtstart")
                if not dtstart:
                    skipped += 1
                    continue

                # Dedup INSIDE this user's target calendar only — same
                # source-uid + same dtstart in the same target = duplicate.
                source_uid = str(comp.get("uid", "")) or None
                if source_uid:
                    src_dtstart = dtstart.dt
                    # Normalize to the SAME naive form import_ics stores, so a
                    # re-import of a tz-aware event matches the existing row.
                    # The old code stripped tzinfo WITHOUT converting to UTC
                    # (wall clock), while storage converts to UTC first, so
                    # every re-import of a TZID event created a duplicate.
                    naive_src = _ics_naive_dtstart(src_dtstart)
                    existing = (
                        db.query(CalendarEvent)
                        .filter(
                            CalendarEvent.calendar_id == target_cal.id,
                            CalendarEvent.dtstart == naive_src,
                            CalendarEvent.summary == str(comp.get("summary", "")),
                        )
                        .first()
                    )
                    if existing:
                        skipped += 1
                        continue

                dt_val = dtstart.dt
                all_day = isinstance(dt_val, date) and not isinstance(dt_val, datetime)
                # For timed events, preserve the source timezone by converting
                # to UTC before stripping tzinfo (DB stores naive). We mark
                # the row with is_utc=True so the serializer adds the Z
                # suffix on output — without this, the frontend would parse
                # the naive ISO as the user's CURRENT local, which is exactly
                # the bug where imported events fire reminders at wrong times.
                from datetime import timezone as _tz
                row_is_utc = False
                if all_day:
                    start_dt = datetime(dt_val.year, dt_val.month, dt_val.day)
                    dtend = comp.get("dtend")
                    end_dt = datetime(dtend.dt.year, dtend.dt.month, dtend.dt.day) if dtend else start_dt + timedelta(days=1)
                else:
                    if hasattr(dt_val, 'tzinfo') and dt_val.tzinfo is not None:
                        start_dt = dt_val.astimezone(_tz.utc).replace(tzinfo=None)
                        row_is_utc = True
                    else:
                        start_dt = dt_val
                    dtend = comp.get("dtend")
                    if dtend:
                        d_end = dtend.dt
                        if hasattr(d_end, 'tzinfo') and d_end.tzinfo is not None:
                            end_dt = d_end.astimezone(_tz.utc).replace(tzinfo=None)
                        else:
                            end_dt = d_end
                    else:
                        end_dt = start_dt + timedelta(hours=1)

                ev = CalendarEvent(
                    uid=uid_val,
                    calendar_id=target_cal.id,
                    summary=str(comp.get("summary", "")),
                    description=str(comp.get("description", "")),
                    location=str(comp.get("location", "")),
                    dtstart=start_dt,
                    dtend=end_dt,
                    all_day=all_day,
                    is_utc=row_is_utc,
                    rrule=(comp.get("rrule").to_ical().decode() if comp.get("rrule") else ""),
                )
                db.add(ev)
                imported += 1

            db.commit()
            return {
                "ok": True,
                "imported": imported,
                "skipped": skipped,
                "calendar": cal_display,
                "calendar_id": target_cal.id,
            }
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error("Failed to import ICS: %s", e)
            raise HTTPException(500, "Failed to import ICS")
        finally:
            db.close()

    @router.get("/export/{cal_id}")
    async def export_ics(request: Request, cal_id: str):
        """Export a calendar as .ics file."""
        from fastapi.responses import Response

        owner = _require_user(request)
        db = SessionLocal()
        try:
            cal = _get_or_404_calendar(db, cal_id, owner)
            events = db.query(CalendarEvent).filter(
                CalendarEvent.calendar_id == cal_id,
                CalendarEvent.status != "cancelled",
            ).all()

            lines = [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//Odysseus//Calendar//EN",
                f"X-WR-CALNAME:{_ics_escape(cal.name)}",
            ]
            for ev in events:
                lines.append("BEGIN:VEVENT")
                lines.append(f"UID:{ev.uid}")
                lines.append(f"SUMMARY:{_ics_escape(ev.summary or '')}")
                if ev.all_day:
                    lines.append(f"DTSTART;VALUE=DATE:{ev.dtstart.strftime('%Y%m%d')}")
                    lines.append(f"DTEND;VALUE=DATE:{ev.dtend.strftime('%Y%m%d')}")
                else:
                    _dt_suffix = "Z" if getattr(ev, "is_utc", False) else ""
                    lines.append(f"DTSTART:{ev.dtstart.strftime('%Y%m%dT%H%M%S')}{_dt_suffix}")
                    lines.append(f"DTEND:{ev.dtend.strftime('%Y%m%dT%H%M%S')}{_dt_suffix}")
                if ev.description:
                    lines.append(f"DESCRIPTION:{_ics_escape(ev.description)}")
                if ev.location:
                    lines.append(f"LOCATION:{_ics_escape(ev.location)}")
                if ev.rrule:
                    lines.append(f"RRULE:{ev.rrule}")
                lines.append("END:VEVENT")
            lines.append("END:VCALENDAR")

            ics_data = "\r\n".join(lines)
            download_name = _safe_ics_filename(cal.name)
            return Response(
                content=ics_data,
                media_type="text/calendar",
                headers={
                    "Content-Disposition": f'attachment; filename="{download_name}"',
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to export ICS: %s", e)
            raise HTTPException(500, "Failed to export ICS")
        finally:
            db.close()

    @router.post("/quick-parse")
    async def quick_parse(request: Request):
        """Parse a natural-language event description into structured fields.

        Input: {"text": "lunch with sara friday 1pm downtown", "tz": "America/New_York"}
        Output: {"ok": true, "event": {"summary", "dtstart", "dtend",
                  "all_day", "location", "description"}, "confidence": 0.0-1.0}

        Anchored on the server's current date/time so phrases like
        "tomorrow", "next Tuesday", "in 30 minutes" resolve correctly.
        Uses the "utility" endpoint (small / fast model) to keep latency low.
        """
        owner = _require_user(request)
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        from src.text_helpers import strip_think
        import json as _json
        import re as _re

        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        from src.user_time import (
            clear_user_time_context,
            current_datetime_prompt,
            now_user_local,
            set_user_tz_name,
            set_user_tz_offset,
        )

        clear_user_time_context()
        tz_hint = (body.get("tz") or "").strip()
        if body.get("tz_offset") is not None:
            set_user_tz_offset(body.get("tz_offset"))
        if tz_hint:
            set_user_tz_name(tz_hint)

        url, model, headers = resolve_endpoint("utility", owner=owner or None)
        if not url:
            url, model, headers = resolve_endpoint("default", owner=owner or None)
        if not url or not model:
            return {"ok": False, "error": "No LLM endpoint configured"}

        now = now_user_local()
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
        # The model gets only the schema it needs to fill out; we re-validate
        # everything client-side too.
        system_prompt = (
            current_datetime_prompt()
            + "You are a calendar event parser. Read the user's one-line "
            "description and emit STRICT JSON describing the event. "
            f"The current user-local timestamp is {now_iso}. "
            + "Resolve relative dates (\"tomorrow\", \"friday\", \"next monday\", "
              "\"in 30 minutes\") against today. Default duration is 60 minutes "
              "when no end time is given. If the text mentions a date with no "
              "time, treat it as an all-day event.\n\n"
              "Output ONLY this JSON shape, nothing else:\n"
              "{\n"
              '  "summary": "<event title, capitalized>",\n'
              '  "dtstart": "<YYYY-MM-DDTHH:MM:00>",\n'
              '  "dtend":   "<YYYY-MM-DDTHH:MM:00>",\n'
              '  "all_day": <true|false>,\n'
              '  "location": "<place or empty>",\n'
              '  "description": "",\n'
              '  "confidence": <0.0-1.0>\n'
              "}\n"
              "For all-day events use \"YYYY-MM-DD\" (no time) for both fields."
        )

        try:
            raw = await llm_call_async(
                url=url, model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                headers=headers,
                temperature=0.0,
                max_tokens=512,
                timeout=20,
            )
        except Exception as e:
            return {"ok": False, "error": f"LLM call failed: {e}"}

        cleaned = strip_think(raw or "", prose=False, prompt_echo=True)
        cleaned = _re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=_re.MULTILINE).strip()
        m = _re.search(r"\{[\s\S]*\}", cleaned)
        if not m:
            return {"ok": False, "error": "Could not extract JSON", "raw": cleaned[:400]}
        try:
            parsed = _json.loads(m.group())
        except Exception as e:
            return {"ok": False, "error": f"Invalid JSON: {e}", "raw": cleaned[:400]}

        # Light validation / defaults so the frontend can trust the shape.
        summary = (parsed.get("summary") or text)[:200]
        # Strip stale relative/absolute time tokens that the LLM (or the
        # user's raw input) sometimes leaks into the summary — these
        # would otherwise be displayed verbatim in reminder notifications
        # that fire much later, when "in 29 min" is no longer true. The
        # actual timing lives in dtstart/dtend.
        summary = _re.sub(r'\bin\s+\d+\s*(min|minute|hour|hr|day)s?\b', '', summary, flags=_re.IGNORECASE)
        summary = _re.sub(r'\(\s*\d{1,2}:\d{2}\s*\)', '', summary)
        summary = _re.sub(r'\b\d{1,2}(:\d{2})?\s*(am|pm)\b', '', summary, flags=_re.IGNORECASE)
        summary = _re.sub(r'\s+@\s+(?=\d)', ' ', summary)  # drop "@" when right before a time
        summary = _re.sub(r'\s+', ' ', summary).strip(' -—,@')
        all_day = bool(parsed.get("all_day"))
        dtstart = (parsed.get("dtstart") or "").strip()
        dtend   = (parsed.get("dtend") or "").strip()
        # Force naive-local on LLM output. The model is anchored on the
        # user's local "now" via the system prompt, so its emitted
        # datetime is already meant to be the user's wall-clock time.
        # Some models append `Z` or a tz offset anyway, which would
        # make `_parse_dt_pair` flag the row as UTC and shift the
        # displayed time forward by the user's tz offset. Strip any
        # trailing tz marker so the time is stored exactly as the LLM
        # wrote it.
        def _strip_tz(s):
            if not s:
                return s
            s = s.strip()
            # Strip "Z"
            if s.endswith('Z') or s.endswith('z'):
                s = s[:-1]
            # Strip "+HH:MM" / "-HH:MM" if it followed a T-time
            s = _re.sub(r'[+-]\d{2}:?\d{2}$', '', s)
            return s
        dtstart = _strip_tz(dtstart)
        dtend   = _strip_tz(dtend)
        if not dtstart:
            return {"ok": False, "error": "Model did not produce a start time", "raw": cleaned[:400]}
        if not dtend:
            # Auto-fill +60 min for timed events; +0 for all-day (single-day).
            try:
                if all_day:
                    dtend = dtstart
                else:
                    dt = datetime.fromisoformat(dtstart)
                    dtend = (dt + timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:00")
            except Exception:
                dtend = dtstart

        return {
            "ok": True,
            "event": {
                "summary": summary,
                "dtstart": dtstart,
                "dtend": dtend,
                "all_day": all_day,
                "location": (parsed.get("location") or "").strip()[:200],
                "description": (parsed.get("description") or "").strip()[:2000],
            },
            "confidence": float(parsed.get("confidence", 0.7) or 0.7),
        }

    return router
