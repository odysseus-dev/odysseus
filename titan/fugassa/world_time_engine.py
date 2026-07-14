"""Canonical world-time apply, advance, and display — single source of truth."""

from __future__ import annotations

import re
from typing import Any

_CLOCK_RE = re.compile(r"^\d{1,2}:\d{2}(\s*(?:AM|PM))?$", re.I)
_AMPM_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*(AM|PM)?$", re.I)


def looks_like_clock(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if _CLOCK_RE.match(text):
        return True
    return bool(re.search(r"\d{1,2}:\d{2}\s*(?:AM|PM)", text, re.I))


def period_label(hour: int) -> str:
    h = int(hour) % 24
    if 5 <= h < 12:
        return "Morning"
    if 12 <= h < 17:
        return "Afternoon"
    if 17 <= h < 21:
        return "Evening"
    return "Night"


def parse_hhmm(text: str) -> tuple[int, int] | None:
    m = _AMPM_HHMM_RE.match(str(text or "").strip())
    if not m:
        return None
    hour = int(m.group(1)) % 12
    minute = int(m.group(2))
    meridiem = (m.group(3) or "").upper()
    if meridiem == "PM":
        hour += 12
    if meridiem == "AM" and int(m.group(1)) == 12:
        hour = 0
    elif not meridiem and int(m.group(1)) >= 13:
        hour = int(m.group(1))
    return hour % 24, minute % 60


def format_hhmm(hour: int, minute: int = 0, *, twelve_hour: bool = True) -> str:
    h, m = int(hour) % 24, int(minute) % 60
    if not twelve_hour:
        return f"{h:02d}:{m:02d}"
    meridiem = "AM" if h < 12 else "PM"
    display = h % 12 or 12
    return f"{display}:{m:02d} {meridiem}"


def split_era_year_cell(era_year: str) -> dict[str, Any]:
    """Parse GM/wizard 'Era, Year, Month, Day' column (comma-separated)."""
    text = str(era_year or "").strip()
    if not text:
        return {}
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 4:
        out: dict[str, Any] = {"era": parts[0], "year": parts[1], "month": parts[2]}
        out["day"] = int(parts[3]) if parts[3].isdigit() else parts[3]
        return out
    if len(parts) == 3:
        return {"era": parts[0], "year": parts[1], "month": parts[2]}
    if len(parts) == 2:
        return {"era": parts[0], "year": parts[1]}
    return {"display_era": text}


def apply_gm_timestamp(state: dict[str, Any], ts: dict[str, Any]) -> None:
    """Merge a parsed GM timestamp table into ``world_time``."""
    if not ts:
        return
    wt = dict(state.get("world_time") or {})
    for key in ("era", "year", "month", "weather", "season", "moon_phase"):
        if ts.get(key) is not None and str(ts.get(key)).strip():
            wt[key] = ts[key]
    tod = str(ts.get("time_of_day") or "").strip()
    if tod and not looks_like_clock(tod):
        wt["time_of_day"] = tod
    elif tod and looks_like_clock(tod) and not wt.get("time_of_day"):
        wt["time_of_day"] = period_label(parse_hhmm(tod)[0] if parse_hhmm(tod) else int(wt.get("hour", 8)))
    hhmm = str(ts.get("hhmm") or "").strip()
    if hhmm:
        wt["hhmm"] = hhmm
        parsed = parse_hhmm(hhmm)
        if parsed:
            wt["hour"], wt["minute"] = parsed
    if ts.get("day") is not None and str(ts.get("day")).strip().isdigit():
        wt["day"] = int(ts["day"])
    if ts.get("location"):
        loc = dict(state.get("location_state") or {})
        loc["gm_location_label"] = str(ts["location"]).strip()
        state["location_state"] = loc
    state["world_time"] = wt


def apply_time_delta(state: dict[str, Any], minutes: int) -> None:
    """Advance engine-owned clock by *minutes* (movement, rest, narrative default)."""
    if minutes <= 0:
        return
    wt = dict(state.get("world_time") or {"day": 1, "hour": 8})
    hour = int(wt.get("hour", 8))
    minute = int(wt.get("minute", 0))
    day = int(wt.get("day", 1))
    total = hour * 60 + minute + int(minutes)
    day += total // (24 * 60)
    total = total % (24 * 60)
    hour = total // 60
    minute = total % 60
    wt["day"] = day
    wt["hour"] = hour
    wt["minute"] = minute
    wt["hhmm"] = format_hhmm(hour, minute)
    tod = str(wt.get("time_of_day") or "").strip()
    if not tod or looks_like_clock(tod):
        wt["time_of_day"] = period_label(hour)
    state["world_time"] = wt


def default_narrative_minutes(intent: str) -> int:
    if intent in ("social",):
        return 5
    if intent in ("search",):
        return 15
    if intent in ("narrative_travel", "narrative_only"):
        return 8
    return 5


def format_world_time_label(wt: dict[str, Any] | None) -> str:
    """Single-line clock label for HUD/chat (no duplicate AM strings)."""
    wt = wt or {}
    parts: list[str] = []
    tod = str(wt.get("time_of_day") or "").strip()
    if tod and not looks_like_clock(tod):
        parts.append(tod)
    clock = str(wt.get("hhmm") or "").strip()
    if not clock:
        hour = wt.get("hour")
        if hour is not None:
            minute = int(wt.get("minute", 0))
            clock = format_hhmm(int(hour), minute)
    if clock and clock not in parts:
        parts.append(clock)
    if not parts:
        day = wt.get("day", 1)
        hour = wt.get("hour", 8)
        parts.append(f"Day {day}, {format_hhmm(int(hour), int(wt.get('minute', 0)))}")
    return " ".join(parts)


def format_world_date_label(wt: dict[str, Any] | None) -> str:
    wt = wt or {}
    bits: list[str] = []
    if wt.get("era"):
        bits.append(str(wt["era"]))
    if wt.get("year"):
        bits.append(str(wt["year"]))
    if wt.get("month"):
        bits.append(str(wt["month"]))
    if wt.get("day") is not None:
        bits.append(str(wt["day"]))
    if wt.get("season") and str(wt["season"]) not in bits:
        bits.append(str(wt["season"]))
    if wt.get("moon_phase"):
        bits.append(f"Moon: {wt['moon_phase']}")
    if wt.get("weather"):
        bits.append(str(wt["weather"]))
    return " · ".join(bits)


def format_chat_header(wt: dict[str, Any] | None, location: str | None = None) -> str:
    time_part = format_world_time_label(wt)
    loc = str(location or "").strip()
    if loc:
        return f"{time_part} · {loc}"
    return time_part


def snapshot_for_chat(state: dict[str, Any]) -> dict[str, str]:
    wt = state.get("world_time") or {}
    loc = (state.get("location_state") or {}).get("name") or ""
    gm_loc = (state.get("location_state") or {}).get("gm_location_label") or ""
    location = gm_loc or loc
    return {
        "label": format_world_time_label(wt),
        "date_label": format_world_date_label(wt),
        "location": location,
        "header": format_chat_header(wt, location),
    }


def repair_world_time_from_wizard(state: dict[str, Any]) -> bool:
    """Re-apply wizard opening_time_hint with overwrite for corrupted saves."""
    from titan.fugassa.game_bootstrap import apply_opening_time_hint_to_world_time

    return apply_opening_time_hint_to_world_time(state, overwrite=True)
