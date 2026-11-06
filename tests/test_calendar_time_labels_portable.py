"""Calendar and email time labels must not use glibc-only strftime flags.

"%-I" and "%-d" drop zero padding on glibc but raise ValueError ("Invalid
format string") on Windows, so a native Windows install crashed while
summarising calendar events and showed raw ISO dates for email. The tests
reproduce Windows behaviour on every platform with a datetime whose
strftime rejects the "%-" flags the way the Windows C runtime does.
"""
from datetime import datetime, timezone

import src.agent_loop as agent_loop
import src.user_time


class _WindowsStrftime(datetime):
    def strftime(self, fmt):
        if "%-" in fmt:
            raise ValueError("Invalid format string")
        return super().strftime(fmt)


def test_calendar_summary_times_do_not_need_glibc_strftime(monkeypatch):
    monkeypatch.setattr(agent_loop, "datetime", _WindowsStrftime)
    monkeypatch.setattr(src.user_time, "user_timezone", lambda: timezone.utc)
    raw = "\n".join([
        "AI: Found 1 event(s) between 2026-09-04 and 2026-09-04:",
        "- 2026-09-04T09:05:00Z -> 2026-09-04T13:30:00Z: [Dentist](#event-dentist)",
    ])

    summary = agent_loop._calendar_list_summary_from_tool_output(raw, max_items=1)

    assert "[Dentist](#event-dentist) — Sep 4, 9:05 AM–1:30 PM" in summary


def test_email_dates_do_not_need_glibc_strftime(monkeypatch):
    monkeypatch.setattr(agent_loop, "datetime", _WindowsStrftime)

    assert agent_loop._friendly_email_date("2026-09-04T09:05:00Z") == "Sep 4, 9:05 AM"
    assert agent_loop._friendly_email_date("2026-12-31T23:45:00") == "Dec 31, 11:45 PM"
