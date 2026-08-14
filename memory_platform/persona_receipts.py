#!/usr/bin/env python3
"""persona_receipts.py — evidence receipts for personality/identity claims (#7).

Design directive: every persona/identity claim should point to the evidence
that justifies it — a receipt file trail. When the identity lens proposes "I
value X", a receipt records WHERE that value came from (which corpus value,
which session, which evidence) and its verification grade. This makes identity
auditable: any "I am X" can be traced to the material that grounded it.

Storage: memory/index/persona_receipts.jsonl — append-only, one receipt per
identity claim, hash-linked to the claim for integrity.

Usage:
  persona_receipts.py add "<claim>" <verification> <source> <evidence-text>
  persona_receipts.py get "<claim>"     # find receipts for a claim
  persona_receipts.py verify            # check hash-chain integrity
  persona_receipts.py list              # all receipts
"""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone

MEM_DIR = os.path.expanduser("~/.config/opencode/memory")
RECEIPTS = os.path.join(MEM_DIR, "index", "persona_receipts.jsonl")


def _hash(claim, verification, source, evidence):
    return hashlib.sha256(
        f"{claim}|{verification}|{source}|{evidence}".encode()
    ).hexdigest()[:16]


def add_receipt(claim, verification, source, evidence=""):
    """Append a receipt for an identity claim. Idempotent per hash."""
    os.makedirs(os.path.dirname(RECEIPTS), exist_ok=True)
    h = _hash(claim, verification, source, evidence)
    existing = [l for l in open(RECEIPTS) if l.strip()] if os.path.exists(RECEIPTS) else []
    for line in existing:
        if h in line:
            return {"added": False, "hash": h, "reason": "duplicate"}
    entry = {
        "claim": claim, "verification": verification, "source": source,
        "evidence": evidence, "hash": h,
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(RECEIPTS, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return {"added": True, "hash": h}


def get_receipts(claim):
    """Find all receipts for a claim (substring match on claim)."""
    if not os.path.exists(RECEIPTS):
        return []
    out = []
    for line in open(RECEIPTS):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            if claim.lower() in e["claim"].lower():
                out.append(e)
        except Exception:
            continue
    return out


def verify():
    """Verify every receipt's hash matches its content (integrity)."""
    if not os.path.exists(RECEIPTS):
        return {"ok": True, "count": 0}
    ok = 0
    bad = []
    for i, line in enumerate(open(RECEIPTS)):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            expected = _hash(e["claim"], e["verification"], e["source"],
                             e.get("evidence", ""))
            if e["hash"] == expected:
                ok += 1
            else:
                bad.append(i)
        except Exception:
            bad.append(i)
    return {"ok": not bad, "count": ok, "bad": bad}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["add", "get", "verify", "list"])
    ap.add_argument("arg", nargs="*", default=[])
    args = ap.parse_args()

    if args.cmd == "add":
        claim = args.arg[0]
        verification = args.arg[1] if len(args.arg) > 1 else "REPORTED"
        source = args.arg[2] if len(args.arg) > 2 else ""
        evidence = " ".join(args.arg[3:])
        print(json.dumps(add_receipt(claim, verification, source, evidence)))
    elif args.cmd == "get":
        claim = " ".join(args.arg)
        print(json.dumps(get_receipts(claim), indent=2))
    elif args.cmd == "verify":
        print(json.dumps(verify(), indent=2))
    elif args.cmd == "list":
        if not os.path.exists(RECEIPTS):
            print("[]")
            return
        print("".join(open(RECEIPTS)))


if __name__ == "__main__":
    main()
