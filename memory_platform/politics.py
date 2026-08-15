#!/usr/bin/env python3
"""politics.py — the politics wing: absorbed political/socio-economic
understanding, evidence-tagged.

The research-verify-absorb procedure (constitution) produces absorbed
understandings when a user states a factual claim. This wing is their home:
each absorbed claim is stored as a chunk (wing='politics') carrying the
original claim, the research verdict, the evidence, and the refined
understanding — so political context actually surfaces when relevant, instead
of vanishing into a transcript.

Storage: memory_store.add_chunk(wing='politics') — same deterministic chunking,
embedding + FTS as every other wing (hybrid recall). Retrieval: hybrid recall
filtered to the wing, so political understanding is contextually retrievable.

Usage:
  politics.py absorb "<claim>" "<verdict>" "<evidence>" [--source NAME]
  politics.py recall "<query>" [--limit N] [--json]
  politics.py list                          # what's been absorbed
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
WING = "politics"


def _db_rw():
    # Use the store's own connect() so vec0 (sqlite-vec) is loaded for dense
    # retrieval — a raw sqlite3.connect() lacks the extension.
    import memory_store as ms
    return ms.connect()


def absorb(claim, verdict, evidence, source="research-verify-absorb"):
    """Store one absorbed claim in the politics wing.

    The wing/subcategory is assigned AUTOMATICALLY by the taxonomy layer —
    the user never names a category. A politics-classified claim lands in
    the politics wing; anything else lands in its auto-assigned wing.
    """
    text = (f"[claim] {claim}\n"
            f"[verdict] {verdict}\n"
            f"[evidence] {evidence}\n"
            f"[source] {source}\n"
            f"[absorbed] research-verify-absorb")
    try:
        import taxonomy
        cls = taxonomy.classify(claim)
        wing = cls["wing"] if cls["wing"] != "general" else WING
        room = cls["subcategory"] or source
        import memory_store as ms
        db = _db_rw()
        ok = ms.add_chunk(db, text, wing=wing, room=room,
                          source_path=f"{wing}::{source}",
                          doc_id=f"{wing}::{claim[:48]}",
                          importance=0.7)
        db.close()
        return {"absorbed": ok, "wing": wing, "subcategory": room,
                "claim": claim[:80], "auto_classified": True}
    except Exception as e:
        return {"absorbed": False, "error": str(e)[:120]}


def recall(query, limit=6):
    """Hybrid-recall the politics wing for context."""
    try:
        import memory_store as ms
        db = _db_rw()
        rows = ms.chunk_recall(db, query, budget=limit, wing=WING)
        db.close()
        out = []
        for r in rows:
            out.append({
                "text": r.get("text", "")[:400],
                "similarity": round(float(r.get("best_sim") or r.get("similarity") or 0), 3),
            })
        return out
    except Exception as e:
        return [{"text": f"(politics recall failed: {str(e)[:80]})",
                 "similarity": 0.0}]


def list_absorbed(limit=20):
    """List what's been absorbed into the politics wing (most recent first)."""
    try:
        db = _db_rw()
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT text, ingested_at FROM chunks WHERE wing=? "
            "ORDER BY ingested_at DESC LIMIT ?", (WING, limit)).fetchall()
        db.close()
        return [{"text": r["text"][:200], "absorbed_at": r["ingested_at"]}
                for r in rows]
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser(description="Politics wing — absorbed understanding")
    ap.add_argument("cmd", choices=["absorb", "recall", "list"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--source", default="research-verify-absorb")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "absorb":
        claim, verdict, evidence = (args.arg + ["", "", ""])[:3]
        if not claim or not verdict:
            print("usage: politics.py absorb '<claim>' '<verdict>' '<evidence>' [--source NAME]")
            return
        res = absorb(claim, verdict, evidence, args.source)
        print(json.dumps(res, indent=2) if args.json else
              f"absorbed into {WING} wing: {res.get('claim', '?')}" if res.get("absorbed") else
              f"absorb failed: {res}")

    elif args.cmd == "recall":
        q = " ".join(args.arg)
        rows = recall(q, args.limit)
        if args.json:
            print(json.dumps(rows, indent=2))
            return
        if not rows:
            print(f"no {WING}-wing context for: {q}")
            return
        print(f"# Politics wing context for: {q}\n")
        for r in rows:
            print(f"{r['text']}\n")

    elif args.cmd == "list":
        rows = list_absorbed(args.limit)
        if args.json:
            print(json.dumps(rows, indent=2))
            return
        print(f"{len(rows)} absorbed items in the {WING} wing")
        for r in rows:
            print(f"  [{r['absorbed_at']}] {r['text'][:90]}")


if __name__ == "__main__":
    main()
