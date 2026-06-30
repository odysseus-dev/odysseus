"""Pin the SQLite connection-cleanup guarantee on the auto-summary write path.

`_auto_summarize_pass_single` in `routes/email_pollers.py` opens a SQLite
connection to persist each generated email summary (``INSERT OR REPLACE INTO
email_summaries``). Before the fix the write lived directly inside the broad
``try`` whose ``except`` at the end of the ``need_sum`` block logs
``"Auto-summary <uid> failed"``. If ``_c.execute()`` or ``_c.commit()`` raised
(disk full, ``database is locked``, schema drift, …) control jumped straight to
that ``except`` and the ``_c.close()`` line was never reached — the connection
leaked. Every crashed write on the 30-minute background poller leaked one more
handle until the process ran out.

This mirrors the IMAP leak regression in
``test_email_polly_imap_leak.py``: drive the real function, force the guarded
operation to raise, and assert the cleanup still happened. The companion cache
writes for calendar/urgency already wrap their SQLite ops so they cannot leak;
this test pins the same guarantee for the summary write.

Post-fix the ``finally`` block closes ``_c`` on both success and failure, so the
connection is released even though the exception still propagates to the
existing ``except`` (which keeps logging ``"summary failed"`` exactly as before).
"""

import os
import sys
import email.message
import tempfile
from pathlib import Path
from unittest.mock import MagicMock


# Point data-dir-using deps at a throwaway tmp dir BEFORE any `from routes...`
# import runs, so module-import-time engine creation can't trip on a missing
# ./data/app.db on a bare CI machine (same guard as the IMAP leak test).
_TMP_DATA = Path(tempfile.mkdtemp(prefix="odysseus-email-summary-leak-"))
os.environ.setdefault("DATA_DIR", str(_TMP_DATA))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DATA / 'app.db'}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _build_raw_email():
    """A minimal RFC822 message the poller can FETCH, parse and summarize."""
    m = email.message.EmailMessage()
    m["Message-ID"] = "<leak-test-1@local>"
    m["Subject"] = "Quarterly invoice"
    m["From"] = "Acme Billing <billing@acme.test>"
    m["Date"] = "Mon, 1 Jan 2024 09:00:00 +0000"
    # Body must exceed the 100-char "too short to process" threshold so the
    # poller actually calls the summarizer and reaches the email_summaries write.
    m.set_content(
        "Please pay invoice INV-42 totalling 1200 USD by Friday. "
        "This covers the quarterly hosting and support retainer for Q1. "
        "Let us know if you have any questions about the line items."
    )
    return m.as_bytes()


async def test_auto_summarize_closes_sqlite_on_summary_write_failure(monkeypatch):
    """A failing ``email_summaries`` INSERT must still close the SQLite
    connection. Pre-fix the broad ``except`` logged the failure but never
    reached ``_c.close()``, leaking one handle per crashed write."""
    import routes.email_pollers as email_pollers
    import src.endpoint_resolver as endpoint_resolver
    import src.llm_core as llm_core

    captured = {"closes": 0, "insert_attempts": 0}

    # ---- IMAP connection: serve exactly one inbox message, then nothing. ----
    raw = _build_raw_email()

    class _Conn:
        def select(self, folder, readonly=True):
            return ("OK", [b"1"])

        def uid(self, command, *args):
            if command == "SEARCH":
                return ("OK", [b"1"])
            if command == "FETCH":
                return ("OK", [(b"1 (RFC822 {0})", raw)])
            return ("OK", [b""])

        def logout(self):
            pass

    monkeypatch.setattr(email_pollers, "_imap_connect", lambda account_id=None, owner="": _Conn())
    monkeypatch.setattr(email_pollers, "_owner_for_email_account", lambda account_id: "")
    monkeypatch.setattr(email_pollers, "_load_settings", lambda: {"email_auto_summarize": True})

    # ---- SQLite: cache-read SELECTs succeed (empty); summary INSERT raises. ----
    class _FakeCursor:
        def fetchall(self):
            return []

    class _FakeConn:
        def execute(self, sql, params=()):
            if "INSERT OR REPLACE INTO email_summaries" in sql:
                captured["insert_attempts"] += 1
                raise RuntimeError("simulated disk full on summary INSERT")
            return _FakeCursor()

        def commit(self):
            pass

        def close(self):
            captured["closes"] += 1

    # sqlite3 is imported inside the function as `_sql3`; patch the real module.
    import sqlite3 as _real_sqlite3
    monkeypatch.setattr(_real_sqlite3, "connect", lambda *a, **k: _FakeConn())

    # ---- LLM endpoint + requests.post: return a usable summary. ----
    monkeypatch.setattr(
        endpoint_resolver, "resolve_endpoint",
        lambda kind, owner=None: ("http://llm.local/v1/chat/completions", "test-model", {}),
    )
    monkeypatch.setattr(llm_core, "_uses_max_completion_tokens", lambda model: False)
    monkeypatch.setattr(llm_core, "_restricts_temperature", lambda model: False)

    def _fake_post(url, json=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {
            "choices": [{"message": {"content": "<<<SUMMARY>>>\n- Pay invoice INV-42 (1200 USD) by Friday.\n<<<END>>>"}}]
        }
        return resp

    # `requests` is imported inside the function as `_req`; patch the real
    # module so the inner `import requests as _req` resolves to our stub.
    import requests as _real_requests
    monkeypatch.setattr(_real_requests, "post", _fake_post)

    await email_pollers._auto_summarize_pass_single(account_id="acct-1", progress_cb=None)

    assert captured["insert_attempts"] >= 1, (
        "test setup: the email_summaries INSERT must be reached for the leak to apply"
    )
    # Cache-read connection (line ~256) is closed normally; the summary-write
    # connection must ALSO be closed despite the INSERT raising. Pre-fix only
    # the cache-read close fired, so closes < insert-connections-opened.
    assert captured["closes"] >= 2, (
        f"SQLite connection on the summary-write path must be closed even when "
        f"the INSERT raises (resource-leak fix). Got closes={captured['closes']}, "
        f"insert_attempts={captured['insert_attempts']}. Pre-fix the broad "
        f"except logged the failure but never reached _c.close()."
    )
