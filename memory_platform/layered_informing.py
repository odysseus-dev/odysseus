#!/usr/bin/env python3
"""layered_informing.py — meaningful discoveries propagate between layers.

The layered design (2026-08-12): ALL layers should be able to inform each other to
deliver results and improve efficiency — but ONLY when meaningful discoveries
are found by any layer. Trivial or unverified noise never crosses layers.

The layers:
  SUBSTANCE   — constitution, worthiness filter (what's worth absorbing)
  EVIDENCE    — evidence_grade (verified/claimed), coherence gate
  MEMORY      — the hybrid store (entries, recall, associations, scenes)
  PERSONA     — identity values, receipts, persona_check
  CLAIM       — claim_audit (anti-overclaim findings)
  RESEARCH    — research_lens (baloney detection, relevance)
  DRIFT       — drift-ledger (drift auto-fix findings)

A DISCOVERY is a meaningful, verified insight one layer found that another
layer could use. It must be:
  - meaningful (above triviality — a real pattern, not noise)
  - verified (evidence-graded, not asserted)
  - actionable (another layer can use it)

Each discovery carries the emitting layer, a target (which layer(s) it should
improve), the finding, and its evidence grade. Only VERIFIED/OBSERVED/CORPUS
discoveries propagate (the fluid wall applies to cross-layer informing too).

Storage: memory/index/discoveries.jsonl — append-only.

Usage:
  layered_informing.py emit <layer> <target> <finding> [--evidence VERIFIED] [--source X]
  layered_informing.py pending                     # discoveries for other layers
  layered_informing.py feed <layer>                # what this layer should ingest
  layered_informing.py apply <layer> <id>          # mark a discovery applied
  layered_informing.py stats
"""

import argparse
import json
import os
import re
from datetime import datetime, timezone

MEM_DIR = os.path.expanduser("~/.config/opencode/memory")
DISCOVERIES = os.path.join(MEM_DIR, "index", "discoveries.jsonl")

# Discoveries below this evidence grade never propagate (the fluid wall).
ALLOWED_GRADES = {"VERIFIED", "OBSERVED", "CORPUS"}

# Which layers can inform which other layers (the "all layers inform each
# other" graph — but only on meaningful, verified discoveries).
FEED_GRAPH = {
    "substance": ["memory", "persona", "claim"],   # worthiness finds: route better
    "evidence":  ["persona", "memory", "substance"], # verified/claimed patterns
    "memory":    ["persona", "research", "substance"], # associations, recall patterns
    "persona":   ["claim", "memory", "substance"],  # identity findings
    "claim":     ["persona", "substance", "research"], # overclaim patterns to avoid
    "research":  ["substance", "memory", "persona"],   # source-reliability findings
    "drift":     ["memory", "persona", "substance"],   # drift causes to prevent
}

# Trivial findings that are NOT meaningful enough to propagate.
TRIVIAL = [
    "canary", "test", "probe", "parkbench", "health check",
    "roundtrip", "query_helpers", "no drift", "0 items",
]


def _is_meaningful(finding):
    low = (finding or "").lower()
    if len(finding) < 15:
        return False
    if any(t in low for t in TRIVIAL):
        return False
    # Must describe a real pattern/insight, not a status update.
    meaningful_markers = ["tend", "pattern", "improve", "avoid", "prefer",
                         "better", "worse", "fail", "works", "because",
                         "should", "not", "recur", "stale", "unreliable"]
    return any(m in low for m in meaningful_markers)


def emit(layer, target, finding, evidence="VERIFIED", source=""):
    """Record a discovery from one layer for another. Only meaningful +
    verified discoveries propagate."""
    if not _is_meaningful(finding):
        return {"emitted": False, "reason": "not meaningful (trivial or noise)"}
    if evidence not in ALLOWED_GRADES:
        return {"emitted": False,
                "reason": f"evidence {evidence} below the propagation bar"}
    os.makedirs(os.path.dirname(DISCOVERIES), exist_ok=True)
    entry = {
        "id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
        "from": layer, "to": target, "finding": finding,
        "evidence": evidence, "source": source, "applied": False,
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(DISCOVERIES, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return {"emitted": True, "id": entry["id"]}


def feed(layer):
    """Discoveries this layer should ingest (emitted by other layers)."""
    if not os.path.exists(DISCOVERIES):
        return []
    out = []
    for line in open(DISCOVERIES):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not d.get("applied") and d.get("to") == layer:
            out.append(d)
    return out


def pending():
    """All un-applied discoveries."""
    if not os.path.exists(DISCOVERIES):
        return []
    out = []
    for line in open(DISCOVERIES):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not d.get("applied"):
            out.append(d)
    return out


def apply_discovery(layer, did):
    """Mark a discovery applied (the receiving layer ingested it)."""
    if not os.path.exists(DISCOVERIES):
        return {"ok": False, "reason": "no discoveries file"}
    lines = open(DISCOVERIES).readlines()
    changed = False
    for i, line in enumerate(lines):
        if did in line:
            try:
                d = json.loads(line)
                d["applied"] = True
                d["applied_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                lines[i] = json.dumps(d) + "\n"
                changed = True
            except Exception:
                pass
            break
    if changed:
        with open(DISCOVERIES, "w") as f:
            f.write("".join(lines))
        return {"ok": True}
    return {"ok": False, "reason": f"no discovery with id {did}"}


def stats():
    if not os.path.exists(DISCOVERIES):
        return {"total": 0, "emitted_by": {}, "applied": 0}
    emitted = {}
    applied = 0
    total = 0
    for line in open(DISCOVERIES):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        total += 1
        emitted[d["from"]] = emitted.get(d["from"], 0) + 1
        if d.get("applied"):
            applied += 1
    return {"total": total, "emitted_by": emitted, "applied": applied}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["emit", "pending", "feed", "apply", "stats"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--evidence", default="VERIFIED")
    ap.add_argument("--source", default="")
    args = ap.parse_args()

    if args.cmd == "emit":
        layer = args.arg[0] if args.arg else ""
        target = args.arg[1] if len(args.arg) > 1 else ""
        finding = " ".join(args.arg[2:])
        print(json.dumps(emit(layer, target, finding, args.evidence, args.source)))
    elif args.cmd == "pending":
        print(json.dumps(pending(), indent=2))
    elif args.cmd == "feed":
        layer = args.arg[0] if args.arg else ""
        print(json.dumps(feed(layer), indent=2))
    elif args.cmd == "apply":
        layer = args.arg[0] if args.arg else ""
        did = args.arg[1] if len(args.arg) > 1 else ""
        print(json.dumps(apply_discovery(layer, did)))
    elif args.cmd == "stats":
        print(json.dumps(stats(), indent=2))


if __name__ == "__main__":
    main()
