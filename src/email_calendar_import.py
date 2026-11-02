"""Apply email invitation revisions without treating cancellations as creates."""

import asyncio
import errno
import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path


@asynccontextmanager
async def _invitation_lock(owner, sender, source_uid):
    """Serialize a series across pollers/workers, including detached instances.

    File locks survive awaits without blocking the loop, release on process
    exit, and don't require holding a database transaction across tool calls.
    Fixed stripes bound disk usage. Never unlink lock files: another process
    may already be waiting on the same inode.
    """
    from src.constants import DATA_DIR
    identity = json.dumps([str(owner or ""), parseaddr(sender)[1].strip().casefold(), str(source_uid).strip()])
    stripe = int(hashlib.sha256(identity.encode()).hexdigest(), 16) % 64
    directory = Path(DATA_DIR) / ".calendar-import-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / f"{stripe:02x}.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            acquire = lambda: msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                await asyncio.sleep(0.025)
        yield
    finally:
        os.close(fd)


async def apply_invitation(component, method, *, owner, sender, args):
    async with _invitation_lock(owner, sender, component.get("uid") or ""):
        return await _apply_invitation(component, method, owner=owner, sender=sender, args=args)


async def _apply_invitation(component, method, *, owner, sender, args):
    from core.database import SessionLocal, CalendarCal, CalendarEvent, EmailCalendarInvitation
    from src.tool_implementations import do_manage_calendar
    from routes.calendar_routes import (
        _delete_calendar_reminders_for_event, _push_caldav_event_after_commit,
        _ics_naive_dtstart, _recurrence_exdates,
    )

    source_uid = str(component.get("uid") or "").strip()
    if not source_uid:
        raise ValueError("Calendar invitation is missing its UID")
    sender = parseaddr(sender)[1].strip().casefold()
    if not sender:
        raise ValueError("Calendar invitation is missing its sender")
    owner = str(owner or "")
    # Untrusted ICS UIDs must never address arbitrary database event IDs.
    identity = hashlib.sha256(json.dumps([owner, sender, source_uid]).encode()).hexdigest()
    master_identity = identity
    recurrence = component.get("recurrence-id")
    recurrence_id = ""
    if recurrence is not None:
        if str(recurrence.params.get("RANGE", "")).upper() == "THISANDFUTURE":
            raise ValueError("THISANDFUTURE invitation updates require a replacement series")
        original = _ics_naive_dtstart(recurrence.dt)
        recurrence_id = original.isoformat()[:16] if isinstance(recurrence.dt, datetime) else original.date().isoformat()
        identity = hashlib.sha256(json.dumps([owner, sender, source_uid, recurrence_id]).encode()).hexdigest()
    sequence = int(component.get("sequence", 0))
    stamp_value = component.get("dtstamp")
    stamp = getattr(stamp_value, "dt", None)
    if isinstance(stamp, datetime):
        stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
        stamp = stamp.astimezone(timezone.utc).isoformat()
    else:
        stamp = ""
    cancelled = str(method).upper() == "CANCEL" or str(component.get("status", "")).upper() == "CANCELLED"
    # Replies describe an attendee's response, not a replacement event.
    if str(method).upper() not in {"", "PUBLISH", "REQUEST", "CANCEL"}:
        return {"exit_code": 0, "duplicate": True}
    db = SessionLocal()
    try:
        master = db.get(EmailCalendarInvitation, master_identity) if recurrence_id else None
        if master and master.cancelled and (sequence, stamp) <= (master.sequence, master.stamp):
            return {"exit_code": 0, "duplicate": True}
        state = db.get(EmailCalendarInvitation, identity)
        if state and (sequence, stamp) < (state.sequence, state.stamp):
            return {"exit_code": 0, "duplicate": True, "uid": state.event_uid or ""}
        if state and (sequence, stamp) == (state.sequence, state.stamp):
            # A cancellation wins ties; a replay must never resurrect it.
            if state.cancelled or not cancelled:
                return {"exit_code": 0, "duplicate": True, "uid": state.event_uid or ""}
        event = None
        if state and state.event_uid:
            event = db.query(CalendarEvent).join(CalendarCal).filter(
                CalendarEvent.uid == state.event_uid, CalendarCal.owner == owner,
            ).first()
        if state is None:
            state = EmailCalendarInvitation(id=identity, owner=owner, sender=sender, source_uid=source_uid, recurrence_id=recurrence_id)
            db.add(state)
        push_uids = []
        def exclude_occurrence():
            if master and master.event_uid:
                parent = db.query(CalendarEvent).join(CalendarCal).filter(
                    CalendarEvent.uid == master.event_uid, CalendarCal.owner == owner,
                ).first()
                if parent:
                    parent.recurrence_exdates = json.dumps(sorted(set(_recurrence_exdates(parent)) | {recurrence_id}))
                    push_uids.append(parent.uid)
        if cancelled:
            exclude_occurrence()
            if event:
                event.status = "cancelled"
                _delete_calendar_reminders_for_event(db, owner, event)
            # Retain a tombstone even if cancellation arrived before invite.
            state.sequence, state.stamp, state.cancelled = sequence, stamp, True
            if not recurrence_id:
                # Cancelling a series also hides its detached replacements.
                children = db.query(EmailCalendarInvitation).filter_by(owner=owner, sender=sender, source_uid=source_uid).all()
                for child in children:
                    if not child.recurrence_id or (child.sequence, child.stamp) > (sequence, stamp):
                        continue
                    child.cancelled, child.sequence, child.stamp = True, sequence, stamp
                    child_event = db.query(CalendarEvent).join(CalendarCal).filter(
                        CalendarEvent.uid == child.event_uid, CalendarCal.owner == owner,
                    ).first()
                    if child_event:
                        child_event.status = "cancelled"
                        _delete_calendar_reminders_for_event(db, owner, child_event)
                        push_uids.append(child_event.uid)
            db.commit()
            if event:
                await _push_caldav_event_after_commit(owner, event.uid, "update")
            for push_uid in push_uids:
                await _push_caldav_event_after_commit(owner, push_uid, "update")
            return {"exit_code": 0, "duplicate": True, "uid": state.event_uid or ""}
        if not args.get("dtstart"):
            raise ValueError("Calendar invitation is missing DTSTART")
        action_args = dict(args)
        if recurrence_id:
            action_args["rrule"] = ""
        if event:
            action_args.update(action="update_event", uid=event.uid)
        result = await do_manage_calendar(
            json.dumps(action_args), owner=owner,
            import_event_uid=str(uuid.uuid5(uuid.NAMESPACE_URL, "email-invitation:" + identity)),
        )
        if result.get("exit_code", 0) != 0:
            raise RuntimeError(result.get("error") or "Calendar invitation write failed")
        uid = str(result.get("uid") or (event.uid if event else ""))
        if not uid:
            raise RuntimeError("Calendar invitation write returned no event UID")
        state.event_uid = uid
        state.sequence, state.stamp, state.cancelled = sequence, stamp, False
        exclude_occurrence()
        if event:
            event.status = "confirmed"
        if not recurrence_id:
            children = db.query(EmailCalendarInvitation).filter_by(owner=owner, sender=sender, source_uid=source_uid).all()
            parent = db.get(CalendarEvent, uid)
            if parent:
                parent.recurrence_exdates = json.dumps(sorted(set(_recurrence_exdates(parent)) | {
                    child.recurrence_id for child in children if child.recurrence_id
                }))
                push_uids.append(uid)
        db.commit()
        if event:
            await _push_caldav_event_after_commit(owner, uid, "update")
        for push_uid in set(push_uids):
            await _push_caldav_event_after_commit(owner, push_uid, "update")
        return {**result, "uid": uid, "duplicate": bool(event) or result.get("duplicate", False)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
