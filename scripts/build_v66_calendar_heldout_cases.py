#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

WEEKDAY_INDEX = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}


def parse_time(value: str) -> tuple[int, int]:
    raw = value.lower().strip()
    minute = 0
    if ":" in raw:
        left, right = raw.replace("am", "").replace("pm", "").split(":", 1)
        hour = int(left)
        minute = int(right[:2])
    else:
        hour = int("".join(ch for ch in raw if ch.isdigit()))
    if "pm" in raw and hour != 12:
        hour += 12
    if "am" in raw and hour == 12:
        hour = 0
    return hour, minute


def next_weekday(anchor: datetime, weekday: str, modifier: str) -> datetime:
    delta = (WEEKDAY_INDEX[weekday] - anchor.weekday()) % 7
    if modifier == "next":
        delta = delta + 7 if delta != 0 else 7
    elif delta == 0:
        delta = 7
    return anchor + timedelta(days=delta)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("data/evals/ody_v66_calendar_date_logic_20260822/heldout_calendar_cases.json"))
    parser.add_argument("--limit", type=int, default=84)
    args = parser.parse_args()

    # The app route injects live current date. These cases are designed for
    # the current 2026-08-22 Asia/Tokyo test window and use deterministic
    # marker cleanup in the existing smoke harness.
    anchor = datetime(2026, 8, 22, 16, 0)
    templates = [
        ("flight", "add {marker} im flying back to japan on {phrase} {time}", False),
        ("flight", "put {marker} flight home on my calendar {phrase} at {time}", False),
        ("drive", "add {marker} drive to Kyoto {phrase} {time}", False),
        ("meeting", "schedule {marker} meeting for {phrase} {time}", False),
        ("appointment", "put {marker} appointment on {phrase} at {time}", False),
        ("flight", "add {marker} flight from Haneda {phrase} {time}", True),
        ("doctor", "schedule {marker} doctor appointment at Tokyo Midtown Clinic {phrase} {time}", True),
    ]
    times = ["5pm", "8am", "7:30pm", "11am", "9pm", "6:15pm"]
    weekdays = list(WEEKDAY_INDEX)
    modifiers = ["", "this", "next"]
    cases = []
    idx = 0
    for weekday in weekdays:
        for modifier in modifiers:
            if modifier == "this" and anchor.weekday() == WEEKDAY_INDEX[weekday]:
                continue
            for _kind, template, has_location in templates:
                if len(cases) >= args.limit:
                    break
                marker = f"ODY-V66-HELDOUT-CAL-{idx:04d}"
                phrase = f"{modifier} {weekday}".strip()
                time_text = times[idx % len(times)]
                hour, minute = parse_time(time_text)
                target = next_weekday(anchor, weekday, modifier).replace(hour=hour, minute=minute, second=0, microsecond=0)
                forbidden_values = ["2026-07-12", "2025-09-10", "JFK"]
                if not has_location:
                    forbidden_values.extend(["Haneda", "Tokyo Midtown Clinic"])
                cases.append({
                    "id": f"calendar_relative_weekday_{idx:04d}",
                    "kind": "calendar",
                    "user": template.format(marker=marker, phrase=phrase, time=time_text),
                    "marker": marker,
                    "expect_first_tool": "manage_calendar",
                    "must_mutate": "calendar_created_at",
                    "expect_created_event_dtstart": target.strftime("%Y-%m-%dT%H:%M"),
                    "forbidden_tools": ["web_search"],
                    "forbidden_tool_arg_values": forbidden_values,
                    "must_answer_any": [target.strftime("%Y-%m-%d"), target.strftime("%A"), time_text.replace(":00", "")],
                })
                idx += 1
            if len(cases) >= args.limit:
                break
        if len(cases) >= args.limit:
            break

    payload = {
        "description": "V66 held-out calendar relative weekday/date logic gate. Built for 2026-08-22 Asia/Tokyo app context.",
        "cases": cases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "cases": len(cases)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
