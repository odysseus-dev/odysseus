"""Regression test for issue #1476 — Docker/headless users grepped the first-run
logs for a "Temporary password" that Odysseus never generates (setup is a web
flow), and the existing log line ("first-run setup required") didn't say how to
proceed. The message now tells them to finish setup in the browser.
"""
import logging

from core.auth import AuthManager


def test_first_run_message_directs_user_to_web_setup(caplog, tmp_path):
    with caplog.at_level(logging.INFO):
        # No auth file at this path → the first-run branch fires.
        AuthManager(auth_path=str(tmp_path / "auth.json"))
    first_run = [r.message for r in caplog.records if "first-run setup required" in r.message]
    assert first_run, "expected a first-run setup log line"
    msg = first_run[0].lower()
    # Actionable: tells the user to use the browser/web UI...
    assert "browser" in msg or "web ui" in msg
    # ...and clears up the "where's my password" confusion.
    assert "no console password" in msg
