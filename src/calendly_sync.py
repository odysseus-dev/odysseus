"""Calendly → local SQLite sync.

Calendly doesn't publish a single account-wide ICS feed for your bookings, so
unlike the Google path we go through the Calendly v2 REST API. The user pastes
a Personal Access Token (Calendly → Integrations → API & Webhooks → personal
access tokens); we look up their user URI and pull `scheduled_events` in the
sync window, one-way (remote → local).

Auth is a bearer token in prefs — same "credential in, read-only pull out"
shape as the CalDAV and Google paths. No OAuth app / redirect flow needed.

API reference: https://developer.calendly.com/api-docs (GET /users/me,
GET /scheduled_events). Times come back as RFC3339 with a `Z`, which we store
as naive-UTC with is_utc=True so the frontend renders them in local time.
"""

import asyncio
import logging
from datetime import datetime, timezone

from src.calendar_sync_common import (
    empty_result, get_or_create_cal, prune_stale, stable_cal_id, upsert_event,
    window,
)

logger = logging.getLogger(__name__)

_API_BASE = "https://api.calendly.com"
# Cap pagination so a misbehaving token/account can't loop forever. 100 events
# per page × 20 pages = 2000 events covers any realistic 15-month window.
_MAX_PAGES = 20


def _parse_rfc3339(s: str):
    """Parse a Calendly RFC3339 timestamp ("2026-01-02T15:04:05Z" or with an
    offset) to naive-UTC. Returns None on failure."""
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _location_str(loc) -> str:
    """Flatten Calendly's polymorphic `location` object into a display string.

    It can be a physical address, a phone number, a Zoom/Meet/Teams join URL,
    or a custom string depending on the event type's location kind.
    """
    if not isinstance(loc, dict):
        return ""
    for key in ("location", "join_url", "data", "type"):
        val = loc.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


async def _api_get(client, url: str, token: str, params=None) -> dict:
    r = await client.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params=params,
    )
    r.raise_for_status()
    return r.json()


async def _fetch_events(token: str) -> tuple:
    """Return (display_name, [event dicts]) from the Calendly API. Raises on
    HTTP/network error so the caller surfaces a useful message."""
    import httpx

    start, end = window()
    min_start = start.replace(microsecond=0).isoformat() + "Z"
    max_start = end.replace(microsecond=0).isoformat() + "Z"

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as cx:
        me = await _api_get(cx, f"{_API_BASE}/users/me", token)
        resource = (me or {}).get("resource", {}) or {}
        user_uri = resource.get("uri")
        display_name = (resource.get("name") or "").strip() or "Calendly"
        if not user_uri:
            raise ValueError("Calendly /users/me returned no user URI")

        events = []
        params = {
            "user": user_uri,
            "min_start_time": min_start,
            "max_start_time": max_start,
            "count": 100,
            "sort": "start_time:asc",
        }
        next_url = f"{_API_BASE}/scheduled_events"
        pages = 0
        while next_url and pages < _MAX_PAGES:
            data = await _api_get(cx, next_url, token, params=params)
            events.extend(data.get("collection", []) or [])
            pagination = data.get("pagination", {}) or {}
            next_url = pagination.get("next_page")
            # next_page is a fully-qualified URL with the cursor baked in, so
            # the original query params must NOT be re-sent on the next hop.
            params = None
            pages += 1

    return display_name, events


def _store_events(owner: str, display_name: str, events: list) -> dict:
    """Upsert fetched Calendly events into local DB. Synchronous (DB work) —
    runs in a threadpool."""
    from core.database import SessionLocal

    result = empty_result()
    cal_id = stable_cal_id("calendly", owner)
    start, end = window()

    db = SessionLocal()
    try:
        get_or_create_cal(db, owner, cal_id, display_name, color="#006BFF", source="calendly")
        result["calendars"] = 1

        seen_uids = set()
        for ev in events:
            uri = ev.get("uri")
            if not uri:
                continue
            start_dt = _parse_rfc3339(ev.get("start_time"))
            end_dt = _parse_rfc3339(ev.get("end_time"))
            if not start_dt:
                continue
            if not end_dt:
                from datetime import timedelta
                end_dt = start_dt + timedelta(hours=1)

            # Cancelled invitees leave the slot in status "canceled"; keep the
            # local copy in sync by letting it upsert and rely on prune for
            # ones that fully disappear. Skip clearly out-of-window rows so the
            # window-scoped prune stays consistent.
            if end_dt < start or start_dt > end:
                continue

            uid_val = f"{cal_id}:{uri.rsplit('/', 1)[-1]}"
            seen_uids.add(uid_val)

            status = (ev.get("status") or "").lower()
            summary = str(ev.get("name") or "Calendly event")
            if status in ("canceled", "cancelled"):
                summary = f"(Canceled) {summary}"

            upsert_event(db, cal_id, {
                "uid": uid_val,
                "summary": summary,
                "description": "",
                "location": _location_str(ev.get("location")),
                "dtstart": start_dt,
                "dtend": end_dt,
                "all_day": False,
                "is_utc": True,  # Calendly always returns tz-aware instants
                "rrule": "",
            })
            result["events"] += 1
        db.commit()

        result["deleted"] = prune_stale(db, cal_id, start, end, seen_uids)
        db.commit()
    except Exception as e:
        logger.exception("Calendly store failed")
        result["errors"].append(str(e)[:200])
        db.rollback()
    finally:
        db.close()

    return result


async def sync_calendly(owner: str) -> dict:
    """Pull the user's Calendly scheduled events into local DB. Returns counts +
    errors; no-ops with a clear error if no token is configured."""
    from routes.prefs_routes import _load_for_user

    cfg = (_load_for_user(owner) or {}).get("calendly", {}) or {}
    token = (cfg.get("token") or "").strip()
    if not token:
        return empty_result("Calendly is not configured")
    try:
        display_name, events = await _fetch_events(token)
    except Exception as e:
        logger.info("Calendly fetch failed: %s", e)
        return empty_result(f"Calendly API error: {str(e)[:160]}")
    try:
        return await asyncio.to_thread(_store_events, owner, display_name, events)
    except Exception as e:
        logger.exception("Calendly sync raised")
        return empty_result(str(e)[:200])
