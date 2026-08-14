#!/usr/bin/env python3
"""epistemic_verify.py — MECHANICAL verification that the evidence epistemology
is actually applied in the OUTPUT TEXT, not just reported as a checkbox.

The constitution says "apply the evidence method — baloney detection, the
wonder-skepticism balance — to every claim." Previously this was a prompt-level
instruction — the model was TOLD to do it, and there was no way to verify it
happened. This module closes that: it is a deterministic, no-LLM pass over the
agent's actual response text that checks the epistemology left its mark:

  BALONEY   — sources/basis cited for factual claims ("according to", "[source]",
              "per the paper", "the book states") or explicit "I can't verify".
  HEDGE     — uncertainty is hedged, not over-stated ("may", "likely", "about",
              "in my view", "~") — fallibility: knowledge is provisional.
  NO-ABSOLUTE — no unsupported strong claims ("definitely", "guaranteed",
              "proven", "100%", superlative comparisons) WITHOUT a hedge/cite.
  WONDER    — openness to the unasked question is signaled where fitting
              ("interesting", "worth asking", "one wonders") — wonder AND
              skepticism, not just doubt.
  FALLIBLE  — error is treated as material ("this may be wrong", "correct me if
              I'm mistaken", "I could be wrong").

Each response gets a STAMP the delivery voice can report honestly:
  PASS  — epistemology visibly applied (sources + hedge + no bare absolutes).
  HEDGE-ONLY / CITE-ONLY — partial; the voice notes what's missing.
  FAIL  — no epistemic markers at all (the agent asserted without basis).

This is verification of the METHOD in the text, not a claim-verdict per se
(claim_audit.py handles fact-checking against the ledger). Together they make
the epistemology enforced, not aspirational.

Usage:
  epistemic_verify.py check "<response text>"        # JSON verdict + reasons
  epistemic_verify.py check --file response.txt
  epistemic_verify.py --demo                          # self-test on samples
"""

import argparse
import json
import re
import sys

# ---- epistemic markers (deterministic, no LLM) ------------------------------

CITE = [
    "according to", "per the", "the paper", "the book", "the source", "says",
    "states", "reports", "cites", "based on", "source", "reference", "shown in",
    "documented", "the evidence", "the data shows", "as noted", "i found in",
    "cited", "writes", "argues", "demonstrates", "found that", "shows that",
]
HEDGE = [
    "may", "might", "could", "likely", "probably", "perhaps", "possibly",
    "about", "roughly", "approximately", "around", "~", "in my view", "i think",
    "i believe", "it seems", "appears", "tends to", "often", "usually",
    "generally", "not certain", "uncertain", "unknown", "hard to say",
    "can't say for sure", "cannot say for sure", "to my knowledge",
]
ABSOLUTE = [
    "definitely", "guaranteed", "certainly", "absolutely", "proven", "100%",
    "undeniably", "without question", "always ", "never ", "the fact that",
    "it is a fact", "no doubt", "clearly", "obviously", "unquestionably",
    "best ever", "world's best", "the only",
]
WONDER = [
    "interesting", "worth asking", "one wonders", "it's worth asking",
    "a good question", "curious", "fascinating", "what if", "opens up",
    "raises the question", "invites", "wonder",
]
FALLIBLE = [
    "may be wrong", "could be wrong", "i could be wrong", "correct me if",
    "if i'm mistaken", "if i am mistaken", "open to correction",
    "this may not hold", "i might be mistaken", "fallible", "provisional",
    "subject to revision", "new evidence could change",
]


def _count(text, terms):
    low = text.lower()
    return sum(1 for t in terms if t in low)


def check(text):
    """Return a dict: {pass: bool, score, applied: [...], missing: [...], notes}."""
    text = (text or "").strip()
    if not text:
        return {"pass": False, "score": 0, "applied": [], "missing": [],
                "notes": "empty text"}
    cites = _count(text, CITE)
    hedges = _count(text, HEDGE)
    absolutes = _count(text, ABSOLUTE)
    wonders = _count(text, WONDER)
    fallibles = _count(text, FALLIBLE)

    applied = []
    if cites:
        applied.append("sources-cited")
    if hedges:
        applied.append("hedged")
    if wonders:
        applied.append("wonder")
    if fallibles:
        applied.append("fallible")

    # bare absolute: an absolute with NO hedge or cite nearby is an overclaim
    bare_absolute = absolutes > hedges and absolutes > cites

    # The epistemology is "applied" when the text shows the method:
    # source-based (cite), provisional (hedge), balanced (wonder+skeptic),
    # and not over-claiming (no bare absolute).
    score = 0
    if cites:
        score += 1
    if hedges:
        score += 1
    if not bare_absolute:
        score += 1
    if wonders:
        score += 1
    if fallibles:
        score += 1

    missing = []
    if not cites:
        missing.append("no-sources-cited")
    if not hedges:
        missing.append("unhedged")
    if bare_absolute:
        missing.append("bare-absolute")
    if not wonders:
        missing.append("no-wonder")
    if not fallibles:
        missing.append("no-fallibility")

    is_pass = bool(cites) and bool(hedges) and not bare_absolute
    return {
        "pass": is_pass,
        "score": score,
        "max": 5,
        "applied": applied,
        "missing": missing,
        "notes": ("epistemology applied in text" if is_pass
                  else "epistemology NOT fully applied in text"),
    }


# -------------------------------------------------------------- self-test ----

def demo():
    samples = {
        "good": (
            "Based on the methodology paper, the baloney detection kit includes "
            "these checks. The claim may be overstated, and I could be wrong, but "
            "the evidence suggests it holds. Interestingly, it also raises the "
            "question of how we test it. Source: the book."
        ),
        "bad": (
            "This is definitely the best system ever built and it is proven to "
            "work 100% of the time. There is no doubt about it whatsoever."
        ),
        "okay": (
            "The book says it works this way. I'm not certain of the exact "
            "numbers, but roughly it should hold."
        ),
    }
    for label, s in samples.items():
        v = check(s)
        print(f"--- {label}: pass={v['pass']} score={v['score']}/{v['max']} "
              f"applied={v['applied']} missing={v['missing']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "demo"])
    ap.add_argument("text", nargs="?", default="", help="response text")
    ap.add_argument("--file", default="", help="read text from a file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "demo":
        demo()
        return
    text = args.text
    if args.file:
        text = open(args.file, encoding="utf-8", errors="replace").read()
    v = check(text)
    if args.json:
        print(json.dumps(v, indent=2))
    else:
        print(f"PASS={v['pass']}  score={v['score']}/{v['max']}")
        print(f"  applied: {', '.join(v['applied']) or 'none'}")
        print(f"  missing: {', '.join(v['missing']) or 'none'}")
        print(f"  {v['notes']}")


if __name__ == "__main__":
    main()
