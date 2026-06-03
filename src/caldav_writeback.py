"""CalDAV write-back: push local create/update/delete out to the remote (#800).

``src/caldav_sync.py`` is a one-way pull (remote → local). So events created,
edited, or deleted in Odysseus on a CalDAV-backed calendar only changed the local
SQLite copy and never reached the server (iCloud/Nextcloud/Radicale/Fastmail) —
they'd silently disappear on the next pull and never show on the user's phone.

This adds the missing write half. The remote calendar URL isn't stored locally
(the local calendar id is a one-way hash of it), so we re-discover the remote
calendar by matching that same hash, then PUT/DELETE the VEVENT by its UID via
the `caldav` lib. Writes are best-effort: the local DB stays the source of truth,
and a remote failure is reported, never fatal to the local operation.

The pure pieces (``build_event_ical``, ``find_remote_calendar``, ``push_event``)
take their inputs by argument so they unit-test against a fake client with no
network.
"""

import asyncio
import logging
from datetime import timezone

logger = logging.getLogger(__name__)


def _stable_cal_id(remote_url: str) -> str:
    # Reuse the sync module's hashing so a local CalDAV calendar id maps back to
    # the same remote URL it was pulled from.
    from src.caldav_sync import _stable_cal_id as _sync_id
    return _sync_id(remote_url)


def build_event_ical(ev: dict) -> str:
    """Serialize a local event dict to a VCALENDAR/VEVENT iCalendar string.

    ``ev`` keys: uid, summary, description, location, dtstart (datetime),
    dtend (datetime), all_day (bool), is_utc (bool), rrule (str).
    Mirrors how the pull path interprets is_utc/all_day so a round-trip is stable.
    """
    from icalendar import Calendar, Event as iEvent
    from icalendar.prop import vRecur

    cal = Calendar()
    cal.add("prodid", "-//Odysseus//CalDAV write-back//EN")
    cal.add("version", "2.0")

    ve = iEvent()
    ve.add("uid", ev["uid"])
    ve.add("summary", ev.get("summary") or "")
    if ev.get("description"):
        ve.add("description", ev["description"])
    if ev.get("location"):
        ve.add("location", ev["location"])

    dtstart = ev["dtstart"]
    dtend = ev["dtend"]
    if ev.get("all_day"):
        ve.add("dtstart", dtstart.date())
        ve.add("dtend", dtend.date())
    elif ev.get("is_utc"):
        # Stored as naive-UTC instants — re-attach UTC so the server gets a Z time.
        ve.add("dtstart", dtstart.replace(tzinfo=timezone.utc))
        ve.add("dtend", dtend.replace(tzinfo=timezone.utc))
    else:
        # Legacy naive-local ("floating") time — emit without a TZ.
        ve.add("dtstart", dtstart)
        ve.add("dtend", dtend)

    if ev.get("rrule"):
        try:
            ve.add("rrule", vRecur.from_ical(ev["rrule"]))
        except Exception:
            logger.debug("CalDAV write-back: skipping unparseable rrule %r", ev.get("rrule"))

    cal.add_component(ve)
    return cal.to_ical().decode("utf-8")


def find_remote_calendar(calendars, local_cal_id: str):
    """Find the remote calendar whose URL hashes to ``local_cal_id``, or None."""
    for cal in calendars:
        try:
            if _stable_cal_id(str(cal.url)) == local_cal_id:
                return cal
        except Exception:
            continue
    return None


def push_event(calendars, local_cal_id: str, ev: dict, *, delete: bool = False) -> dict:
    """Create/update (or delete) ``ev`` on the matching remote calendar.

    Returns ``{"ok": bool, ...}``. ``calendars`` is the discovered caldav
    calendar list (injected so this is unit-testable with fakes).
    """
    uid = (ev or {}).get("uid") if isinstance(ev, dict) else None
    if not uid:
        return {"ok": False, "error": "event uid is required"}

    remote = find_remote_calendar(calendars, local_cal_id)
    if remote is None:
        return {"ok": False, "error": "remote calendar not found"}

    try:
        existing = remote.event_by_uid(uid)
    except Exception:
        existing = None

    if delete:
        if existing is None:
            return {"ok": True, "note": "already absent on remote"}
        existing.delete()
        return {"ok": True}

    ical = build_event_ical(ev)
    if existing is not None:
        existing.data = ical
        existing.save()
        return {"ok": True, "updated": True}
    remote.save_event(ical)
    return {"ok": True, "created": True}


def _discover_calendars(client):
    """Discover the principal's calendars, falling back to the URL itself —
    same strategy as the pull path."""
    from caldav.lib.error import AuthorizationError, NotFoundError
    try:
        return client.principal().calendars()
    except (AuthorizationError, NotFoundError):
        raise
    except Exception:
        try:
            return [client.calendar(url=str(client.url))]
        except Exception:
            return []


def _writeback_blocking(local_cal_id, ev, delete, url, username, password) -> dict:
    import caldav
    client = caldav.DAVClient(url=url, username=username, password=password)
    calendars = _discover_calendars(client)
    if not calendars:
        return {"ok": False, "error": "no remote calendars discovered"}
    return push_event(calendars, local_cal_id, ev, delete=delete)


async def writeback_event(owner: str, calendar_source: str, calendar_id: str,
                          ev: dict, *, delete: bool = False) -> dict:
    """Best-effort push of a local change to the remote CalDAV server.

    No-ops (``{"skipped": ...}``) when the calendar isn't CalDAV-backed or no
    credentials are configured. Never raises — a remote failure is logged and
    returned, the local DB remaining the source of truth.
    """
    if calendar_source != "caldav":
        return {"skipped": "not a caldav calendar"}
    try:
        from routes.prefs_routes import _load_for_user
        from src.secret_storage import decrypt
        cfg = (_load_for_user(owner) or {}).get("caldav", {}) or {}
        url = (cfg.get("url") or "").strip()
        user = (cfg.get("username") or "").strip()
        # Stored encrypted by routes/calendar_routes; decrypt before use so
        # the remote sees the real password (decrypt is a no-op on legacy
        # plaintext). The pull path src/caldav_sync.py already does this.
        pw = decrypt(cfg.get("password") or "")
        if not (url and user and pw):
            return {"skipped": "caldav not configured"}
        result = await asyncio.to_thread(_writeback_blocking, calendar_id, ev, delete, url, user, pw)
        if not result.get("ok"):
            logger.warning("CalDAV write-back did not apply: %s", result.get("error") or result)
        return result
    except Exception as e:
        logger.exception("CalDAV write-back raised")
        return {"ok": False, "error": str(e)[:200]}
