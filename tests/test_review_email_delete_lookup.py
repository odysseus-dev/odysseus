from unittest.mock import MagicMock

import pytest

from routes.email_routes import _resolve_current_email_uid


def test_disconnect_is_not_confirmed_absence():
    conn = MagicMock()
    conn.uid.side_effect = OSError("IMAP disconnected")
    with pytest.raises(OSError):
        _resolve_current_email_uid(conn, "123")


def test_rejected_search_is_not_confirmed_absence():
    conn = MagicMock()
    conn.uid.side_effect = [("OK", [None]), ("NO", [b"search failed"])]
    with pytest.raises(RuntimeError):
        _resolve_current_email_uid(conn, "123")


def test_confirmed_absence_can_be_idempotent():
    conn = MagicMock()
    conn.uid.side_effect = [("OK", [None]), ("OK", [b""])]
    assert _resolve_current_email_uid(conn, "123") == ""


def test_message_id_search_failure_is_not_confirmed_absence():
    conn = MagicMock()
    conn.uid.side_effect = [("OK", [None]), ("OK", [b""]), ("NO", [b"failed"])]
    with pytest.raises(RuntimeError):
        _resolve_current_email_uid(conn, "123", "<id@example.test>")


def test_stale_uid_resolves_by_message_id():
    conn = MagicMock()
    conn.uid.side_effect = [("OK", [None]), ("OK", [b""]), ("OK", [b"456"])]
    assert _resolve_current_email_uid(conn, "123", "<id@example.test>") == "456"
