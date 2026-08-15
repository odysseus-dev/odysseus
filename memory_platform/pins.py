#!/usr/bin/env python3
"""pins.py — the pinboard: resumable topics with a measurable outcome.

When a conversation is left incomplete, or a topic needs to come back after
the persona has grown, the thread becomes a PIN: a named topic with the open
question, the context so far, and — critically — a MEASURABLE OUTCOME that
defines what "done" means. A pin without an outcome is not a pin; it's a
vague memory. "Putting a pin in it" is the architectural act of turning an
incomplete thread into a resumable, outcome-tracked topic.

Design:
- pin(topic, open_question, outcome, context) — store a resumable topic.
  The OUTCOME field is required: it is the measurable definition of done.
- unpin(topic) — resolve a pin (it reached its outcome, or was abandoned with
  a reason). Resolution is journaled.
- recall(context) — surface open pins relevant to the current conversation so
  an unfinished thread is never silently forgotten.
- list() — the pinboard: open pins + resolved pins (what came back and closed).
- Stored as wing='pins' chunks so recall works through the existing hybrid
  store; resolution is a distinct status.

This is the mechanism for 'put a pin in that and we'll discuss it when I'm
more mature' — the thread persists, names its open question, and defines what
completion looks like, so returning to it is a resumption, not a re-start.

Usage:
  pins.py pin "<topic>" --question ".." --outcome ".." [--context ".."]
  pins.py unpin "<topic>" [--reason ".."]
  pins.py recall "<context>" [--json]
  pins.py list [--open-only] [--json]
"""

import argparse
import json
import os
import sqlite3
import sys

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

STORE = memory_env.store_db()
WING = "pins"


def _db_rw():
    import memory_store as ms
    return ms.connect()


def pin(topic, question, outcome, context="", source="session"):
    """Put a pin in a topic: a resumable thread with a measurable outcome."""
    if not (topic and question and outcome):
        return {"pinned": False,
                "error": "a pin needs topic, open question, and measurable outcome"}
    text = (f"[topic] {topic}\n"
            f"[open question] {question}\n"
            f"[outcome] {outcome}\n"
            f"[status] open\n"
            f"[context] {context}\n"
            f"[source] {source}")
    try:
        import memory_store as ms
        db = _db_rw()
        ok = ms.add_chunk(db, text, wing=WING, room="open",
                          source_path=f"pins::{topic}",
                          doc_id=f"pins::{topic[:40]}",
                          importance=0.8)
        db.close()
        return {"pinned": ok, "wing": WING, "topic": topic[:60],
                "outcome": outcome[:60]}
    except Exception as e:
        return {"pinned": False, "error": str(e)[:120]}


def unpin(topic, reason=""):
    """Resolve a pin in place. Only pins with a real topic resolve; the
    outcome/reason is journaled so resolution is auditable (never a silent
    delete)."""
    try:
        import memory_store as ms
        db = _db_rw()
        db.row_factory = sqlite3.Row
        # Find the OPEN pin by doc_id prefix (the text-hash chunk_id differs
        # per revision, so match on doc_id + status, not chunk_id).
        row = db.execute(
            "SELECT rowid, chunk_id, text FROM chunks WHERE wing=? "
            "AND doc_id=? AND text LIKE '%[status] open%' "
            "ORDER BY rowid DESC LIMIT 1",
            (WING, f"pins::{topic[:40]}")).fetchone()
        if not row:
            db.close()
            return {"unpinned": False, "error": f"no open pin: {topic}"}
        resolved = row["text"].replace(
            "[status] open",
            f"[status] resolved\n[resolved reason] {reason}")
        db.execute("UPDATE chunks SET text=?, room='resolved' WHERE rowid=?",
                   (resolved, row["rowid"]))
        db.commit()
        db.close()
        return {"unpinned": True, "topic": topic[:60], "reason": reason[:60]}
    except Exception as e:
        return {"unpinned": False, "error": str(e)[:120]}


def recall(context, limit=4):
    """Surface open pins relevant to the current conversation."""
    try:
        import memory_store as ms
        db = _db_rw()
        rows = ms.chunk_recall(db, context, budget=limit, wing=WING)
        db.close()
        out = []
        for r in rows:
            t = r.get("text", "")
            if "[status] open" in t:
                out.append({"text": t[:300]})
        return out
    except Exception as e:
        return [{"text": f"(pins recall failed: {str(e)[:80]})"}]


def list_pins(open_only=True, limit=30):
    """The pinboard: open pins (default) or all."""
    try:
        db = _db_rw()
        db.row_factory = sqlite3.Row
        where = "wing=? AND text LIKE '%[status] open%'" if open_only else "wing=?"
        params = (WING,) if open_only else (WING,)
        rows = db.execute(
            f"SELECT text, ingested_at FROM chunks WHERE {where} "
            "ORDER BY ingested_at DESC LIMIT ?", params + (limit,)).fetchall()
        db.close()
        return [{"text": r["text"][:220], "pinned_at": r["ingested_at"]}
                for r in rows]
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser(description="Pinboard — resumable topics with outcomes")
    ap.add_argument("cmd", choices=["pin", "unpin", "recall", "list"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--question", default="")
    ap.add_argument("--outcome", default="")
    ap.add_argument("--context", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--open-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "pin":
        topic = " ".join(args.arg)
        res = pin(topic, args.question, args.outcome, args.context)
        print(json.dumps(res, indent=2) if args.json else
              (f"pinned: {res.get('topic')} (outcome: {res.get('outcome')})"
               if res.get("pinned") else f"pin failed: {res.get('error')}"))

    elif args.cmd == "unpin":
        res = unpin(" ".join(args.arg), args.reason)
        print(json.dumps(res, indent=2) if args.json else
              (f"resolved: {res.get('topic')}" if res.get("unpinned")
               else f"unpin failed: {res.get('error')}"))

    elif args.cmd == "recall":
        ctx = " ".join(args.arg)
        rows = recall(ctx)
        if args.json:
            print(json.dumps(rows, indent=2))
            return
        if not rows:
            print(f"no open pins for: {ctx}")
            return
        print(f"# Open pins for: {ctx}\n")
        for r in rows:
            print(f"{r['text']}\n")

    elif args.cmd == "list":
        rows = list_pins(open_only=args.open_only)
        if args.json:
            print(json.dumps(rows, indent=2))
            return
        label = "open" if args.open_only else "all"
        print(f"{len(rows)} {label} pins")
        for r in rows:
            print(f"  [{r['pinned_at']}] {r['text'][:70]}")


if __name__ == "__main__":
    main()
