"""Google Calendar → local SQLite sync.

Google exposes every calendar as a private ICS feed — the "Secret address in
iCal format" URL under Settings → "Integrate calendar". We pull that feed over
HTTPS and upsert its VEVENTs into the local calendar store, one-way (remote →
local), exactly like the CalDAV path.

Why the secret-iCal URL instead of OAuth:
- It needs no Google Cloud OAuth app, client id/secret, or consent screen —
  the user pastes one URL and sync works immediately, which matches how every
  other pull integration in Odysseus is configured (a credential in prefs, no
  redirect dance).
- It's read-only by construction, so there's no risk of the sync mutating the
  user's Google calendar. Event creation still happens against local calendars.

The same code path handles any ICS-over-HTTP feed (Outlook "Publish", iCloud
public calendars, a raw `webcal://` link), so the module is provider-agnostic
under the hood even though the UI frames it as "Google Calendar".

The `icalendar` + `httpx` deps are already required by the CalDAV path.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from src.calendar_sync_common import (
    empty_result, get_or_create_cal, prune_stale, stable_cal_id, to_utc_naive,
    upsert_event, window,
)

logger = logging.getLogger(__name__)

# Google's secret-iCal links live under calendar.google.com/calendar/ical/...
# We don't hard-require that host (the same path serves Outlook/iCloud feeds),
# but we do reject obviously-wrong inputs early.
_MAX_FEED_BYTES = 10 * 1024 * 1024  # 10 MB — same cap as the .ics upload path.


def _normalize_url(url: str) -> str:
    """webcal:// is just http(s) with a different scheme hint used by calendar
    clients — rewrite it so httpx can fetch it."""
    url = (url or "").strip()
    if url.startswith("webcal://"):
        return "https://" + url[len("webcal://"):]
    if url.startswith("webcals://"):
        return "https://" + url[len("webcals://"):]
    return url


async def fetch_feed(url: str) -> bytes:
    """Fetch the raw ICS document. Raises on HTTP / network error so callers
    can surface a useful message. Caps the body to avoid OOM on a hostile or
    runaway feed."""
    import httpx

    url = _normalize_url(url)
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as cx:
        r = await cx.get(url, headers={"Accept": "text/calendar, */*"})
        r.raise_for_status()
        content = r.content
        if len(content) > _MAX_FEED_BYTES:
            raise ValueError(f"Feed too large (> {_MAX_FEED_BYTES // (1024 * 1024)} MB)")
        return content


def _sync_blocking(owner: str, url: str, feed_bytes: bytes) -> dict:
    """Parse the already-fetched ICS bytes and upsert into local DB. Synchronous
    (DB + icalendar parsing) — intended to run in a threadpool."""
    from icalendar import Calendar as iCal

    from core.database import SessionLocal

    result = empty_result()

    try:
        ical = iCal.from_ical(feed_bytes)
    except Exception as e:
        result["errors"].append(f"Could not parse calendar feed: {e}")
        return result

    # Prefer the feed's own name (X-WR-CALNAME) for the local calendar; the
    # secret-iCal URL itself is opaque and not user-friendly.
    display_name = str(ical.get("X-WR-CALNAME", "") or "").strip() or "Google Calendar"
    cal_id = stable_cal_id("gcal", url)
    start, end = window()

    db = SessionLocal()
    try:
        local_cal = get_or_create_cal(
            db, owner, cal_id, display_name, color="#4285F4", source="gcal",
        )
        result["calendars"] = 1

        seen_uids = set()
        for comp in ical.walk():
            if comp.name != "VEVENT":
                continue
            dtstart_p = comp.get("dtstart")
            if not dtstart_p:
                continue

            uid_val = str(comp.get("uid", "")) or str(uuid.uuid4())
            # Namespace the UID by this calendar so the same Google event UID
            # appearing in two subscribed feeds (or a CalDAV mirror) doesn't
            # collide on the shared CalendarEvent.uid primary key.
            uid_val = f"{cal_id}:{uid_val}"
            seen_uids.add(uid_val)

            start_dt, all_day = to_utc_naive(dtstart_p.dt)

            dtend_p = comp.get("dtend")
            if dtend_p:
                end_dt, _ = to_utc_naive(dtend_p.dt)
            elif all_day:
                end_dt = start_dt + timedelta(days=1)
            else:
                end_dt = start_dt + timedelta(hours=1)

            # Skip events entirely outside the sync window so prune_stale's
            # window-scoped delete stays consistent with what we inserted.
            if end_dt < start or start_dt > end:
                seen_uids.discard(uid_val)
                continue

            row_is_utc = (
                not all_day
                and isinstance(dtstart_p.dt, datetime)
                and dtstart_p.dt.tzinfo is not None
            )

            upsert_event(db, cal_id, {
                "uid": uid_val,
                "summary": str(comp.get("summary", "")),
                "description": str(comp.get("description", "")),
                "location": str(comp.get("location", "")),
                "dtstart": start_dt,
                "dtend": end_dt,
                "all_day": all_day,
                "is_utc": row_is_utc,
                "rrule": (comp.get("rrule").to_ical().decode() if comp.get("rrule") else ""),
            })
            result["events"] += 1
        db.commit()

        result["deleted"] = prune_stale(db, cal_id, start, end, seen_uids)
        db.commit()
    except Exception as e:
        logger.exception("Google Calendar sync failed")
        result["errors"].append(str(e)[:200])
        db.rollback()
    finally:
        db.close()

    return result


async def sync_gcal(owner: str) -> dict:
    """Pull the user's configured Google Calendar iCal feed into local DB.
    Returns counts + errors; no-ops with a clear error if not configured."""
    from routes.prefs_routes import _load_for_user

    cfg = (_load_for_user(owner) or {}).get("gcal", {}) or {}
    url = (cfg.get("ics_url") or "").strip()
    if not url:
        return empty_result("Google Calendar is not configured")
    try:
        feed_bytes = await fetch_feed(url)
    except Exception as e:
        logger.info("Google Calendar feed fetch failed: %s", e)
        return empty_result(f"Could not fetch feed: {str(e)[:160]}")
    try:
        return await asyncio.to_thread(_sync_blocking, owner, url, feed_bytes)
    except Exception as e:
        logger.exception("Google Calendar sync raised")
        return empty_result(str(e)[:200])
