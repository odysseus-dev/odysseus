"""Per-request user-local time helpers.

Chat routes set this context from browser headers. Prompt builders and tools
can then resolve relative dates against the user's clock instead of the server.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Optional


_USER_TZ_OFFSET_MIN: ContextVar[Optional[int]] = ContextVar("user_tz_offset_min", default=None)
_USER_TZ_NAME: ContextVar[Optional[str]] = ContextVar("user_tz_name", default=None)


def set_user_tz_offset(offset_min) -> None:
    """Set the current user's UTC offset in minutes east of UTC."""
    if offset_min in (None, ""):
        _USER_TZ_OFFSET_MIN.set(None)
        return
    try:
        value = int(offset_min)
    except (TypeError, ValueError):
        return
    if -14 * 60 <= value <= 14 * 60:
        _USER_TZ_OFFSET_MIN.set(value)


def get_user_tz_offset() -> Optional[int]:
    """Return minutes east of UTC for the current user, if known."""
    return _USER_TZ_OFFSET_MIN.get()


def set_user_tz_name(name) -> None:
    """Set a safe IANA timezone label for the current request context."""
    if not name:
        _USER_TZ_NAME.set(None)
        return
    first_token = str(name).strip().split()[0] if str(name).strip() else ""
    cleaned = re.sub(r"[^A-Za-z0-9_+\-./]", "", first_token)[:80]
    _USER_TZ_NAME.set(cleaned or None)


def get_user_tz_name() -> Optional[str]:
    """Return the current user's browser timezone name, if provided."""
    return _USER_TZ_NAME.get()


def clear_user_time_context() -> None:
    """Clear user-local time context for tests and non-browser entry points."""
    _USER_TZ_OFFSET_MIN.set(None)
    _USER_TZ_NAME.set(None)


def format_utc_offset(offset_min: Optional[int]) -> str:
    """Format minutes east of UTC as +HH:MM or -HH:MM."""
    if offset_min is None:
        offset_min = 0
    sign = "+" if offset_min >= 0 else "-"
    total = abs(int(offset_min))
    hours, minutes = divmod(total, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def user_timezone() -> timezone:
    """Return the best known user timezone as a fixed-offset tzinfo."""
    offset = get_user_tz_offset()
    if offset is None:
        name = get_user_tz_name()
        if name:
            try:
                from zoneinfo import ZoneInfo
                return ZoneInfo(name)
            except Exception:
                pass
        return datetime.now().astimezone().tzinfo or timezone.utc
    return timezone(timedelta(minutes=offset))


def now_user_local(now_utc: Optional[datetime] = None) -> datetime:
    """Return the current time in the user's timezone."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(user_timezone())


def _date_label(dt: datetime) -> str:
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.year}"


def _clock_label(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {dt.strftime('%p')}"


def timezone_label(dt: Optional[datetime] = None) -> str:
    """Return a concise display label such as Australia/Brisbane, UTC+10:00."""
    offset = get_user_tz_offset()
    if offset is None:
        if dt is None:
            dt = datetime.now().astimezone()
        offset = int((dt.utcoffset() or timedelta()).total_seconds() // 60)
    offset_label = f"UTC{format_utc_offset(offset)}"
    name = get_user_tz_name()
    return f"{name}, {offset_label}" if name else offset_label


def current_datetime_prompt(now_utc: Optional[datetime] = None) -> str:
    """Build reusable system prompt text for date/time reasoning."""
    if now_utc is None:
        utc_now = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        utc_now = now_utc.replace(tzinfo=timezone.utc)
    else:
        utc_now = now_utc.astimezone(timezone.utc)

    local_now = now_user_local(utc_now)
    tomorrow = local_now + timedelta(days=1)
    return (
        "## Current date and time\n"
        f"Today is {_date_label(local_now)} ({local_now.strftime('%Y-%m-%d')}). "
        f"User local time is {_clock_label(local_now)} ({timezone_label(local_now)}); "
        f"current UTC time is {utc_now.strftime('%H:%M')}.\n"
        f"Tomorrow is {_date_label(tomorrow)} ({tomorrow.strftime('%Y-%m-%d')}) "
        "in the user's local timezone.\n"
        "Use this for any 'today', 'tomorrow', 'tonight', 'this week', or other "
        "relative-date reasoning. Do not ask for an exact date just because the "
        "user used a relative date.\n"
        "When scheduling calendar events with manage_calendar, pass local ISO "
        "datetimes resolved against this user-local date/time.\n"
        "When scheduling a task with manage_tasks, scheduled_time is in UTC: "
        "convert the user's stated local time using the UTC offset above.\n\n"
    )
