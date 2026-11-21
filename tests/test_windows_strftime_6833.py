"""Regression tests for cross-platform date/time formatting without glibc %- flags (#6833)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import src.agent_loop as agent_loop
import src.user_time as user_time


class _StrictWindowsDatetime(datetime):
    """Datetime subclass rejecting glibc-only '%-' strftime flags on any OS."""

    def strftime(self, fmt: str) -> str:
        if "%-" in fmt:
            raise ValueError("Invalid format string")
        return super().strftime(fmt)


def test_calendar_list_summary_avoids_glibc_strftime_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_time, "user_timezone", lambda: timezone.utc)

    class _StrictModuleDatetime:
        @staticmethod
        def fromisoformat(value: str) -> _StrictWindowsDatetime:
            parsed = datetime.fromisoformat(value)
            return _StrictWindowsDatetime(
                parsed.year,
                parsed.month,
                parsed.day,
                parsed.hour,
                parsed.minute,
                parsed.second,
                parsed.microsecond,
                tzinfo=parsed.tzinfo,
            )

    monkeypatch.setattr(agent_loop, "datetime", _StrictModuleDatetime)

    raw = "\n".join(
        [
            "AI: Found 2 event(s) between 2026-09-04 and 2026-09-06:",
            "- 2026-09-04T09:05:00Z -> 2026-09-04T10:30:00Z: [Standup](#event-standup)",
            "- 2026-09-05 (all day): [Offsite](#event-offsite)",
        ]
    )
    summary = agent_loop._calendar_list_summary_from_tool_output(raw)
    assert "[Standup](#event-standup) — Sep 4, 9:05 AM–10:30 AM" in summary
    assert "[Offsite](#event-offsite) — Sep 5 · All day" in summary


def test_friendly_email_date_avoids_glibc_strftime_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StrictModuleDatetime:
        @staticmethod
        def fromisoformat(value: str) -> _StrictWindowsDatetime:
            parsed = datetime.fromisoformat(value)
            return _StrictWindowsDatetime(
                parsed.year,
                parsed.month,
                parsed.day,
                parsed.hour,
                parsed.minute,
                parsed.second,
                parsed.microsecond,
                tzinfo=parsed.tzinfo,
            )

    monkeypatch.setattr(agent_loop, "datetime", _StrictModuleDatetime)

    assert agent_loop._friendly_email_date("2026-08-05T09:05:00Z") == "Aug 5, 9:05 AM"
    assert agent_loop._friendly_email_date("2026-11-20T14:30:00 extra") == "Nov 20, 2:30 PM"
    assert agent_loop._friendly_email_date("not-a-date") == "not-a-date"
    assert agent_loop._friendly_email_date("") == ""
