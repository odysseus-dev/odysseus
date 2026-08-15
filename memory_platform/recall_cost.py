#!/usr/bin/env python3
"""recall_cost.py — cost-aware retrieval routing (agent-native memory, 2026).

The agent-native memory finding (arXiv:2606.24775) is that retrieval quality
alone is the wrong metric: OPERATIONAL COST is a first-class concern, and
localised maintenance beats global reorganisation. This module wraps the
store's recall and journals the true cost of each retrieval, so the tiering
can adapt and costs are measurable — the prerequisite for 'superior retrieval'
that is PROVEN, not just claimed.

What it measures per recall:
- entries returned (context tokens they'd inject)
- dense vs BM25 hit ratio (which tier did the work)
- abstention events (recall that correctly returned nothing)
- cumulative cost (journaled to <memory>/journal/recall-cost-<month>.md)

Usage (wrapper — same contract as memory_store.recall):
    from recall_cost import recall
    entries, scores = recall(db, query, budget=8)

Or standalone reporting:
    recall_cost.py report              # cost journal summary
    recall_cost.py recall "<q>"        # one recall + its cost
"""

import argparse
import json
import os
import sys

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

STORE = memory_env.store_db()
JOURNAL_DIR = os.path.join(memory_env.memory_dir(), "journal")

# Approx tokens per entry for cost accounting (chars/4, same as warm tier).
TOK_PER_CHAR = 0.25


def _journal(cost):
    try:
        import fcntl, datetime
        os.makedirs(JOURNAL_DIR, exist_ok=True)
        month = datetime.datetime.now().strftime("%Y-%m")
        path = os.path.join(JOURNAL_DIR, f"recall-cost-{month}.md")
        lock = open(os.path.join(JOURNAL_DIR, ".recall-cost.lock"), "a")
        fcntl.flock(lock, fcntl.LOCK_EX)
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        line = (f"`{ts}` q='{cost.get('query','')[:50]}' "
                f"tokens={cost['tokens']} entries={cost['entries']} "
                f"dense={cost['dense_ratio']:.0%} abstained={cost['abstained']}")
        with open(path, "a") as f:
            f.write(line + "\n")
        fcntl.flock(lock, fcntl.LOCK_UN)
    except Exception:
        pass


def recall(db, query, budget=8, min_score=0.0, journal=True):
    """Cost-aware recall: same result as memory_store.recall, plus a cost
    report. Returns (result, cost) so callers can route on cost without
    losing the retrieval. Abstains exactly like the store (no distractors)."""
    import memory_store as ms
    q = (query or "").strip()
    if not q:
        return [], {"query": q, "tokens": 0, "entries": 0,
                    "dense_ratio": 0.0, "abstained": True}
    result = ms.recall(db, q, budget=budget, min_score=min_score)
    if isinstance(result, tuple):
        entries, scores = result
    else:
        entries, scores = result, []
    tokens = sum(len((e.get("text") or "")) for e in entries) * TOK_PER_CHAR
    # Estimate which tier did the work: entries with a strong dense score came
    # through the semantic tier; the rest through BM25.
    dense_hits = sum(1 for s in scores if s >= 0.7) if scores else 0
    cost = {
        "query": q,
        "tokens": int(tokens),
        "entries": len(entries),
        "dense_ratio": (dense_hits / len(entries)) if entries else 0.0,
        "abstained": len(entries) == 0,
    }
    if journal:
        _journal(cost)
    return entries, cost


def report(limit=30):
    """Summarise the recall-cost journal (what retrieval is costing)."""
    try:
        import datetime, re
        month = datetime.datetime.now().strftime("%Y-%m")
        path = os.path.join(JOURNAL_DIR, f"recall-cost-{month}.md")
        with open(path) as f:
            lines = f.readlines()
        total_tokens = 0
        abstentions = 0
        for ln in lines[-limit:]:
            m = re.search(r"tokens=(\d+)", ln)
            if m:
                total_tokens += int(m.group(1))
            if "abstained=True" in ln:
                abstentions += 1
        return {"entries": len(lines[-limit:]),
                "tokens_in_window": total_tokens,
                "abstentions": abstentions, "path": path}
    except Exception:
        return {"entries": 0, "tokens_in_window": 0, "abstentions": 0}


def main():
    ap = argparse.ArgumentParser(description="Cost-aware retrieval routing")
    ap.add_argument("cmd", choices=["report", "recall"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "report":
        r = report()
        print(json.dumps(r, indent=2) if args.json else
              f"recall cost: {r['entries']} journaled calls, "
              f"{r['tokens_in_window']} tokens, {r['abstentions']} abstentions")
        return

    q = " ".join(args.arg)
    if not q:
        print("usage: recall_cost.py recall '<query>'")
        return
    import memory_store as ms
    db = ms.connect()
    entries, cost = recall(db, q)
    db.close()
    if args.json:
        print(json.dumps({"entries": [e.get("text", "")[:120] for e in entries],
                          "cost": cost}, indent=2))
        return
    print(f"{len(entries)} entries, {cost['tokens']} tokens, "
          f"dense={cost['dense_ratio']:.0%}, abstained={cost['abstained']}")
    for e in entries[:4]:
        print(f"  - {(e.get('text') or '')[:80]}")


if __name__ == "__main__":
    main()
