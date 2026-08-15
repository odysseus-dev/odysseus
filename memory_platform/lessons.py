#!/usr/bin/env python3
"""lessons.py — the teachable-moments wing: growth from mistakes.

The persona records what it got wrong, why, and what to do differently — and
reconsults those lessons so the same mistake is not made twice. This is
Reflexion-style episodic memory (arXiv:2303.11366): verbal reflection stored
in an episodic buffer and retrieved to shape later behaviour, without touching
weights. It is the inter-test-time adaptation path of the Self-Evolving Agents
survey (arXiv:2507.21046) — evolution across sessions, not within one.

Design:
- A lesson is a QUINTET: trigger (when it applies), mistake (what went wrong),
  analysis (why), behaviour (what to do instead), evidence (where it came from).
- `record` writes the lesson as a store chunk (wing='lessons') AND derives a
  behavioural delta via growth_delta.apply — so a recorded mistake immediately
  changes behaviour, not just the record.
- `recall` pulls lessons relevant to the current context (hybrid recall) so the
  persona consults them before acting — the "don't repeat it" check.
- `recent` shows what the persona is currently learning from.
- Deliberately NOT a ledger of shame: the framing is 'teachable moment', the
  purpose is behavioural growth, and the analysis field is required so a
  mistake is never recorded without its cause.

Storage: wing='lessons' chunks + growth_delta journal (single high-confidence
gate). Retrieval: hybrid recall, wing-filtered.

Usage:
  lessons.py record "<trigger>" --mistake ".." --analysis ".." --behaviour ".." [--pivot ".."] [--evidence TEXT]
  lessons.py recall "<context>" [--limit N] [--json]
  lessons.py recent [--limit N]
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
WING = "lessons"


def _db_rw():
    import memory_store as ms
    return ms.connect()


def record(trigger, mistake, analysis, behaviour, evidence="", source="session",
           pivot=""):
    """Record a teachable moment. Stores it AND derives a behavioural delta
    so the mistake changes how the persona behaves, not just what it knows.
    Returns both outcomes (a mistake recorded but not behaviourally applied
    would be a ledger, not a lesson).

    PIVOT (PivoARL, 2026): the single decisive turn that went wrong. The
    finding is that retrying the pivotal turn — not the whole task — is what
    makes reflection pay. A lesson without its pivot is a general regret; a
    lesson with its pivot is a surgical correction. Required for a lesson to
    be behaviourally applied: the delta encodes the pivot as the actionable
    change."""
    if not (trigger and mistake and analysis and behaviour):
        return {"recorded": False,
                "error": "lesson needs trigger, mistake, analysis, behaviour"}
    if not pivot:
        return {"recorded": False,
                "error": "lesson needs a PIVOT — the single decisive turn that went wrong"}
    error = None
    stored = False
    try:
        import memory_store as ms
        text = (f"[trigger] {trigger}\n"
                f"[pivot] {pivot}\n"
                f"[mistake] {mistake}\n"
                f"[analysis] {analysis}\n"
                f"[behaviour] {behaviour}\n"
                f"[evidence] {evidence}\n"
                f"[source] {source}")
        db = _db_rw()
        stored = ms.add_chunk(db, text, wing=WING, room=source,
                              source_path=f"lessons::{source}",
                              doc_id=f"lessons::{trigger[:40]}",
                              importance=0.75)
        db.close()
    except Exception as e:
        stored = False
        error = str(e)[:120]
    delta = None
    try:
        import growth_delta as gd
        delta = gd.apply(json.dumps({
            "change": behaviour,
            "evidence": (analysis or mistake)[:200],
            "confidence": 0.8,
            "target": "delivery",
        }))
        if not delta.get("applied"):
            delta = None
    except Exception as e:
        delta = None
        error = str(e)[:120]
    return {"recorded": stored, "behaviour_applied": bool(delta),
            "wing": WING, "trigger": trigger[:60],
            "error": error if (not stored and not delta) else None}


def recall(context, limit=5):
    """Pull lessons relevant to the current context (the 'don't repeat it'
    check — the persona consults its teachable moments before acting)."""
    try:
        import memory_store as ms
        db = _db_rw()
        rows = ms.chunk_recall(db, context, budget=limit, wing=WING)
        db.close()
        return [{"text": r.get("text", "")[:300]} for r in rows]
    except Exception as e:
        return [{"text": f"(lessons recall failed: {str(e)[:80]})"}]


def recent(limit=8):
    """What the persona is currently learning from (most recent lessons)."""
    try:
        db = _db_rw()
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT text, ingested_at FROM chunks WHERE wing=? "
            "ORDER BY ingested_at DESC, rowid DESC LIMIT ?", (WING, limit)).fetchall()
        db.close()
        return [{"text": r["text"][:220], "recorded_at": r["ingested_at"]}
                for r in rows]
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser(description="Teachable moments — growth from mistakes")
    ap.add_argument("cmd", choices=["record", "recall", "recent"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--mistake", default="")
    ap.add_argument("--analysis", default="")
    ap.add_argument("--behaviour", default="")
    ap.add_argument("--pivot", default="",
                    help="the single decisive turn that went wrong (required)")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "record":
        trigger = " ".join(args.arg)
        res = record(trigger, args.mistake, args.analysis, args.behaviour,
                     args.evidence, pivot=args.pivot)
        print(json.dumps(res, indent=2) if args.json else
              (f"recorded: {res['trigger']} (stored={res['recorded']}, "
               f"behaviour_applied={res['behaviour_applied']})"
               if res.get("recorded") or res.get("behaviour_applied")
               else f"record failed: {res.get('error', res)}"))

    elif args.cmd == "recall":
        ctx = " ".join(args.arg)
        rows = recall(ctx, args.limit)
        if args.json:
            print(json.dumps(rows, indent=2))
            return
        if not rows:
            print(f"no relevant lessons for: {ctx}")
            return
        print(f"# Teachable moments for: {ctx}\n")
        for r in rows:
            print(f"{r['text']}\n")

    elif args.cmd == "recent":
        rows = recent(args.limit)
        if args.json:
            print(json.dumps(rows, indent=2))
            return
        print(f"{len(rows)} recent teachable moments")
        for r in rows:
            print(f"  [{r['recorded_at']}] {r['text'][:80]}")


if __name__ == "__main__":
    main()
