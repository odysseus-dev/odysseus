#!/usr/bin/env python3
"""reason_traces.py — memory of HOW I solved problems (#17).

From eMoT (arXiv:2606.02054): reasoning trajectories as memories — distinct
from fact-memory. When a problem is solved well, the REASONING PATH is stored,
so next time a similar problem appears, the agent recalls the approach, not
just the answer. This directly helps coding and research issues.

Storage: memory/index/reason_traces.jsonl — append-only, each trace is the
problem -> approach -> outcome with a hash for integrity.

Usage:
  reason_traces.py record "<problem>" "<approach>" "<outcome>" [--tags a,b]
  reason_traces.py find "<problem>"      # find similar past approaches
  reason_traces.py list                  # all traces
"""

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone

MEM_DIR = os.path.expanduser("~/.config/opencode/memory")
TRACES = os.path.join(MEM_DIR, "index", "reason_traces.jsonl")


def _tokens(text):
    return set(re.findall(r"[a-z]{4,}", (text or "").lower()))


def record(problem, approach, outcome, tags=None):
    os.makedirs(os.path.dirname(TRACES), exist_ok=True)
    entry = {
        "problem": problem,
        "approach": approach,
        "outcome": outcome,
        "tags": tags or [],
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    h = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:16]
    entry["hash"] = h
    with open(TRACES, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def find(problem, limit=3, threshold=0.2):
    """Find past traces relevant to a problem (tag + word overlap)."""
    if not os.path.exists(TRACES):
        return []
    pt = _tokens(problem)
    scored = []
    for line in open(TRACES):
        if not line.strip():
            continue
        try:
            t = json.loads(line)
        except Exception:
            continue
        tags = t.get("tags") or []
        tag_hit = any(tag.lower() in problem.lower() for tag in tags)
        overlap = len(pt & _tokens(t["problem"])) / max(len(pt), 1)
        score = (0.5 if tag_hit else 0) + overlap
        scored.append((score, t))
    scored.sort(key=lambda x: -x[0])
    return [t for s, t in scored if s >= threshold][:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["record", "find", "list"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--tags", default="")
    args = ap.parse_args()

    if args.cmd == "record":
        problem = args.arg[0] if args.arg else ""
        approach = args.arg[1] if len(args.arg) > 1 else ""
        outcome = " ".join(args.arg[2:])
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        print(json.dumps(record(problem, approach, outcome, tags)))
    elif args.cmd == "find":
        problem = " ".join(args.arg)
        print(json.dumps(find(problem), indent=2))
    elif args.cmd == "list":
        if not os.path.exists(TRACES):
            print("[]")
            return
        print("".join(open(TRACES)))


if __name__ == "__main__":
    main()
