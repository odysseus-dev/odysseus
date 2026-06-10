"""
src/tools/calendar_tools.py

Calendar / research / contacts tool implementations extracted from
tool_implementations.py (P8-T5): manage_calendar (CalDAV-backed local event
CRUD), manage_research / trigger_research (deep-research library + kickoff via
the internal API), and resolve_contact / manage_contact. Calendar date parsing
helpers come from src.services.calendar_helpers; the internal API base/headers
are reached through the tool_implementations aggregator (they live in
model_tools after P8-T4). Shared _parse_tool_args is routed through the
aggregator too so test patches still bind.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from src.constants import DEEP_RESEARCH_DIR
from src.tools._shared import ti as _ti
from src.services.calendar_helpers import (
    parse_due_for_user,
    _ensure_default_calendar,
    _parse_dt,
    _parse_dt_pair,
    _resolve_base_uid,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Calendar tool — CalDAV-backed event CRUD
# ---------------------------------------------------------------------------

async def do_manage_calendar(content: str, owner: Optional[str] = None) -> Dict:
    """Handle manage_calendar tool calls: list/create/update/delete calendar events (local SQLite)."""
    from datetime import datetime, timedelta
    from core.database import SessionLocal, CalendarCal, CalendarEvent, Note
    import uuid as _uuid

    try:
        args = _ti._parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    # ── Batch normalization ──
    # Some models (e.g. deepseek-v4-flash) emit {"events": [{...}, ...]}
    # instead of individual create_event calls. Iterate and create each.
    if isinstance(args.get("events"), list) and not args.get("action"):
        results = []
        for ev in args["events"]:
            if not isinstance(ev, dict):
                continue
            # Normalize start/end from {dateTime: "..."} object to flat string
            for field, target in [("start", "dtstart"), ("end", "dtend")]:
                val = ev.pop(field, None)
                if val and target not in ev:
                    ev[target] = val.get("dateTime", val) if isinstance(val, dict) else val
            ev.setdefault("action", "create_event")
            r = await do_manage_calendar(json.dumps(ev), owner=owner)
            results.append(r)
        created = [r for r in results if r.get("exit_code") == 0 and not r.get("error")]
        failed = [r for r in results if r.get("error")]

        if not results:
            return {"error": "No events to create", "exit_code": 1}

        # Surface both successes and failures
        parts = []
        if created:
            summaries = [r.get("response", "") for r in created]
            parts.append(f"Created {len(created)} event(s):\n" + "\n".join(summaries))
        if failed:
            first_error = failed[0].get("error", "Unknown error")
            parts.append(f"Failed to create {len(failed)} event(s). First error: {first_error}")

        response = "\n\n".join(parts)
        # Non-zero exit code for partial or total failure
        exit_code = 0 if not failed else 1
        return {"response": response, "exit_code": exit_code, "created_count": len(created), "failed_count": len(failed)}


    # Normalize action — some models emit hyphens ("list-calendars") instead
    # of underscores. Treat them as equivalent so we don't bounce a
    # cosmetic typo back to the model and waste a round-trip. Also accept
    # short forms (`create`, `update`, `delete`) as aliases for the
    # full `<verb>_event` names — models keep emitting the short forms.
    action = (args.get("action") or "list_events").replace("-", "_").strip().lower()
    _ACTION_ALIASES = {
        "create": "create_event",
        "update": "update_event",
        "delete": "delete_event",
        "list": "list_events",
    }
    action = _ACTION_ALIASES.get(action, action)
    db = SessionLocal()

    def _calendar_query():
        q = db.query(CalendarCal)
        if owner is not None:
            q = q.filter(CalendarCal.owner == owner)
        return q

    def _event_query():
        q = db.query(CalendarEvent).join(CalendarCal)
        if owner is not None:
            q = q.filter(CalendarCal.owner == owner)
        return q

    def _reminder_minutes(raw_args) -> Optional[int]:
        raw = (
            raw_args.get("reminder_minutes")
            or raw_args.get("remind_before_minutes")
            or raw_args.get("alarm_minutes")
            or raw_args.get("reminder")
            or raw_args.get("alarm")
        )
        if raw in (None, ""):
            desc = str(raw_args.get("description") or "")
            if re.search(r"\b(remind|reminder|alarm)\b", desc, re.I):
                raw = desc
        if raw in (None, "", False):
            return None
        if raw is True:
            return 10
        if isinstance(raw, (int, float)):
            return max(0, int(raw))
        text = str(raw).strip().lower()
        if text in {"none", "no", "off", "false"}:
            return None
        m = re.search(r"(\d+)\s*(?:m|min|minute|minutes)\b", text)
        if m:
            return max(0, int(m.group(1)))
        m = re.search(r"(\d+)\s*(?:h|hr|hour|hours)\b", text)
        if m:
            return max(0, int(m.group(1)) * 60)
        if text.isdigit():
            return max(0, int(text))
        return None

    def _event_description(raw_args, minutes_before: Optional[int]) -> str:
        desc = str(raw_args.get("description", "") or "")
        if minutes_before is None:
            return desc
        reminder_only = re.compile(
            r"^\s*(?:remind(?:er)?|alarm)\s*:?\s*\d+\s*"
            r"(?:m|min|minute|minutes|h|hr|hour|hours)\b.*$",
            re.I,
        )
        return "" if reminder_only.match(desc) else desc

    def _parse_event_dt(raw: str) -> tuple[datetime, bool]:
        """Parse agent event datetimes in the user's timezone when available."""
        return _parse_dt_pair(parse_due_for_user(raw))

    def _first_nonempty_arg(*names: str):
        for name in names:
            value = args.get(name)
            if value not in (None, ""):
                return value
        return None

    def _create_calendar_reminder(summary: str, location: str, dtstart: datetime,
                                  all_day: bool, minutes_before: int,
                                  is_utc: bool = False) -> tuple[Optional[str], Optional[str]]:
        remind_at = dtstart - timedelta(minutes=minutes_before)
        now = datetime.utcnow() if is_utc else datetime.now()
        if dtstart <= now:
            return None, "event already passed"
        if remind_at <= now:
            # If the requested "before" time already passed but the event is
            # still upcoming, create an immediate Note reminder instead of
            # silently dropping it.
            remind_at = now
        start_fmt = dtstart.strftime("%a %b %d") if all_day else dtstart.strftime("%a %b %d %H:%M")
        loc = f" @ {location}" if location else ""
        text = f"{summary}{loc} — {start_fmt}"
        due_date = remind_at.isoformat() + ("Z" if is_utc else "")
        expected_title = f"Reminder: {summary}"
        existing_q = db.query(Note).filter(
            Note.archived == False,  # noqa: E712
            Note.due_date == due_date,
        )
        if owner is not None:
            existing_q = existing_q.filter(Note.owner == owner)
        target_title = re.sub(r"^\s*reminder\s*:\s*", "", expected_title.strip().lower())
        for existing in existing_q.limit(25).all():
            existing_title = re.sub(r"^\s*reminder\s*:\s*", "", (existing.title or "").strip().lower())
            if existing_title == target_title:
                return existing.id, "duplicate reminder already exists"
        note = Note(
            id=str(_uuid.uuid4()),
            owner=owner,
            title=expected_title,
            items=json.dumps([{"text": text, "done": False, "checked": False}]),
            note_type="todo",
            label="calendar",
            due_date=due_date,
            source="calendar",
        )
        db.add(note)
        return note.id, None

    try:
        if action == "list_calendars":
            _ensure_default_calendar(db, owner)
            cals = _calendar_query().all()
            result = [{"name": c.name, "href": c.id} for c in cals]
            if result:
                lines = [f"Found {len(result)} calendar(s):"]
                for c in result:
                    lines.append(f"- {c['name']} ({c['href'][:8]})")
                response_text = "\n".join(lines)
            else:
                response_text = "No calendars found."
            return {"response": response_text, "calendars": result, "exit_code": 0}

        elif action == "list_events":
            try:
                start_raw = _first_nonempty_arg(
                    "start", "start_date", "range_start", "from", "dtstart", "since"
                )
                end_raw = _first_nonempty_arg(
                    "end", "end_date", "range_end", "to", "dtend", "until"
                )
                if start_raw:
                    start_dt = _parse_dt(start_raw)
                else:
                    start_dt = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                if end_raw:
                    end_dt = _parse_dt(end_raw)
                else:
                    end_dt = start_dt + timedelta(days=14)
            except ValueError as e:
                return {"error": f"Invalid date format: {e}", "exit_code": 1}

            q = _event_query().filter(
                CalendarEvent.dtstart < end_dt,
                CalendarEvent.dtend > start_dt,
                CalendarEvent.status != "cancelled",
            )
            calendar_filter = args.get("calendar")
            if calendar_filter:
                q = q.filter(
                    (CalendarEvent.calendar_id == calendar_filter) |
                    (CalendarCal.name == calendar_filter)
                )
            rows = q.order_by(CalendarEvent.dtstart).all()
            events = []
            for ev in rows:
                if ev.all_day:
                    s, e = ev.dtstart.strftime("%Y-%m-%d"), ev.dtend.strftime("%Y-%m-%d")
                else:
                    suffix = "Z" if getattr(ev, "is_utc", False) else ""
                    s, e = ev.dtstart.isoformat() + suffix, ev.dtend.isoformat() + suffix
                events.append({
                    "uid": ev.uid, "summary": ev.summary or "", "dtstart": s, "dtend": e,
                    "all_day": ev.all_day, "description": ev.description or "",
                    "location": ev.location or "",
                    "calendar": ev.calendar.name if ev.calendar else "",
                    "calendar_href": ev.calendar_id,
                    "event_type": ev.event_type or "",
                    "importance": ev.importance or "normal",
                })
            if not events:
                response_text = f"No events between {start_dt.date().isoformat()} and {end_dt.date().isoformat()}."
            else:
                lines = [f"Found {len(events)} event(s) between {start_dt.date().isoformat()} and {end_dt.date().isoformat()}:"]
                for ev in events:
                    when = ev["dtstart"]
                    when_str = f"{when} (all day)" if ev.get("all_day") else f"{when} -> {ev.get('dtend', '')}"
                    # Clickable anchor — opens the calendar on the event's day.
                    line = f"- {when_str}: [{ev['summary']}](#event-{ev['uid']})"
                    if ev.get("event_type"):
                        line += f" #{ev['event_type']}"
                    if ev.get("importance") and ev["importance"] != "normal":
                        line += f" !{ev['importance']}"
                    if ev.get("location"):
                        line += f" @ {ev['location']}"
                    if ev.get("calendar"):
                        line += f" ({ev['calendar']})"
                    if ev.get("description"):
                        desc = ev["description"].strip().replace("\n", " ")
                        if len(desc) > 120:
                            desc = desc[:117] + "..."
                        line += f"\n    {desc}"
                    lines.append(line)
                response_text = "\n".join(lines)
            return {"response": response_text, "events": events, "exit_code": 0}

        elif action == "create_event":
            summary = args.get("summary")
            # Accept the various names models like to use for the start
            # field: dtstart (canonical), start, start_time, when.
            dtstart_str = (args.get("dtstart") or args.get("start")
                           or args.get("start_time") or args.get("when"))
            if not summary or not dtstart_str:
                return {"error": "summary and dtstart are required", "exit_code": 1}

            # Accept either an href OR a calendar name/short-id like "Main"
            # or "62e545d8" — saves the model from having to memorize hrefs
            # after a `list_calendars` call returned short prefixes.
            cal_href = args.get("calendar_href") or args.get("calendar")
            cal = None
            if cal_href:
                cal = (_calendar_query()
                       .filter(CalendarCal.id == cal_href)
                       .first())
                if not cal:
                    # Try by name (case-insensitive) or by short-id prefix
                    cal = (_calendar_query()
                           .filter(CalendarCal.name.ilike(cal_href))
                           .first())
                if not cal:
                    cal = (_calendar_query()
                           .filter(CalendarCal.id.like(f"{cal_href}%"))
                           .first())
            if not cal:
                cal = _ensure_default_calendar(db, owner)

            all_day = bool(args.get("all_day", False))
            try:
                dtstart, dtstart_is_utc = _parse_event_dt(dtstart_str)
            except ValueError as e:
                return {"error": f"Could not parse dtstart {dtstart_str!r}: {e}", "exit_code": 1}
            dtend_raw = args.get("dtend") or args.get("end") or args.get("end_time")
            if dtend_raw:
                try:
                    dtend, dtend_is_utc = _parse_event_dt(dtend_raw)
                    dtstart_is_utc = dtstart_is_utc or dtend_is_utc
                except ValueError as e:
                    return {"error": f"Could not parse dtend {dtend_raw!r}: {e}", "exit_code": 1}
            else:
                # Support duration: "1h", "30m", "90min", "1hr30m"
                dur = (args.get("duration") or "").strip().lower()
                delta = None
                if dur:
                    import re as _re_d
                    h = _re_d.search(r'(\d+)\s*(?:h|hr|hours?)', dur)
                    m = _re_d.search(r'(\d+)\s*(?:m|min|minutes?)', dur)
                    secs = (int(h.group(1)) * 3600 if h else 0) + (int(m.group(1)) * 60 if m else 0)
                    if secs > 0:
                        delta = timedelta(seconds=secs)
                if delta is not None:
                    dtend = dtstart + delta
                elif all_day:
                    dtend = dtstart + timedelta(days=1)
                else:
                    dtend = dtstart + timedelta(hours=1)

            # Dedup: if a non-cancelled event with the same title + start time already
            # exists, return its UID instead of creating a fresh copy. Prevents the
            # email triage from multiplying events when several emails reference the
            # same meeting. Compare case-insensitively since LLM-extracted titles
            # can vary in capitalisation.
            from sqlalchemy import func as _func
            existing = (
                _event_query()
                .filter(
                    CalendarEvent.dtstart == dtstart,
                    CalendarEvent.status != "cancelled",
                    _func.lower(CalendarEvent.summary) == summary.lower(),
                )
                .first()
            )
            if existing is not None:
                reminder_note_id = None
                reminder_skipped_reason = None
                minutes_before = _reminder_minutes(args)
                if minutes_before is not None:
                    reminder_note_id, reminder_skipped_reason = _create_calendar_reminder(
                        existing.summary or summary,
                        existing.location or "",
                        existing.dtstart,
                        existing.all_day,
                        minutes_before,
                        bool(existing.is_utc),
                    )
                    if reminder_note_id:
                        db.commit()
                reminder_text = ""
                if minutes_before is not None:
                    reminder_text = (
                        f"; reminder set {minutes_before} min before"
                        if reminder_note_id
                        else f"; reminder not set ({reminder_skipped_reason or 'reminder time already passed'})"
                    )
                return {
                    "response": (
                        f"Event already exists: '{summary}' on {dtstart_str}"
                        + reminder_text
                    ),
                    "uid": existing.uid,
                    "reminder_note_id": reminder_note_id,
                    "reminder_skipped_reason": reminder_skipped_reason,
                    "duplicate": True,
                    "exit_code": 0,
                }

            # Optional tag/category and importance — friendly aliases.
            event_type = (args.get("event_type") or args.get("tag")
                          or args.get("category") or args.get("type") or "") or None
            importance = args.get("importance") or "normal"
            minutes_before = _reminder_minutes(args)

            uid = str(_uuid.uuid4())
            ev = CalendarEvent(
                uid=uid, calendar_id=cal.id, summary=summary,
                description=_event_description(args, minutes_before),
                location=args.get("location", "") or "",
                dtstart=dtstart, dtend=dtend, all_day=all_day,
                is_utc=dtstart_is_utc and not all_day,
                rrule=args.get("rrule", "") or "",
                event_type=event_type,
                importance=importance,
            )
            db.add(ev)
            reminder_note_id = None
            reminder_skipped_reason = None
            if minutes_before is not None:
                reminder_note_id, reminder_skipped_reason = _create_calendar_reminder(
                    summary,
                    args.get("location", "") or "",
                    dtstart,
                    all_day,
                    minutes_before,
                    dtstart_is_utc and not all_day,
                )
            db.commit()
            tag_blurb = f" [{event_type}]" if event_type else ""
            if minutes_before is None:
                reminder_blurb = ""
            elif reminder_note_id:
                reminder_blurb = f" with reminder {minutes_before} min before"
            else:
                reminder_blurb = f" without reminder ({reminder_skipped_reason or 'reminder time already passed'})"
            # Return a clickable anchor so the agent can surface a link
            # that opens the calendar on that day. See the markdown
            # anchor convention ([Name](#event-<uid>)).
            return {
                "response": f"Created event [{summary}](#event-{uid}){tag_blurb} on {dtstart_str}{reminder_blurb}",
                "uid": uid,
                "anchor": f"[{summary}](#event-{uid})",
                "reminder_note_id": reminder_note_id,
                "reminder_skipped_reason": reminder_skipped_reason,
                "exit_code": 0,
            }

        elif action == "update_event":
            uid = args.get("uid")
            if not uid:
                return {"error": "uid is required", "exit_code": 1}
            try:
                base_uid = _resolve_base_uid(uid)
            except ValueError as e:
                return {"error": str(e), "exit_code": 1}
            ev = _event_query().filter(CalendarEvent.uid == base_uid).first()
            if not ev:
                return {"error": f"Event {uid} not found", "exit_code": 1}
            if args.get("summary") is not None:
                ev.summary = args["summary"]
            if args.get("description") is not None:
                ev.description = args["description"]
            if args.get("location") is not None:
                ev.location = args["location"]
            if args.get("dtstart") is not None:
                # Anchor naive/natural-language input to the USER's timezone and
                # refresh is_utc, exactly like create_event. Parsing with the
                # raw server-local _parse_dt here (and never touching is_utc)
                # silently shifted an updated event by the user's UTC offset.
                _eff_all_day = (
                    args["all_day"] if args.get("all_day") is not None else ev.all_day
                )
                ev.dtstart, _su = _parse_event_dt(args["dtstart"])
                ev.is_utc = bool(_su and not _eff_all_day)
            if args.get("dtend") is not None:
                ev.dtend, _eu = _parse_event_dt(args["dtend"])
            if args.get("all_day") is not None:
                ev.all_day = args["all_day"]
            # Tag/category + importance updates (any of these aliases).
            _tag = (args.get("event_type") or args.get("tag")
                    or args.get("category") or args.get("type"))
            if _tag is not None:
                ev.event_type = _tag or None
            if args.get("importance") is not None:
                ev.importance = args["importance"]
            db.commit()
            return {"response": f"Updated event {uid}", "exit_code": 0}

        elif action == "delete_event":
            uid = args.get("uid")
            if not uid:
                return {"error": "uid is required", "exit_code": 1}
            try:
                base_uid = _resolve_base_uid(uid)
            except ValueError as e:
                return {"error": str(e), "exit_code": 1}
            ev = _event_query().filter(CalendarEvent.uid == base_uid).first()
            if not ev:
                return {"error": f"Event {uid} not found", "exit_code": 1}
            db.delete(ev)
            db.commit()
            return {"response": f"Deleted event {uid}", "exit_code": 0}

        else:
            return {
                "error": f"Unknown action: {action}. Use list_events, create_event, update_event, delete_event, list_calendars",
                "exit_code": 1,
            }

    except Exception as e:
        db.rollback()
        logger.error(f"manage_calendar error: {e}")
        return {"error": str(e), "exit_code": 1}
    finally:
        db.close()


# ── Research tools ──

async def do_manage_research(content: str, owner: Optional[str] = None) -> Dict:
    """List, read/open, or delete saved deep-research results from the Library.
    Args (JSON): {"action": "list|read|delete", "id": "<id>", "search": "..."}.
    Research is stored as data/deep_research/<id>.json (query, summary, sources)."""
    import json as _json
    from pathlib import Path as _Path
    try:
        args = _ti._parse_tool_args(content) if content.strip().startswith("{") else {}
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    action = (args.get("action") or "list").lower()
    rid = (args.get("id") or args.get("session_id") or args.get("research_id") or "").strip()
    data_dir = _Path(DEEP_RESEARCH_DIR)

    # SECURITY: the research id is interpolated straight into a filesystem
    # path (data/deep_research/<rid>.json) for read AND delete. Without this
    # gate an agent-supplied id like "../settings" or "../../etc/passwd"
    # escapes the research dir — reading exfiltrates arbitrary *.json into
    # chat, deleting unlinks arbitrary *.json on disk. Allow only a bare
    # token (research session ids are hex/uuid/slug — no separators).
    if rid and not re.fullmatch(r"[A-Za-z0-9_-]+", rid):
        return {"error": "Invalid research id."}

    def _load(p):
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    if action in ("read", "open", "view", "get"):
        if not rid:
            return {"error": "Provide the research id (from action='list')."}
        p = data_dir / f"{rid}.json"
        if not p.exists():
            return {"error": f"Research '{rid}' not found."}
        d = _load(p) or {}
        summary = d.get("result") or d.get("raw_report") or d.get("summary") or d.get("report") or "(no report body)"
        srcs = d.get("sources", []) or []
        out = f"# {d.get('query', '(untitled)')}\n\n{summary}"
        if srcs:
            out += "\n\nSources:\n" + "\n".join(
                f"- {s.get('title') or s.get('url', '')}: {s.get('url', '')}" for s in srcs[:30]
            )
        return {"output": out[:16000], "exit_code": 0}

    if action == "delete":
        if not rid:
            return {"error": "Provide the research id to delete (from action='list')."}
        p = data_dir / f"{rid}.json"
        if p.exists():
            try:
                p.unlink()
            except Exception as e:
                return {"error": f"Failed to delete: {e}"}
            return {"output": f"Deleted research '{rid}'.", "exit_code": 0}
        return {"error": f"Research '{rid}' not found."}

    # default: list — clickable [query](#research-<id>) rows, most-recent first
    search = (args.get("search") or "").lower()
    items = []
    if data_dir.exists():
        for p in data_dir.glob("*.json"):
            d = _load(p)
            if not d:
                continue
            q = d.get("query", "")
            if search and search not in q.lower():
                continue
            items.append((d.get("completed_at", 0) or 0, p.stem, q, len(d.get("sources", []) or [])))
    items.sort(reverse=True)
    if not items:
        return {"output": "No research found in the library." + (f" (search: {search})" if search else ""), "exit_code": 0}
    rows = "\n".join(f"- [{q or '(untitled)'}](#research-{sid}) — {n} sources" for _, sid, q, n in items[:50])
    return {"output": f"Research library ({len(items)} item{'s' if len(items) != 1 else ''}):\n{rows}", "exit_code": 0}


async def do_trigger_research(content: str, owner: Optional[str] = None) -> Dict:
    """Start a live deep-research job that appears in the Deep Research
    sidebar. Hits /api/research/start (the same path the sidebar's
    'Research' button uses) so the session is discoverable + streamable
    there, rather than creating a scheduled task that never surfaces."""
    import httpx
    try:
        args = _ti._parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    topic = args.get("topic", "") or args.get("query", "")
    if not topic:
        return {"error": "topic (or query) is required", "exit_code": 1}
    payload: Dict[str, Any] = {"query": topic}
    # Optional knobs the research panel supports.
    if args.get("max_rounds") is not None:
        try: payload["max_rounds"] = int(args["max_rounds"])
        except (ValueError, TypeError): pass
    if args.get("max_time") is not None:
        try: payload["max_time"] = int(args["max_time"])
        except (ValueError, TypeError): pass
    if args.get("category"):
        payload["category"] = args["category"]
    if args.get("search_provider"):
        payload["search_provider"] = args["search_provider"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_ti._INTERNAL_BASE}/api/research/start",
                                     json=payload, headers=_ti._internal_headers(owner))
        if resp.status_code >= 400:
            return {"error": f"research/start returned HTTP {resp.status_code}: {resp.text[:200]}", "exit_code": 1}
        data = resp.json()
        sid = data.get("session_id", "?")
        return {
            "output": (
                f"Deep research started: [{topic}](#research-{sid}). "
                "Click to open the Deep Research sidebar and watch progress / read the report."
            ),
            "session_id": sid,
            "anchor": f"[{topic}](#research-{sid})",
            # UI hint so the frontend can open/refresh the research panel.
            "ui_event": "research_started",
            "research_session_id": sid,
            "exit_code": 0,
        }
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


# ── Contact tools ──

async def do_resolve_contact(content: str, owner: Optional[str] = None) -> Dict:
    """Look up a contact by name. Searches: CardDAV -> email history -> memory."""
    import httpx
    try:
        args = _ti._parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    name = args.get("name", "")
    if not name:
        return {"error": "name is required", "exit_code": 1}

    contacts = {}  # email -> {name, source}

    # 1. CardDAV (Radicale) — structured contacts. Call in-process: a
    # server-side httpx GET to /api/contacts/search carries no session
    # cookie and would 401 under require_user.
    try:
        import asyncio
        from routes import contacts_routes as cc
        all_contacts = await asyncio.to_thread(cc._fetch_contacts)
        q = name.lower()
        for c in (all_contacts or []):
            hay_name = (c.get("name") or "").lower()
            match = q in hay_name or any(q in (e or "").lower() for e in c.get("emails", []))
            if not match:
                continue
            for email in (c.get("emails") or []):
                email = (email or "").strip().lower()
                if email and "@" in email:
                    contacts[email] = {"name": c.get("name") or email, "source": "contacts"}
    except Exception:
        pass

    async with httpx.AsyncClient(timeout=30) as client:
        # 2. Email history (sent/received)
        try:
            resp = await client.get(f"{_ti._INTERNAL_BASE}/api/email/resolve-contact", params={"name": name})
            if resp.status_code == 200:
                for c in (resp.json().get("contacts") or []):
                    email = (c.get("email") or "").strip().lower()
                    if email and email not in contacts:
                        contacts[email] = {"name": c.get("name") or email, "source": "email history"}
        except Exception:
            pass

    if not contacts:
        return {"output": f"No contacts found matching '{name}'.", "exit_code": 0}

    lines = [f"Contacts matching '{name}':"]
    for email, info in contacts.items():
        lines.append(f"- {info['name']} <{email}> ({info['source']})")
    return {"output": "\n".join(lines), "exit_code": 0}


async def do_manage_contact(content: str, owner: Optional[str] = None) -> Dict:
    """Add / update / delete / list CardDAV contacts. Calls the contacts
    helpers IN-PROCESS rather than over HTTP — a server-side httpx call to
    /api/contacts/* carries no session cookie and would be rejected by
    require_user (401), so the tool would see zero contacts even though
    the browser-side UI works fine."""
    try:
        args = _ti._parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    action = (args.get("action") or "").strip().lower()
    try:
        from routes import contacts_routes as cc
    except Exception as e:
        return {"error": f"Contacts module unavailable: {e}", "exit_code": 1}
    # The contacts helpers are sync (httpx blocking calls to CardDAV) — run
    # them in a thread so we don't block the event loop.
    import asyncio
    try:
        if action == "list":
            rows = await asyncio.to_thread(cc._fetch_contacts, True)
            if not rows:
                return {"output": "No contacts.", "exit_code": 0}
            lines = [f"{len(rows)} contacts:"]
            for c in rows:
                em = ", ".join(c.get("emails") or [])
                lines.append(f"- {c.get('name') or '(no name)'} <{em}>  [uid={c.get('uid','')}]")
            return {"output": "\n".join(lines), "exit_code": 0}

        if action == "add":
            email = (args.get("email") or "").strip()
            if not email:
                return {"error": "email is required for add", "exit_code": 1}
            name = (args.get("name") or "").strip() or email.split("@")[0]
            # Dedupe by email (same as the /add route).
            existing = await asyncio.to_thread(cc._fetch_contacts)
            for c in existing:
                if email.lower() in [e.lower() for e in c.get("emails", [])]:
                    return {"output": f"{email} is already a contact ({c.get('name','')}).", "exit_code": 0}
            ok = await asyncio.to_thread(cc._create_contact, name, email)
            return {"output": f"{'Added' if ok else 'Failed to add'} {name} <{email}>.", "exit_code": 0 if ok else 1}

        if action in ("update", "edit"):
            uid = (args.get("uid") or "").strip()
            if not uid:
                return {"error": "uid is required for update (use action=list to find it)", "exit_code": 1}
            name = (args.get("name") or "").strip()
            emails = args.get("emails")
            if emails is None and args.get("email"):
                emails = [args["email"]]
            emails = [e.strip() for e in (emails or []) if e and e.strip()]
            phones = [p.strip() for p in (args.get("phones") or []) if p and p.strip()]
            if not name and not emails:
                return {"error": "Provide a name or emails to update", "exit_code": 1}
            if not name and emails:
                name = emails[0].split("@")[0]
            ok = await asyncio.to_thread(cc._update_contact, uid, name, emails, phones)
            return {"output": "Contact updated." if ok else "Update failed.", "exit_code": 0 if ok else 1}

        if action == "delete":
            uid = (args.get("uid") or "").strip()
            if not uid:
                return {"error": "uid is required for delete (use action=list to find it)", "exit_code": 1}
            ok = await asyncio.to_thread(cc._delete_contact, uid)
            return {"output": "Contact deleted." if ok else "Delete failed.", "exit_code": 0 if ok else 1}

        return {"error": f"Unknown action '{action}'. Use list, add, update, or delete.", "exit_code": 1}
    except Exception as e:
        return {"error": f"Contact operation failed: {e}", "exit_code": 1}


# ── Vaultwarden / Bitwarden CLI tools ──

