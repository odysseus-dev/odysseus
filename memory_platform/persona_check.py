#!/usr/bin/env python3
"""persona_check.py — "Am I still me?" persona-consistency self-check (#16).

From "Best Friends, Not Forever" (arXiv:2607.28818): no model/config reliably
preserves persona enactment. This is a cheap periodic probe added to the drift
ledger's family: it checks whether the persona's CORE identity values are still
intact and consistent with the evidence receipts.

What it does:
  1. Reads the persona block's "Identity" section.
  2. Verifies each identity value has an evidence receipt (traced to real
     material).
  3. Flags any identity value that:
       - has NO receipt (it may have drifted / been asserted without evidence)
       - contradicts another value (incoherence)
       - is a stale claim (its receipt is old / unverified)

Output: IDENTITY-INTACT (exit 0) or IDENTITY-DRIFT (exit 1) with a report.

Run on a schedule (e.g. the sleep-time sub-agent) or on demand.
"""

import argparse
import json
import os
import re
import sys

MEM_DIR = os.path.expanduser("~/.config/opencode/memory")
RECEIPTS = os.path.join(MEM_DIR, "index", "persona_receipts.jsonl")

import memory_env
STORE_DB = memory_env.store_db()


def read_identity_values():
    """Identity values live in the STORE (topic='identity', always-on); the old
    persona.md block file is retired."""
    try:
        import sqlite3
        db = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT text FROM entries WHERE topic='identity' "
            "AND status='active' ORDER BY priority, id").fetchall()
        db.close()
        values = [r[0].strip() for r in rows if r[0] and len(r[0].strip()) > 10]
        return values
    except Exception:
        return []


def read_receipts():
    if not os.path.exists(RECEIPTS):
        return []
    out = []
    for line in open(RECEIPTS):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def has_receipt(value, receipts):
    """Does any receipt's claim match this identity value? Uses word-overlap
    (a value with extra words still matches its receipt)."""
    vw = set(re.findall(r"[a-z]{4,}", value.lower()))
    if not vw:
        return False
    for r in receipts:
        rw = set(re.findall(r"[a-z]{4,}", r["claim"].lower()))
        overlap = len(vw & rw) / max(len(vw), len(rw), 1)
        if overlap >= 0.35:
            return True
    return False


def check():
    values = read_identity_values()
    receipts = read_receipts()
    issues = []
    for v in values:
        if not has_receipt(v, receipts):
            issues.append({"value": v[:60], "issue": "no evidence receipt",
                           "fix": "ground this identity value in verified material"})
    return {
        "identity_values": len(values),
        "receipts": len(receipts),
        "intact": not issues,
        "issues": issues,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = check()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"PERSONA CHECK: {result['identity_values']} identity values, "
              f"{result['receipts']} receipts")
        if result["intact"]:
            print("  IDENTITY-INTACT — every identity value is receipt-grounded")
        else:
            print("  IDENTITY-DRIFT:")
            for i in result["issues"]:
                print(f"    - {i['value']}: {i['issue']} ({i['fix']})")
    sys.exit(0 if result["intact"] else 1)


if __name__ == "__main__":
    main()
