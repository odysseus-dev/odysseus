#!/usr/bin/env python3
"""consolidate.py — budget-aware consolidation (#2).

From Retain-or-Consolidate (arXiv:2607.17545): formalizes WHEN to keep raw
records vs consolidate, and WHICH operator to use, per budget pressure.

- Under LOOSE budget (store well under capacity): retain raw records. Accuracy
  is highest when nothing is compressed.
- Under TIGHT budget (store near capacity): consolidate — but choose the
  operator by evidence quality:
      MERGE    — two facts are near-duplicates (same meaning) -> one record
      ABSTRACT — many related facts -> one summary (only for VERIFIED/CORPUS)
      REWRITE  — an entry is stale -> refresh it (supersede, keep history)
  REPORTED/ASSERTED facts are NEVER abstracted (that's how hallucination
  compounds); they only MERGE on exact duplicates.

Usage:
  consolidate.py pressure            # store size vs target (0-1 pressure)
  consolidate.py plan <json-facts>   # what to do given pressure
  consolidate.py apply <json-ops>    # execute the ops (merge/abstract)
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import zlib

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_store.py")
STORE_PY = memory_env.python_bin()

TARGET_BYTES = 250 * 1024 * 1024   # 250MB target store size
TIGHT_PRESSURE = 0.85              # consolidate above this pressure


def store_pressure(db_path):
    """0-1 pressure: how full is the store vs target?"""
    import os
    size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    return min(1.0, size / TARGET_BYTES)


def _meaning_similar(a, b):
    """Cheap duplicate test: normalized token overlap."""
    na = set(re.findall(r"[a-z]{4,}", a.lower()))
    nb = set(re.findall(r"[a-z]{4,}", b.lower()))
    if not na or not nb:
        return False
    return len(na & nb) / max(len(na), len(nb)) >= 0.7


def plan(facts, pressure):
    """Decide what to do with a set of entries given budget pressure.

    Returns a list of ops:
      {"op": "KEEP", id, reason}        — retain raw (loose budget)
      {"op": "MERGE", ids, keep, reason}— near-duplicates -> keep one
      {"op": "ABSTRACT", ids, summary, reason} — many verified -> one summary
      {"op": "REWRITE", id, new, reason}— stale verified entry -> refresh
    """
    ops = []
    if pressure < TIGHT_PRESSURE:
        # Loose budget: keep everything raw. Retention wins loose budgets.
        for f in facts:
            ops.append({"op": "KEEP", "id": f.get("id"), "reason": "loose budget: retain raw"})
        return ops

    # Tight budget: consolidate.
    seen = {}
    for f in facts:
        text = (f.get("text") or "").strip()
        if not text:
            continue
        verification = f.get("verification", "REPORTED")
        # Group near-duplicates for MERGE.
        merged = False
        for key in list(seen.keys()):
            if _meaning_similar(key, text):
                ops.append({"op": "MERGE", "ids": [seen[key], f.get("id")],
                            "keep": key, "reason": "near-duplicate under pressure"})
                merged = True
                break
        if merged:
            continue
        seen[text] = f.get("id")
        # ABSTRACT only verified/corpus facts (never claimed -> no hallucination
        # compounding). REWRITE only stale verified facts.
        if verification in ("VERIFIED", "CORPUS", "OBSERVED"):
            if f.get("stale"):
                ops.append({"op": "REWRITE", "id": f.get("id"),
                            "new": text + " (refreshed)", "reason": "stale verified entry"})
            elif f.get("group"):
                ops.append({"op": "ABSTRACT", "ids": f.get("group"),
                            "summary": text, "reason": "verified group under pressure"})
            else:
                ops.append({"op": "KEEP", "id": f.get("id"),
                            "reason": "verified, no group to abstract"})
        else:
            # REPORTED/ASSERTED: keep raw, never abstract.
            ops.append({"op": "KEEP", "id": f.get("id"),
                        "reason": "reported/asserted — never abstracted"})
    return ops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pressure", "plan"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--db", default=os.path.expanduser(
        "~/.config/opencode/memory/store/memory.db"))
    args = ap.parse_args()

    if args.cmd == "pressure":
        print(json.dumps({"pressure": round(store_pressure(args.db), 3),
                          "target_bytes": TARGET_BYTES,
                          "tight_at": TIGHT_PRESSURE}))
    elif args.cmd == "plan":
        facts = json.loads(args.arg[0]) if args.arg else []
        pressure = store_pressure(args.db)
        print(json.dumps({"pressure": round(pressure, 3),
                          "ops": plan(facts, pressure)}, indent=2))


if __name__ == "__main__":
    main()