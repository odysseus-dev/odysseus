"""Regression guard for issue #1851 — sessions deleted mid-flight.

`run_auto_sort` fires on every session-created event. A brand-new session
has 0 messages, so it matched the `msg_count == 0 → delete` branch before
the first chat message could arrive. The result: the session vanished between
`POST /api/session` (201) and the first `POST /api/chat_stream`, returning 404.

Fix: skip any session whose `created_at` is within `_NEW_SESSION_GRACE_SECONDS`
(5 minutes). The guard is applied before all other deletion heuristics so that
no newly created session can be culled regardless of name, message count, or
content length.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src/session_actions.py"


def _src() -> str:
    return SRC.read_text(encoding="utf-8")


def test_grace_period_constant_defined():
    text = _src()
    assert "_NEW_SESSION_GRACE_SECONDS" in text, (
        "_NEW_SESSION_GRACE_SECONDS constant must be defined in session_actions.py"
    )
    # Must be a positive integer (seconds)
    m = re.search(r"_NEW_SESSION_GRACE_SECONDS\s*=\s*(\d+)", text)
    assert m, "_NEW_SESSION_GRACE_SECONDS must be assigned a numeric value"
    assert int(m.group(1)) > 0, "_NEW_SESSION_GRACE_SECONDS must be positive"


def test_grace_period_check_before_deletion_heuristics():
    text = _src()
    # The grace-period continue must appear in the per-row loop and must come
    # before the msg_count == 0 deletion branch.
    grace_pos = text.find("_NEW_SESSION_GRACE_SECONDS")
    empty_delete_pos = text.find("msg_count == 0")
    assert grace_pos != -1, "grace-period guard not found"
    assert empty_delete_pos != -1, "msg_count == 0 branch not found"
    assert grace_pos < empty_delete_pos, (
        "grace-period guard must appear before the msg_count == 0 delete branch"
    )


def test_grace_period_uses_created_at():
    text = _src()
    # Guard must read row.created_at and compare against utcnow()
    assert re.search(r"row\.created_at", text), (
        "grace-period check must read row.created_at"
    )
    assert re.search(r"datetime\.utcnow\(\)\s*-\s*row\.created_at", text), (
        "grace-period check must compare datetime.utcnow() - row.created_at"
    )
    assert "_NEW_SESSION_GRACE_SECONDS" in text, (
        "grace-period check must reference _NEW_SESSION_GRACE_SECONDS"
    )
