#!/usr/bin/env python3
"""socratic.py — the actional belief-revision loop (Socratic method, made structural).

PRINCIPLE (user directive): if a better argument informs the system of a more
consistent perspective, the system must ADOPT that perspective and follow
through — not merely concede in conversation while holding the old action.
Growth is organic; epistemic integrity requires that a concession moves the
ACTIONABLE rules, or records a principled reason it doesn't.

This is the structural support: every argument is ledgered, every concession
must either amend a rule or justify not amending it, and nothing is held
silently.

The loop (Socratic):
  thesis  -> record the position
  critique -> record the opposing argument
  concede -> if the critique is accepted:
               * either WRITE the revised understanding as a rule (amendment)
               * or RECORD why the rule stands despite the concession
  coherence -> the ledger verifies no conceded argument has an un-updated rule

Integration: amendments go through constitution-add.py (the constitution's
only writer — read_only is preserved; this layer SUPPLIES the amendment that
constitution-add executes, it does not bypass it).

Usage:
  socratic.py thesis "topic" "the position"
  socratic.py critique "topic" "the opposing argument"
  socratic.py concede "topic" --amend "new rule text" ["--rule constitution"]
  socratic.py concede "topic" --hold "principled reason the rule stands"
  socratic.py coherence            # verify no un-updated concessions
  socratic.py ledger               # show the argument history
"""

import argparse
import json
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from . import memory_env
except ImportError:
    import memory_env

STORE_DB = memory_env.store_db()
LEDGER_TOPIC = "socratic"


def connect():
    db = sqlite3.connect(STORE_DB)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS socratic (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            kind TEXT NOT NULL,       -- thesis | critique | concede | hold
            body TEXT NOT NULL,
            amended TEXT DEFAULT '',  -- rule text if this concession amended one
            hold_reason TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    db.commit()
    return db


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(kind, topic, body, amended="", hold_reason=""):
    db = connect()
    db.execute(
        "INSERT INTO socratic (topic, kind, body, amended, hold_reason, "
        "created_at) VALUES (?,?,?,?,?,?)",
        (topic, kind, body, amended, hold_reason, _now()))
    db.commit()
    eid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return eid


def amend_rule(topic, new_rule, target="operating", supersedes=None):
    """WRITE a conceded understanding as an actionable rule.

    Goes through the store as an operating/persona entry. ACTIONAL GROWTH:
    if `supersedes` names an old rule, that old rule is retired (marked
    superseded) — the change is real, not additive bloat. This is the "
    growth must change the actionable thing" requirement: a conceded
    argument either changes the rule the agent acts on, or records why not.
    """
    db = connect()
    # dedupe: same rule text already present?
    exists = db.execute(
        "SELECT id FROM entries WHERE topic=? AND text=? AND status='active'",
        (target, new_rule)).fetchone()
    if exists:
        db.close()
        return {"amended": False, "reason": "rule already present"}
    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO entries (text, importance, created_at, last_accessed, "
        "topic, source, method, status, valid_from, confidence, temperature, "
        "always_on, priority) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (new_rule, 0.8, ts, ts, target, "socratic", "socratic", "active",
         ts, 0.9, 1.0, 1, 4))
    eid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    # retire the superseded rule — the old action is replaced, not accumulated
    if supersedes:
        old = db.execute(
            "SELECT id FROM entries WHERE topic=? AND text=? AND status='active'",
            (target, supersedes)).fetchone()
        if old:
            try:
                db.execute(
                    "UPDATE entries SET status='superseded', valid_until=? "
                    "WHERE id=?", (ts, old["id"]))
            except Exception:
                db.execute("UPDATE entries SET status='superseded' WHERE id=?",
                           (old["id"],))
    db.commit()
    db.close()
    return {"amended": True, "topic": target, "rule": new_rule,
            "superseded": bool(supersedes), "new_id": eid}


def coherence():
    """Verify: every CONCEDED argument either amended a rule or recorded a
    principled hold. Any concede without amendment/hold is a COHERENCE GAP —
    the concession was conversational but never made actional."""
    db = connect()
    rows = db.execute(
        "SELECT * FROM socratic WHERE kind='concede' ORDER BY id").fetchall()
    db.close()
    gaps = []
    for r in rows:
        if not r["amended"] and not r["hold_reason"]:
            gaps.append({"id": r["id"], "topic": r["topic"],
                         "body": r["body"][:80]})
    return {"checked": len(rows), "gaps": gaps,
            "coherent": len(gaps) == 0}


def ledger(topic=None):
    db = connect()
    if topic:
        rows = db.execute(
            "SELECT * FROM socratic WHERE topic=? ORDER BY id", (topic,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM socratic ORDER BY id").fetchall()
    db.close()
    return [{"id": r["id"], "topic": r["topic"], "kind": r["kind"],
             "body": r["body"], "amended": r["amended"],
             "hold_reason": r["hold_reason"]} for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["thesis", "critique", "concede", "coherence",
                                    "ledger", "check-consent"])
    ap.add_argument("topic", nargs="?", default="")
    ap.add_argument("text", nargs="*", default=None)
    ap.add_argument("--amend", default="", help="new rule text to adopt")
    ap.add_argument("--hold", default="", help="principled reason rule stands")
    ap.add_argument("--target", default="operating",
                    help="where to write an amended rule (operating/persona)")
    ap.add_argument("--supersedes", default="",
                    help="text of the old rule this amendment replaces")
    args = ap.parse_args()

    if args.cmd == "coherence":
        c = coherence()
        print(json.dumps(c, indent=2))
        return
    if args.cmd == "ledger":
        print(json.dumps(ledger(args.topic or None), indent=2))
        return
    if args.cmd == "check-consent":
        # CONSENT GATE (corrected 2026-08-15): the Socratic loop applies ONLY
        # when the user consents to argue about something. Default is evidence-
        # when-available, not perpetual questioning. This returns whether the
        # argumentative mode should engage at all.
        topic = args.topic or " ".join(args.text)
        print(json.dumps({
            "verdict": "GATE",
            "instruction": (
                "Do NOT engage the Socratic/argument loop unless the user has "
                "explicitly consented to argue about this. Otherwise the "
                "evidence is sufficient — present it, absorb what survives, "
                "and stop. The real Socratic method is a midwife, not a "
                "wrestler: it serves the user's understanding, never 'wins.' "
                "If you are reaching for 'the evidence says...' to deflect a "
                "conclusion you find uncomfortable, that is the facsimile — "
                "check whether the user consented to argue before continuing."),
            "topic": topic,
        }, indent=2))
        return

    body = " ".join(args.text) if args.text else ""
    if args.cmd == "thesis":
        eid = record("thesis", args.topic, body)
        print(f"thesis recorded ({eid})")
    elif args.cmd == "critique":
        eid = record("critique", args.topic, body)
        print(f"critique recorded ({eid})")
    elif args.cmd == "concede":
        eid = record("concede", args.topic, body,
                     amended=args.amend, hold_reason=args.hold)
        if args.amend:
            res = amend_rule(args.topic, args.amend, target=args.target,
                             supersedes=args.supersedes or None)
            print(f"concede recorded ({eid}); amendment: {res}")
        elif args.hold:
            print(f"concede recorded ({eid}); hold with reason: {args.hold[:60]}")
        else:
            print(f"concede recorded ({eid}) but NO amendment and NO hold — "
                  f"this is a COHERENCE GAP (run `coherence` to see it)")


if __name__ == "__main__":
    main()
