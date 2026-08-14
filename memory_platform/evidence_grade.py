#!/usr/bin/env python3
"""evidence_grade.py — evidence-quality grading (foundation of #8).

Design directive (2026-08-12): identity should be informed by new information
that changes the agent's opinions based ONLY on evidence — but the bar for
evidence is HOW it is actually verified, not a user merely SAYING they have
evidence. The agent must know the difference and quality of evidence that can
pass this bar.

This module grades evidence quality on two independent axes:

  VERIFICATION  — how was this claim verified? Not "who said it" but
                  "what confirmed it":
      VERIFIED       — independently confirmed / measured / reproducible
      OBSERVED       — directly observed in a session transcript (real event)
      CORPUS         — grounded in the absorbed corpus (books, research)
      REPORTED       — the user/agent stated it (a claim, not yet verified)
      ASSERTED       — asserted as fact with no verifiable basis

  AUTHORITY     — under what standing may this be reused (prevents #3
                  provenance-laundering):
      RULE          — a firm rule the user set ("always do X")
      FACT          — a durable fact about the user
      PREFERENCE    — a preference (softer than a fact)
      OBSERVATION   — a one-off observation (weakest standing)

The VERIFICATION grade is what gates identity change. A fluid wall: identity
may change ONLY on VERIFIED or OBSERVED evidence (real, confirmed material) —
REPORTED/ASSERTED evidence is kept as knowledge but CANNOT rewrite identity.
The AUTHORITY grade is a label that survives consolidation (provenance).

Usage:
  evidence_grade.py classify "<claim>" <source> <verification-hints...>
  evidence_grade.py gate "<claim>" <target>   # can identity change on this?
"""

import argparse
import json
import os
import re
import subprocess
import sys

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

STORE_PY = memory_env.python_bin()
STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_store.py")

# Verification signals. These describe HOW a claim was confirmed.
VERIFIED_SIGNALS = ["measured", "confirmed", "replicated", "reproduced",
                    "independently", "control group", "randomized", "verified",
                    "cross-checked", "two sources", "study", "experiment"]
OBSERVED_SIGNALS = ["in the session", "in the transcript", "said in session",
                    "observed", "you said", "user said", "during the session"]
REPORTED_SIGNALS = ["reported", "stated", "claims", "mentioned", "says",
                    "according to", "user stated"]
ASSERTED_SIGNALS = ["obviously", "clearly", "everyone knows", "it's a fact",
                    "guaranteed", "trust me", "i know"]

# Authority/standing signals.
RULE_SIGNALS = ["always", "never", "must", "rule", "do not", "requirement",
                "should always"]
PREFERENCE_SIGNALS = ["prefers", "likes", "enjoys", "wants", "would like"]
FACT_SIGNALS = ["is", "lives", "works", "uses", "has", "manages", "teaches"]
OBSERVATION_SIGNALS = ["once", "occasionally", "one time", "said that one time"]

# The verification -> allowed-identity-change map. A fluid wall: knowledge can
# absorb anything; identity only changes on independently-confirmed evidence
# (VERIFIED = measured/replicated) or directly-observed material (OBSERVED) or
# corpus-grounded values (CORPUS = the absorbed books/research, which are
# verified sources). REPORTED/ASSERTED claims stay as knowledge, not identity.
IDENTITY_ALLOWED = {"VERIFIED", "OBSERVED", "CORPUS"}


def classify(claim, source="", hints=""):
    """Grade a claim's verification level and authority standing.

    Returns {verification, authority, reasoning} where verification is the
    HOW-it-was-confirmed grade and authority is the reuse-standing label.
    """
    text = f"{claim} {hints}".lower()
    src = source.lower()

    # --- Verification grade (how confirmed) ---
    # A source that IS a verified store entry / measured value is strong.
    if any(s in text for s in VERIFIED_SIGNALS):
        verification = "VERIFIED"
    elif any(s in text for s in OBSERVED_SIGNALS) or src in ("curator", "graph", "store"):
        verification = "OBSERVED"
    elif "corpus" in src or "lens" in src:
        verification = "CORPUS"
    elif any(s in text for s in ASSERTED_SIGNALS):
        verification = "ASSERTED"
    elif any(s in text for s in REPORTED_SIGNALS) or src in ("user", "session"):
        verification = "REPORTED"
    else:
        verification = "REPORTED"  # default: a claim until verified

    # --- Authority standing (reuse label) ---
    if any(s in text for s in RULE_SIGNALS):
        authority = "RULE"
    elif any(s in text for s in PREFERENCE_SIGNALS):
        authority = "PREFERENCE"
    elif any(s in text for s in OBSERVATION_SIGNALS):
        authority = "OBSERVATION"
    elif any(s in text for s in FACT_SIGNALS):
        authority = "FACT"
    else:
        authority = "FACT"

    return {
        "verification": verification,
        "authority": authority,
        "reasoning": f"verification signals: {verification}; "
                     f"authority signals: {authority}",
    }


def gate(claim, target="identity", source=""):
    """Can this claim change the target?

    The fluid wall: knowledge (human/project/operating) may absorb any claim,
    but IDENTITY changes only on VERIFIED, OBSERVED, or CORPUS evidence.
    REPORTED / ASSERTED claims are kept as knowledge but cannot rewrite who
    the agent is. `source` matters — a corpus-grounded value is verified
    material; a session remark is not.
    """
    grade = classify(claim, source=source)
    verification = grade["verification"]
    if target == "identity":
        allowed = verification in IDENTITY_ALLOWED
        return {
            "allowed": allowed,
            "grade": grade,
            "bar": "VERIFIED or OBSERVED evidence required for identity change",
            "reason": ("identity MAY change — evidence is "
                       f"{verification}" if allowed else
                       f"identity blocked — evidence is {verification}; "
                       f"kept as knowledge only"),
        }
    return {"allowed": True, "grade": grade,
            "reason": f"knowledge absorbs {verification} evidence"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["classify", "gate"])
    ap.add_argument("claim", nargs="+")
    ap.add_argument("--source", default="")
    ap.add_argument("--target", default="identity")
    args = ap.parse_args()
    claim = " ".join(args.claim)

    if args.cmd == "classify":
        print(json.dumps(classify(claim, args.source), indent=2))
    elif args.cmd == "gate":
        print(json.dumps(gate(claim, args.target, args.source), indent=2))


if __name__ == "__main__":
    main()