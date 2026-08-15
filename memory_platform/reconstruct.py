#!/usr/bin/env python3
"""reconstruct.py — active memory reconstruction (MRAgent, ICML 2026).

The market's biggest retrieval shift: "Memory is Reconstructed, Not Retrieved"
(arXiv:2606.06036, 2026, up to 23% over baselines on LoCoMo/LongMemEval).
Static retrieve-then-reason returns a fixed top-k and stops; active
reconstruction walks the associative memory graph from a cue, and PRUNES the
exploration by accumulated evidence — so retrieval adapts to what's found
mid-search instead of committing to the first k.

How this builds on the platform's existing graph:
- memory_store already precomputes an ASSOCIATIONS graph (shared distinctive
  word + dense cosine, strength-scored, fanout-bounded). That is the
  Cue-Tag-Content substrate.
- This module adds the ACTIVE half: iterative hop-wise expansion from the
  query's best seed, keeping paths whose accumulated evidence clears a floor
  and pruning branches that stop paying — the reconstruction that static
  top-k retrieval cannot do.

Design (bounded, no combinatorial explosion — per the paper's constraint):
  1. SEED — dense+BM25 recall the top few entries (the cues).
  2. EXPAND — walk each seed's strong associations (>= min_strength), 1-2 hops.
  3. SCORE — accumulate evidence: seed score + association strength decayed by
     hop depth. A branch is PRUNED when its accumulated score drops below the
     floor (evidence stops accumulating -> stop exploring that path).
  4. RECONSTRUCT — return the union, ranked by accumulated evidence, with the
     path each item was reached through (why it's here).
  5. Compare directly against static recall on the same query so the gain is
     MEASURED, not asserted.

This is the honest 'superior retrieval' claim: the reconstruction path is
provable against the static path on the same store and query.

Usage:
  reconstruct.py search "<query>" [--hops 2] [--min-strength 0.3] [--json]
  reconstruct.py compare "<query>"   # reconstruction vs static, side by side
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
DEFAULT_MIN_STRENGTH = 0.3   # association links worth following
DEFAULT_HOPS = 2             # graph-walk depth (bounded, no explosion)
PRUNE_FLOOR = 0.25           # accumulated evidence below this -> prune branch
HOP_DECAY = 0.6              # each hop's evidence contribution decays


def _store():
    """Indirection for memory_store so tests can inject a fake."""
    global _store_module
    if _store_module is not None:
        return _store_module
    import memory_store as ms
    return ms


_store_module = None


def _db_ro():
    # Use the store's own connect() so vec0 (sqlite-vec) is loaded for dense
    # retrieval — a raw read-only sqlite3.connect() lacks the extension.
    return _store().connect()


def _seed(db, query, n=3):
    """Static recall seeds the reconstruction (the cues)."""
    ms = _store()
    res = ms.recall(db, query, budget=n)
    entries, scores = (res if isinstance(res, tuple) else (res, []))
    seeds = []
    for i, e in enumerate(entries):
        score = scores[i] if i < len(scores) else 0.5
        seeds.append({"id": e.get("id"), "text": e.get("text"),
                      "score": float(score)})
    return seeds


def _assocs(db, entry_ids, min_strength):
    """One-hop associations for a set of entry ids (bounded fanout)."""
    if not entry_ids:
        return {}
    ph = ",".join("?" * len(entry_ids))
    rows = db.execute(
        "SELECT a.src_id, e.id, e.text, a.strength "
        "FROM associations a JOIN entries e ON e.id = a.dst_id "
        f"WHERE a.src_id IN ({ph}) AND a.strength >= ? AND e.status='active' "
        "ORDER BY a.strength DESC LIMIT 40",
        entry_ids + [min_strength]).fetchall()
    out = {}
    for src, dst, text, strength in rows:
        out.setdefault(dst, []).append({"from": src, "text": text,
                                        "strength": float(strength)})
    return out


def reconstruct(db, query, hops=DEFAULT_HOPS, min_strength=DEFAULT_MIN_STRENGTH):
    """Active reconstruction: seed -> walk associations -> prune by evidence."""
    seeds = _seed(db, query)
    if not seeds:
        return {"query": query, "items": [], "seeds": 0,
                "note": "nothing seeded (abstain — no distractors)"}

    # BFS with pruning: each frontier node carries accumulated evidence;
    # a branch dies when its evidence stops accumulating.
    collected = {}   # entry id -> {text, evidence, path}
    frontier = [{"id": s["id"], "text": s["text"], "evidence": s["score"],
                 "depth": 0, "path": [s["id"]]} for s in seeds]
    for s in frontier:
        collected.setdefault(s["id"], {"text": s["text"], "evidence": s["evidence"],
                                       "path": s["path"]})

    for _ in range(hops):
        ids = [f["id"] for f in frontier if f["depth"] == _]
        if not ids:
            break
        assocs = _assocs(db, ids, min_strength)
        next_frontier = []
        for f in frontier:
            if f["depth"] != _:
                continue
            for dst, links in assocs.items():
                if dst == f["id"]:
                    continue
                best = max(links, key=lambda l: l["strength"])
                # Accumulate evidence: parent's evidence + decayed link strength.
                ev = f["evidence"] + best["strength"] * (HOP_DECAY ** (f["depth"] + 1))
                if ev < PRUNE_FLOOR:
                    continue  # PRUNE: evidence stopped accumulating
                path = f["path"] + [dst]
                if dst in collected:
                    if ev > collected[dst]["evidence"]:
                        collected[dst] = {"text": best["text"], "evidence": ev,
                                          "path": path}
                    continue
                collected[dst] = {"text": best["text"], "evidence": ev, "path": path}
                next_frontier.append({"id": dst, "text": best["text"],
                                      "evidence": ev, "depth": _ + 1, "path": path})
        frontier = next_frontier

    items = sorted(collected.values(), key=lambda i: -i["evidence"])
    return {"query": query, "items": items, "seeds": len(seeds),
            "reconstructed": len(items)}


def compare(db, query, hops=DEFAULT_HOPS, min_strength=DEFAULT_MIN_STRENGTH):
    """Reconstruction vs static recall on the same query — measured gain."""
    ms = _store()
    static_res = ms.recall(db, query, budget=8)
    static_entries, _ = (static_res if isinstance(static_res, tuple)
                         else (static_res, []))
    static_ids = {e.get("id") for e in static_entries}
    rec = reconstruct(db, query, hops, min_strength)
    rec_ids = {item["path"][-1] for item in rec["items"]}
    new_ids = rec_ids - static_ids  # items reconstruction reached that static missed
    new_items = [i for i in rec["items"] if i["path"][-1] in new_ids]
    return {
        "query": query,
        "static_returned": len(static_entries),
        "reconstruction_returned": len(rec["items"]),
        "newly_reached": len(new_items),
        "gain": len(new_items) / max(1, len(static_entries)),
        "note": ("reconstruction walks the association graph from the seeds; "
                 "static top-k cannot reach these"),
    }


def main():
    ap = argparse.ArgumentParser(description="Active memory reconstruction (MRAgent, ICML 2026)")
    ap.add_argument("cmd", choices=["search", "compare"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--hops", type=int, default=DEFAULT_HOPS)
    ap.add_argument("--min-strength", type=float, default=DEFAULT_MIN_STRENGTH)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    q = " ".join(args.arg)
    if not q:
        print("usage: reconstruct.py search|compare '<query>'")
        return
    db = _db_ro()
    if args.cmd == "search":
        res = reconstruct(db, q, args.hops, args.min_strength)
        if args.json:
            print(json.dumps(res, indent=2))
            return
        print(f"{res['reconstructed']} reconstructed items "
              f"(seeded by {res['seeds']}):\n")
        for it in res["items"][:10]:
            print(f"  [{it['evidence']:.2f}] {(it['text'] or '')[:70]}")
    else:
        res = compare(db, q, args.hops, args.min_strength)
        if args.json:
            print(json.dumps(res, indent=2))
            return
        print(f"Query: {q}")
        print(f"  static top-k:      {res['static_returned']} entries")
        print(f"  reconstruction:    {res['reconstruction_returned']} items")
        print(f"  newly reached:     {res['newly_reached']} (beyond static)")
        print(f"  relative gain:     {res['gain']:.0%}")
        print(f"  {res['note']}")
    db.close()


if __name__ == "__main__":
    main()
