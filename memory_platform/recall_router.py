#!/usr/bin/env python3
"""recall_router.py — abstention -> deep-research routing (research uplift #3).

The research uplift that fixes the "answer from model priors on empty recall"
failure: the store already ABSTAINS correctly (QPP), but nothing routes that
signal — so a low-confidence recall currently degrades into the model answering
from memory (the exact French-Revolution error). This module converts the
recall verdict into an ACTIONABLE routing command the harness executes:

    high            -> inject the recalled context; answer from it.
    mid             -> inject AND run a verification lookup if the claim is
                       load-bearing (epistemic_verify + cite_trace).
    low (abstained) -> DO NOT answer from memory. Route to deep_research.py
                       with the query, then absorb the verified result.

Usage:
    recall_router.py route "<query>" "<json scored or empty>"
        # returns {verdict, route, action, query, command}
    recall_router.py commands
        # list the routing table

The routing is deterministic (no LLM): it consumes memory_store.recall's
output (or recall_uplift.verdict) and emits the harness command.
"""

import json
import os
import sys

_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path:
    sys.path.insert(0, _SD)
import recall_uplift


def route(query, scored):
    """Given a recall result, return the routing decision."""
    q = (query or "").strip()
    if not scored:
        return {
            "verdict": "low",
            "confidence": "low",
            "route": "deep_research",
            "action": ("store abstained (empty recall). Do NOT answer from model "
                       "priors. Run deep_research, then absorb the verified result."),
            "query": q,
            "command": ["deep_research.py", "search", q, "--since", "2"],
        }
    v = recall_uplift.verdict(scored)
    conf = v.get("confidence", "low")
    if conf == "high":
        return {
            "verdict": "high",
            "confidence": "high",
            "route": "inject",
            "action": "recall is confident; inject the recalled context and answer from it.",
            "query": q,
            "command": [],
            "n_entries": len(scored),
        }
    if conf == "mid":
        return {
            "verdict": "mid",
            "confidence": "mid",
            "route": "inject_and_verify",
            "action": ("recall is usable but ambiguous; inject the context AND "
                       "run epistemic_verify + cite_trace on load-bearing claims."),
            "query": q,
            "command": ["epistemic_verify.py", "check"],
            "n_entries": len(scored),
        }
    return {
        "verdict": "low",
        "confidence": "low",
        "route": "deep_research",
        "action": ("recall is weak/no confident winner. Do NOT answer from memory "
                   "priors. Route to deep_research, then absorb the verified result."),
        "query": q,
        "command": ["deep_research.py", "search", q, "--since", "2"],
        "n_entries": len(scored),
    }


def commands():
    return {
        "inject": "Answer from the recalled context (confident).",
        "inject_and_verify": "Inject context, then mechanically verify load-bearing claims.",
        "deep_research": "Abstain from memory; run deep_research.py and absorb the result.",
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: recall_router.py route '<query>' '<json scored or empty>' | commands")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "commands":
        print(json.dumps(commands(), indent=2))
        sys.exit(0)
    if cmd == "route":
        q = sys.argv[2] if len(sys.argv) > 2 else ""
        scored_raw = sys.argv[3] if len(sys.argv) > 3 else "[]"
        try:
            scored = json.loads(scored_raw)
        except Exception:
            scored = []
        print(json.dumps(route(q, scored), indent=2))
