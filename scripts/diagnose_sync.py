"""Report what Odysseus actually sees for mail folders and calendar sync.

Runs inside the container against the app's own modules, so it needs no
session cookie and no API token — the HTTP API requires a login, which is
what makes an equivalent curl script awkward to run.

    docker compose exec odysseus python3 scripts/diagnose_sync.py

Prints folder names, counts, calendar names and a live sync result. It never
prints a message body, a token, a password or anything out of .env.
"""

import os
import signal
import sys
import traceback

# Works whether this is run inside the container (/app) or from a checkout.
for candidate in (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "/app"):
    if candidate not in sys.path and os.path.isdir(os.path.join(candidate, "core")):
        sys.path.insert(0, candidate)


# Unbuffered, so a probe that stalls still shows everything printed before it.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

PROBE_TIMEOUT = 45


def head(title):
    print(f"\n\033[1m── {title}\033[0m")


class _Timeout(Exception):
    pass


def safe(label, fn, timeout=PROBE_TIMEOUT):
    """Run one probe under a wall-clock cap.

    A failure or a stall in one probe must not hide the ones after it — an
    unreachable IMAP host would otherwise hang the whole report before it
    reached the calendar sections.
    """
    def _fire(signum, frame):
        raise _Timeout()

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.alarm(timeout)
    try:
        fn()
    except _Timeout:
        print(f"  {label} timed out after {timeout}s — the server did not answer")
    except Exception as e:
        print(f"  {label} failed: {type(e).__name__}: {str(e)[:200]}")
        if "-v" in sys.argv:
            traceback.print_exc()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def main():
    from core.database import (
        CalendarCal, CalendarEvent, EmailAccount, MsTodoDeletedNote, Note,
        SessionLocal,
    )

    # ── Accounts ──────────────────────────────────────────────────
    head("1. Email accounts")
    owners = []
    db = SessionLocal()
    try:
        rows = db.query(EmailAccount).all()
        if not rows:
            print("  none configured")
        for acc in rows:
            owners.append(acc.owner or "")
            print(f"  id={acc.id}  owner={acc.owner!r}  default={acc.is_default}")
            print(f"    imap={acc.imap_host}  oauth={acc.oauth_provider or 'none'}")
    finally:
        db.close()

    db = SessionLocal()
    try:
        owners += [c.owner or "" for c in db.query(CalendarCal).all()]
    finally:
        db.close()
    owner = owners[0] if owners else ""
    candidates = list(dict.fromkeys(owners + [""]))

    # ── Folders, live off IMAP ────────────────────────────────────
    head("2. Folder names, straight from IMAP")
    folders = []

    def _folders():
        from routes.email_helpers import _imap

        with _imap(None, owner=owner) as conn:
            status, raw = conn.list()
            print(f"  LIST status: {status}")
            import re

            for f in raw or []:
                decoded = f.decode() if isinstance(f, bytes) else str(f)
                m = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)
                name = (m.group(1) or m.group(2)) if m else "?"
                folders.append(name)
                flags = decoded.split(")")[0].strip("(* LIST ")
                print(f"    {name!r}   flags: {flags}")

    safe("IMAP LIST", _folders)
    print("  → Office 365 names its sent folder 'Sent Items'.")

    # ── What the Sent folder holds ────────────────────────────────
    head("3. The Sent folder")

    def _sent():
        from routes.email_helpers import _detect_sent_folder, _imap, _q

        with _imap(None, owner=owner) as conn:
            sent = _detect_sent_folder(conn)
            print(f"  detected as: {sent!r}")
            status, data = conn.select(_q(sent), readonly=True)
            print(f"  SELECT status: {status}")
            if status != "OK":
                print("  → the detected folder does not exist on this server")
                return
            print(f"  messages: {(data[0] or b'0').decode(errors='replace')}")
            st, uids = conn.uid("SEARCH", None, "ALL")
            ids = (uids[0] or b"").split()[-5:]
            for uid in reversed(ids):
                st2, fetched = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])")
                for part in fetched or []:
                    if isinstance(part, tuple) and part[1]:
                        text = part[1].decode(errors="replace").strip().replace("\r\n", " | ")
                        print(f"    uid {uid.decode()}: {text[:110]}")

    safe("Sent probe", _sent)

    # ── Microsoft calendar connection ─────────────────────────────
    head("4. Microsoft calendar accounts")

    ms_owner = [None]

    def _msaccounts():
        from src.msgraph_calendar import _load_msgraph_accounts

        for probe_owner in candidates:
            accounts = _load_msgraph_accounts(probe_owner)
            if not accounts:
                continue
            if ms_owner[0] is None:
                ms_owner[0] = probe_owner
            for acc in accounts:
                print(f"  owner={probe_owner!r}  id={acc.get('id')}  email={acc.get('email')}")
                print(f"    has refresh token: {bool(acc.get('refresh_token'))}"
                      f"   expiry: {acc.get('token_expiry')}")
        if ms_owner[0] is None:
            print(f"  none connected for any known owner (tried {candidates})")
            print("  → Settings → Integrations → Add → Microsoft 365 Calendar")
        elif ms_owner[0] != owner:
            print(f"  NOTE: connected under owner {ms_owner[0]!r}, but the mail account")
            print(f"        is owned by {owner!r}. A sync running as one cannot see the other.")

    safe("account lookup", _msaccounts)

    # ── Can we get a token, and does Graph answer ─────────────────
    head("5. Graph reachability")

    def _graph():
        from src.msgraph_calendar import (
            _graph_request, _load_msgraph_accounts, _valid_access_token,
        )

        probe_owner = ms_owner[0]
        if probe_owner is None:
            print("  skipped — no connected account to test with")
            return
        accounts = _load_msgraph_accounts(probe_owner)
        account_id = accounts[0].get("id")
        token = _valid_access_token(probe_owner, account_id)
        if not token:
            print("  could NOT obtain an access token — the sign-in has expired,")
            print("  or the client secret / tenant changed. Reconnect the calendar.")
            return
        print(f"  access token obtained ({len(token)} chars)")
        me = _graph_request(token, "GET", "/me")
        print(f"  /me → {me.get('userPrincipalName') or me.get('mail')}")
        cals = _graph_request(token, "GET", "/me/calendars", params={"$top": 25})
        for c in cals.get("value") or []:
            print(f"    calendar: {c.get('name')!r}  id={str(c.get('id'))[:24]}…")

    safe("Graph probe", _graph)

    # ── Local calendars ───────────────────────────────────────────
    head("6. Calendars stored locally")
    db = SessionLocal()
    try:
        for cal in db.query(CalendarCal).all():
            count = db.query(CalendarEvent).filter(
                CalendarEvent.calendar_id == cal.id).count()
            pending = db.query(CalendarEvent).filter(
                CalendarEvent.calendar_id == cal.id,
                CalendarEvent.caldav_sync_pending.isnot(None)).count()
            print(f"  {cal.name!r}  source={cal.source}  owner={cal.owner!r}  "
                  f"events={count}  pending_push={pending}")
    finally:
        db.close()

    # ── Force a sync ──────────────────────────────────────────────
    head("7. Forcing a two-way sync")

    def _sync():
        import asyncio

        from src.msgraph_calendar import sync_msgraph_direction

        probe_owner = owner if ms_owner[0] is None else ms_owner[0]
        print(f"  syncing as owner={probe_owner!r}")
        result = asyncio.run(sync_msgraph_direction(probe_owner, "both"))
        print(f"  {result}")

    safe("sync", _sync)

    # ── Microsoft To Do ───────────────────────────────────────────
    head("8. Microsoft To Do (tasks)")

    todo_owner = [None]

    def _todo_accounts():
        from src.msgraph_todo import _load_mstodo_accounts

        for probe_owner in candidates:
            accounts = _load_mstodo_accounts(probe_owner)
            if not accounts:
                continue
            if todo_owner[0] is None:
                todo_owner[0] = probe_owner
            for acc in accounts:
                print(f"  owner={probe_owner!r}  id={acc.get('id')}  email={acc.get('email')}")
                print(f"    has refresh token: {bool(acc.get('refresh_token'))}"
                      f"   expiry: {acc.get('token_expiry')}")
        if todo_owner[0] is None:
            print(f"  none connected for any known owner (tried {candidates})")
            print("  → Settings → Integrations → Add → Microsoft To Do")

    safe("To Do account lookup", _todo_accounts)

    def _todo_lists():
        from src.msgraph_todo import (
            _graph_request, _load_mstodo_accounts, _valid_access_token,
        )

        probe_owner = todo_owner[0]
        if probe_owner is None:
            print("  skipped — no connected account to test with")
            return
        account_id = (_load_mstodo_accounts(probe_owner)[0] or {}).get("id")
        token = _valid_access_token(probe_owner, account_id)
        if not token:
            print("  could NOT obtain an access token — the sign-in has expired,")
            print("  or Tasks.ReadWrite was never consented. Reconnect Microsoft To Do.")
            return
        print(f"  access token obtained ({len(token)} chars)")
        lists = _graph_request(token, "GET", "/me/todo/lists", params={"$top": 25})
        for lst in lists.get("value") or []:
            kind = lst.get("wellknownListName") or "custom"
            print(f"    list: {lst.get('displayName')!r}  ({kind})")

    safe("To Do lists", _todo_lists)

    # ── Local task notes ──────────────────────────────────────────
    head("9. Task notes stored locally")
    db = SessionLocal()
    try:
        from src.msgraph_todo import SYNCED_NOTE_TYPES

        rows = db.query(Note).filter(Note.note_type.in_(SYNCED_NOTE_TYPES)).all()
        linked = [nt for nt in rows if nt.remote_id]
        pending = [nt for nt in rows if nt.todo_sync_pending or not nt.remote_id]
        print(f"  task notes: {len(rows)}   linked to To Do: {len(linked)}   "
              f"waiting to push: {len(pending)}")
        owners = sorted({repr(nt.owner) for nt in rows})
        print(f"  owners present: {', '.join(owners) or '(none)'}")
        tombstones = db.query(MsTodoDeletedNote).count()
        print(f"  deletes waiting to reach To Do: {tombstones}")
    finally:
        db.close()

    head("10. Forcing a two-way task sync")

    def _todo_sync():
        import asyncio

        from src.msgraph_todo import sync_mstodo_direction

        if todo_owner[0] is None:
            print("  skipped — no connected account")
            return
        print(f"  syncing as owner={todo_owner[0]!r}")
        print(f"  {asyncio.run(sync_mstodo_direction(todo_owner[0], 'both'))}")

    safe("task sync", _todo_sync)

    print("\nDone. Nothing above includes message bodies, tokens or .env values.")


if __name__ == "__main__":
    main()
