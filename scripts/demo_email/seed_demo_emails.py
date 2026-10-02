#!/usr/bin/env python3
"""Seed a throwaway, local-only mailbox with fake demo emails.

This populates the `demo@odysseus.local` Dovecot account (which has NO mbsync
channel, so nothing here ever touches a real server) with a curated, obviously
fake but realistic set of messages — varied senders, read/unread/flagged mix, a
reply thread, an attachment, a newsletter, a calendar invite, an urgent one, and
a spammy one — so the email assistant's summarize / reply / tag / spam / calendar
features can be shown off without exposing any real mail.

Idempotent: `--reset` wipes every mailbox in the demo account first, so re-running
gives a clean, identical inbox every time.

Usage:
    python seed_demo_emails.py            # append demo mail (creates folders)
    python seed_demo_emails.py --reset    # wipe the demo account, then re-seed
    python seed_demo_emails.py --reset --wipe-only   # just empty it

Connection mirrors the app's local-Dovecot account (localhost:31143, STARTTLS).
Override via env: DEMO_IMAP_HOST/PORT/USER/PASSWORD.
"""
from __future__ import annotations

import argparse
import imaplib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
import os

HOST = os.getenv("DEMO_IMAP_HOST", "localhost")
PORT = int(os.getenv("DEMO_IMAP_PORT", "31143"))
USER = os.getenv("DEMO_IMAP_USER", "demo@odysseus.local")
PASSWORD = os.getenv("DEMO_IMAP_PASSWORD", "demodemo")

# Marker header on every message we create — lets a human (or a future cleanup)
# tell demo mail apart at a glance.
MARKER = ("X-Odysseus-Demo", "1")

DEMO_OWNER_ADDR = USER  # the demo "you"

# The "could've just been a search" email gets a FIXED Message-ID so we can
# pre-seed a matching cached AI reply (keyed by Message-ID) in the app's email
# cache DB — the read path attaches it as cached_ai_reply. This makes the
# "my agent already looked it up and drafted the answer" beat reliable on stage.
LOOKUP_MSGID = "<demo-lookup-deepseek@odysseus.local>"
# The app's email cache lives at <repo>/data/scheduled_emails.db (email_summaries,
# email_ai_replies, ... keyed by Message-ID).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DB = _REPO_ROOT / "data" / "scheduled_emails.db"


def _connect() -> imaplib.IMAP4:
    """Connect + STARTTLS like the app does."""
    conn = imaplib.IMAP4(HOST, PORT)
    conn.starttls()
    conn.login(USER, PASSWORD)
    return conn


def _tiny_pdf(title: str) -> bytes:
    """A minimal but valid one-page PDF, so the attachment is real and openable."""
    body = (
        f"BT /F1 18 Tf 72 700 Td ({title}) Tj ET\n"
        "BT /F1 12 Tf 72 670 Td (This is a fake demo invoice. Not a real charge.) Tj ET"
    ).encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(body), body),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
            % (len(objs) + 1, xref_pos))
    return bytes(out)


def _ics(summary: str, start: datetime, mins: int) -> str:
    end = start + timedelta(minutes=mins)
    fmt = "%Y%m%dT%H%M%SZ"
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Odysseus Demo//EN\r\n"
        "METHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
        f"UID:{make_msgid()}\r\n"
        f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}\r\n"
        f"DTSTART:{start.strftime(fmt)}\r\nDTEND:{end.strftime(fmt)}\r\n"
        f"SUMMARY:{summary}\r\nLOCATION:Video call\r\n"
        "ORGANIZER;CN=Demo Team:mailto:calendar@dfx522.example\r\n"
        f"ATTENDEE;CN=You:mailto:{DEMO_OWNER_ADDR}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


def _msg(*, frm, to=None, subject, text, html=None, days_ago=0, hours_ago=0,
         in_reply_to=None, references=None, msg_id=None,
         pdf=None, pdf_name="invoice.pdf", ics=None) -> tuple[EmailMessage, datetime]:
    m = EmailMessage()
    m["From"] = frm
    m["To"] = to or f"You <{DEMO_OWNER_ADDR}>"
    m["Subject"] = subject
    when = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours_ago)
    m["Date"] = formatdate(when.timestamp(), localtime=False)
    m["Message-ID"] = msg_id or make_msgid(domain="odysseus.local")
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = references or in_reply_to
    m[MARKER[0]] = MARKER[1]
    m.set_content(text)
    if html:
        m.add_alternative(html, subtype="html")
    if pdf is not None:
        m.add_attachment(pdf, maintype="application", subtype="pdf", filename=pdf_name)
    if ics is not None:
        m.add_attachment(ics.encode("utf-8"), maintype="text", subtype="calendar",
                         filename="invite.ics", params={"method": "REQUEST"})
    return m, when


def build_dataset() -> list[dict]:
    """Returns a list of {mailbox, flags, msg, when} dicts. Themed/playful, fake."""
    items: list[dict] = []

    def add(mailbox, flags, msg_when):
        msg, when = msg_when
        items.append({"mailbox": mailbox, "flags": flags, "msg": msg, "when": when})

    # 1. Recruiter — unread. (Subject has an emoji to also show the mono-emoji render.)
    add("INBOX", "", _msg(
        frm="Brogan O'Hara <talent@northstar-labs.example>",
        subject="We want you on the Northstar AI team 🚀",
        days_ago=0, hours_ago=2,
        text=("Hey,\n\nSaw your work on the Odysseus stack — seriously impressive. "
              "We're building an agentic AI platform and your name keeps coming up.\n\n"
              "Any chance you're open to a quick chat this week? Comp is competitive and "
              "the team is fully remote.\n\nCheers,\nBrogan\nHead of Talent, Northstar Labs"),
        html=("<p>Hey,</p><p>Saw your work on the <b>Odysseus</b> stack — seriously "
              "impressive. We're building an agentic AI platform and your name keeps coming "
              "up.</p><p>Any chance you're open to a quick chat this week? Comp is competitive "
              "and the team is fully remote.</p><p>Cheers,<br>Brogan<br><i>Head of Talent, "
              "Northstar Labs</i></p>")))

    # 1b. The "could've just been a search" email — unread, newest (top of inbox).
    #     Fixed Message-ID so we can pre-seed the agent's researched reply.
    add("INBOX", "", _msg(
        frm="Greg <greg@odysseus-demo.example>",
        subject="quick q for the slide — DeepSeek-V3 param count?",
        msg_id=LOOKUP_MSGID, days_ago=0, hours_ago=0,
        text=("hey! sorry to bug you — in a meeting and someone asked and i'm "
              "blanking: how many parameters does DeepSeek-V3 actually have, total "
              "vs active? need it for the comparison slide. could you look it up real "
              "quick? 🙏\n\nty!\nGreg"),
        html=("<p>hey! sorry to bug you — in a meeting and someone asked and i'm "
              "blanking: <b>how many parameters does DeepSeek-V3 actually have, total "
              "vs active?</b> need it for the comparison slide. could you look it up "
              "real quick? 🙏</p><p>ty!<br>Greg</p>")))

    # 2. Newsletter — unread.
    add("INBOX", "", _msg(
        frm="Local Models Weekly <news@localmodels.example>",
        subject="This week in local AI: tiny models, big benchmarks",
        days_ago=1,
        text=("LOCAL MODELS WEEKLY — Issue #142\n\n"
              "• Local LLMs that fit in a shoebox GPU\n"
              "• Why your RAG pipeline needs evaluation\n"
              "• Cave of the week: someone ran 8x4090D in a closet\n\n"
              "Unsubscribe any time.")))

    # 3. Reply thread — original is in Sent, the reply lands unread in INBOX.
    orig_id = make_msgid(domain="odysseus.local")
    add("Sent", "(\\Seen)", _msg(
        frm=f"You <{DEMO_OWNER_ADDR}>",
        to="Alex <alex@creator.example>",
        subject="stream setup for Saturday",
        msg_id=orig_id, days_ago=2,
        text=("Yo — for Saturday's stream, are we doing the dual-PC setup or just the "
              "one rig? Need to know before I cable everything.\n\n- You")))
    add("INBOX", "", _msg(
        frm="Alex <alex@creator.example>",
        subject="Re: stream setup for Saturday",
        in_reply_to=orig_id, references=orig_id,
        days_ago=0, hours_ago=5,
        text=("Dual-PC, definitely. Last time the single rig choked when we ran the "
              "AI overlay + OBS + the game. Bring the capture card too.\n\n"
              "Thanks,\nAlex")))

    # 4. Invoice with a real PDF attachment.
    add("INBOX", "(\\Seen)", _msg(
        frm="CloudCompute Billing <billing@cloudcompute.example>",
        subject="Your invoice #DFX-2042 is ready",
        days_ago=3,
        text=("Hi,\n\nYour CloudCompute invoice #DFX-2042 for $42.00 is attached "
              "(GPU minutes, May).\n\nNo action needed — auto-charged to your card on "
              "the 1st.\n\n— CloudCompute"),
        pdf=_tiny_pdf("Invoice #DFX-2042 - $42.00"), pdf_name="invoice_DFX-2042.pdf"))

    # 5. Calendar invite (ICS attachment + explicit time in body).
    nextmon = datetime.now(timezone.utc) + timedelta(days=(7 - datetime.now().weekday()) % 7 or 7)
    nextmon = nextmon.replace(hour=10, minute=0, second=0, microsecond=0)
    add("INBOX", "", _msg(
        frm="Demo Team <calendar@dfx522.example>",
        subject="Invitation: Demo Team sync — Monday 10:00",
        days_ago=0, hours_ago=20,
        text=("You're invited to the weekly Demo Team sync.\n\n"
              f"When: Monday {nextmon:%b %d} at 10:00 UTC (30 min)\n"
              "Where: Video call\n\n"
              "Agenda: stall-detector rollout, emoji icons, demo prep."),
        ics=_ics("Demo Team sync", nextmon, 30)))

    # 6. Urgent — flagged + unread.
    add("INBOX", "(\\Flagged)", _msg(
        frm="Ops Bot <ops@odysseus-demo.example>",
        subject="[URGENT] prod is on fire 🔥 — odysseus-ui 502s",
        days_ago=0, hours_ago=1,
        text=("PAGE: odysseus-ui is returning 502s on the /api/chat endpoint.\n"
              "Error rate 38% over the last 5 min. Last deploy was 12 min ago.\n\n"
              "Need eyes ASAP. Reply here or join the incident call.")))

    # 7. Spammy — obvious, for the spam verdict.
    add("INBOX", "", _msg(
        frm="Prize Department <winner@totally-legit-prizes.example>",
        subject="CONGRATULATIONS!!! You have WON 1,000,000 GOLD COINS!!!",
        days_ago=4,
        text=("Dear Lucky Winner,\n\nYou have been SELECTED to receive ONE MILLION "
              "gold coins!!! To claim, simply reply with your bank details and "
              "a small processing fee of 50 coins.\n\nACT NOW — offer expires in 3 hours!!!\n\n"
              "Totally Legit Prizes Inc.")))

    # 8. A normal, already-read personal one.
    add("INBOX", "(\\Seen)", _msg(
        frm="Mom <mom@family.example>",
        subject="did you eat??",
        days_ago=1, hours_ago=3,
        text=("hi sweetie just checking did you eat today. you work too much on the "
              "computer. call me. love mom xoxo")))

    return items


def _seed_cache() -> None:
    """Pre-seed the app's email cache so the lookup email arrives with a summary
    and an AI reply that has clearly 'done the search' (answer + source)."""
    if not CACHE_DB.parent.exists():
        print(f"  (skip cache seed: {CACHE_DB.parent} missing)")
        return
    reply = (
        "Hi Greg,\n\n"
        "Looked it up — DeepSeek-V3 is a 671B-parameter Mixture-of-Experts model, "
        "with 37B parameters active per token (256 routed experts + 1 shared). It "
        "was trained on ~14.8T tokens and ships with a 128K-token context window.\n\n"
        "Source: DeepSeek-V3 Technical Report (arXiv:2412.19437) and the official "
        "model card on Hugging Face.\n\n"
        "Hope that unblocks the slide!\n\n"
        "— drafted for you by your Odysseus assistant"
    )
    summary = ("Greg needs the DeepSeek-V3 parameter count (total vs active) for a "
               "comparison slide. Quick factual lookup — answerable with a search.")
    now = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(str(CACHE_DB))
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS email_ai_replies (
            message_id TEXT PRIMARY KEY, uid TEXT, folder TEXT, reply TEXT NOT NULL,
            model_used TEXT, created_at TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS email_summaries (
            message_id TEXT PRIMARY KEY, uid TEXT, folder TEXT, subject TEXT,
            sender TEXT, summary TEXT NOT NULL, model_used TEXT, created_at TEXT NOT NULL)""")
        con.execute(
            "INSERT OR REPLACE INTO email_ai_replies "
            "(message_id, uid, folder, reply, model_used, created_at) VALUES (?,?,?,?,?,?)",
            (LOOKUP_MSGID, "", "INBOX", reply, "demo", now))
        con.execute(
            "INSERT OR REPLACE INTO email_summaries "
            "(message_id, uid, folder, subject, sender, summary, model_used, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (LOOKUP_MSGID, "", "INBOX", "quick q for the slide — DeepSeek-V3 param count?",
             "greg@odysseus-demo.example", summary, "demo", now))
        con.commit()
        print("  pre-seeded cached AI reply + summary for the lookup email.")
    finally:
        con.close()


def _clear_cache() -> None:
    """Remove the pre-seeded cache rows for the lookup email (best-effort)."""
    if not CACHE_DB.exists():
        return
    con = sqlite3.connect(str(CACHE_DB))
    try:
        for tbl in ("email_ai_replies", "email_summaries"):
            try:
                con.execute(f"DELETE FROM {tbl} WHERE message_id = ?", (LOOKUP_MSGID,))
            except sqlite3.OperationalError:
                pass
        con.commit()
    finally:
        con.close()


def _ensure_mailbox(conn: imaplib.IMAP4, name: str) -> None:
    if name.upper() == "INBOX":
        return
    typ, _ = conn.select(name)
    if typ != "OK":
        conn.create(name)


def _wipe(conn: imaplib.IMAP4) -> int:
    """Delete every message in every mailbox of this (throwaway) account.

    Guard: the connection params are env-overridable, so refuse to run the
    destructive expunge unless the target is unmistakably the local demo
    account — otherwise a misconfigured DEMO_IMAP_USER/HOST could irreversibly
    wipe a real mailbox. Override only with DEMO_ALLOW_WIPE=1 (you must mean it).
    """
    safe_target = USER.endswith("@odysseus.local") or HOST in ("localhost", "127.0.0.1", "::1")
    if not safe_target and os.getenv("DEMO_ALLOW_WIPE") != "1":
        raise SystemExit(
            f"refusing to wipe non-demo target {USER}@{HOST}:{PORT} — "
            f"set DEMO_ALLOW_WIPE=1 to override")
    typ, boxes = conn.list()
    n = 0
    names = []
    if typ == "OK":
        for raw in boxes:
            line = raw.decode(errors="replace")
            # last token, possibly quoted, is the mailbox name
            name = line.split(' "/" ')[-1].split(' "." ')[-1].strip().strip('"')
            names.append(name)
    for name in set(names) | {"INBOX"}:
        if conn.select(name)[0] != "OK":
            continue
        typ, data = conn.search(None, "ALL")
        if typ == "OK" and data and data[0]:
            ids = data[0].split()
            for i in ids:
                conn.store(i, "+FLAGS", "\\Deleted")
            n += len(ids)
            conn.expunge()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="wipe the account before seeding")
    ap.add_argument("--wipe-only", action="store_true", help="only wipe, don't seed")
    args = ap.parse_args()

    try:
        conn = _connect()
    except Exception as e:
        print(f"ERROR: could not connect to {USER}@{HOST}:{PORT} — {e}", file=sys.stderr)
        print("Is the Dovecot user created + Dovecot reloaded?", file=sys.stderr)
        return 1

    try:
        if args.reset or args.wipe_only:
            removed = _wipe(conn)
            _clear_cache()
            print(f"wiped {removed} message(s) from {USER}")
            if args.wipe_only:
                return 0

        items = build_dataset()
        for it in items:
            _ensure_mailbox(conn, it["mailbox"])
            dt = imaplib.Time2Internaldate(it["when"].timestamp())
            conn.append(it["mailbox"], it["flags"], dt, it["msg"].as_bytes())
        _seed_cache()
        print(f"seeded {len(items)} demo message(s) into {USER} "
              f"(INBOX + Sent). Switch to the 'Demo' account in Odysseus to view.")
        return 0
    finally:
        try:
            conn.logout()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
