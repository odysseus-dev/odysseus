"""Pin the IMAP connection-cleanup guarantee in the background auto-summarize poller.

`_auto_summarize_pass_single` in `routes/email_pollers.py` is invoked on a
30-minute background cadence (via `_auto_summarize_poller`) and on-demand
for one-shot scheduled tasks. It opens a long-lived IMAP connection at
line 171 (`conn = _imap_connect(...)`) and then performs ~700 lines of
work — IMAP `select`/`FETCH`/`SEARCH`, network POSTs to the LLM endpoint,
SQLite writes, and per-uid awaits.

If anything in that body raised before this fix, the outer `except`
block at line 921 caught it, logged `"Auto-summarize pass error: ..."`,
and returned. The IMAP `conn.logout()` was *only* called on three safe
paths (early `"No recent emails"`, early `"No model configured"`, and
the happy path at the very end), so any exception meant the socket
stayed open until the IMAP server's idle timeout killed it. For a
background poller that runs every 30 minutes, that is a slow but
unbounded connection leak per crashed pass.

This is the exact same shape as the just-merged upstream fixes #1325
(`_imap_move` in `routes/email_helpers.py`) and #1330 (`_list_emails_sync`
in `routes/email_routes.py`), but the request-path fixes did not cover
the *background* poller path — so this is the obvious third instance a
careful reviewer would ask "did we get all of them?".

The fix is the same try/finally pattern from #1330:
  1. initialize `conn = None` before the try
  2. let the try-block assign `conn = _imap_connect(...)`
  3. drop the three explicit `conn.logout()` calls on safe paths
  4. add a `finally:` block that calls `conn.logout()` if `conn` was set

The regression test below triggers an exception in the post-`conn` body
(force `conn.select` to raise) and asserts `conn.logout` was called.
Pre-fix the assertion fails because the `except` branch never reaches
`conn.logout`; post-fix the `finally` block guarantees it.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock


# Point every data-dir-using dependency (core.database, secret_storage,
# routes.email_helpers, ...) at a per-process tmp dir BEFORE any
# `from routes...` import runs. Without this the SQLAlchemy engine
# created at module-import time would try to open `./data/app.db`,
# which doesn't exist on bare CI machines, and our test would fail
# with `OperationalError: unable to open database file` long before
# the leak regression had a chance to fire.
_TMP_DATA = Path(tempfile.mkdtemp(prefix="odysseus-email-polly-leak-"))
os.environ.setdefault("DATA_DIR", str(_TMP_DATA))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DATA / 'app.db'}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def test_auto_summarize_pass_logs_out_imap_on_select_failure(monkeypatch):
    """An exception after `conn = _imap_connect(...)` must still call
    `conn.logout()`. Pre-fix, the outer `except` returned without
    logging out, leaking the IMAP socket. The `select` call on the
    post-connect path is the first un-guarded IMAP call, so forcing
    it to raise lands us in the outer `except` cleanly without any
    of the inner try/except scans swallowing the error first."""
    import routes.email_pollers as email_pollers

    captured = {}

    class _Conn:
        def select(self, folder, readonly=True):
            captured.setdefault("select_calls", []).append(folder)
            raise RuntimeError("simulated IMAP select failure")

        def logout(self):
            captured["logout_calls"] = captured.get("logout_calls", 0) + 1

    def fake_imap_connect(account_id=None, owner=""):
        captured["connect_called"] = True
        return _Conn()

    def fake_owner_for(account_id):
        return "alice"

    def fake_load_settings():
        # Enable at least one auto_* so we get past the early
        # "Nothing to do" return at line 159 (which returns before
        # `conn` is created and so is not relevant to the leak).
        return {"email_auto_summarize": True}

    monkeypatch.setattr(email_pollers, "_imap_connect", fake_imap_connect)
    monkeypatch.setattr(email_pollers, "_owner_for_email_account", fake_owner_for)
    monkeypatch.setattr(email_pollers, "_load_settings", fake_load_settings)

    result = await email_pollers._auto_summarize_pass_single(
        account_id="acct-1", progress_cb=None,
    )

    assert captured.get("connect_called") is True, (
        "test setup: _imap_connect must be reached for the leak to apply"
    )
    assert captured.get("logout_calls", 0) >= 1, (
        f"conn.logout() must be called at least once on the error path "
        f"(IMAP leak fix). Got logout_calls={captured.get('logout_calls')}, "
        f"select_calls={captured.get('select_calls')}. Pre-fix the "
        f"outer `except` returned without logging out the IMAP socket."
    )
    assert result.startswith("Error:"), (
        f"On simulated failure, the function should return an 'Error: ...' "
        f"string (matches the outer except at line 921). Got: {result!r}"
    )
