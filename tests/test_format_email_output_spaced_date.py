"""Check-in email formatter must not swallow a spaced date into the sender.

_format_email_output parsed "[12] Subject From: Name | Date" with a date
group of (\\S+) — a single whitespace-free token. A real date with a space
("Jun 2", "Mon 10:30") could not match it, so the non-greedy sender group
expanded to absorb "Name | Jun 2", producing "- Name | Jun 2 — Subject".
"""
from src.task_scheduler import TaskScheduler


def test_spaced_date_not_absorbed_into_sender():
    out = TaskScheduler._format_email_output("[12] Meeting notes From: Bob Smith | Jun 2")
    assert out == "- Bob Smith — Meeting notes"


def test_no_date_still_parses():
    out = TaskScheduler._format_email_output("[1] Subject line From: Alice")
    assert out == "- Alice — Subject line"


def test_no_sender_still_parses():
    out = TaskScheduler._format_email_output("[3] Standalone subject")
    assert out == "- Standalone subject"


def test_iso_date_with_no_space_unaffected():
    out = TaskScheduler._format_email_output("[7] Report From: Carol | 2026-06-02")
    assert out == "- Carol — Report"
