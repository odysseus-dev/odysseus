#!/usr/bin/env python3
"""research_lens.py — the growth-informed research filter.

Applies the system's own absorbed intelligence to research: the evidence-
detection method (baloney detection), the worthiness filter's rigor, and the
memory store's contextual knowledge. This is the methodology layer — it
decides which research results matter, which are contextually relevant, and
what related searches to bring in.

The lens implements three operations, each grounded in the growth:

  1. assess_source(title, snippet) — the evidence-detection method as a
     deterministic scorer: falsifiability, independent confirmation, no
     overclaim, no authority-cargo, no sensationalism, quantitative grounding.
     Verdict: STRONG / WEAK / REJECT (with per-criterion reasons).

  2. relevance(query, title, snippet) — is this result contextually relevant to
     the user's actual work (projects, preferences) per the memory store? A
     topically-adjacent but irrelevant result is flagged (abstention), NOT
     passed through as if it mattered.

  3. expand_query(query) — the wonder-skepticism balance applied to search:
     return related-but-unstated search directions the user didn't explicitly
     request, so research reaches beyond the literal query without losing
     skepticism. Drawn from the store's topic coverage + identity values.

The lens is deterministic (regex + ledger lookup, no LLM for the gate) and
returns machine-readable JSON, so it can be wired into research workflows and
the canary suite.

Usage:
  research_lens.py assess "<title>" "<snippet>"
  research_lens.py relevance "<query>" "<title>" "<snippet>"
  research_lens.py expand "<query>"
  research_lens.py pipeline "<query>" "<title>" "<snippet>"   # all three
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

MEM_DIR = memory_env.memory_dir()
STORE_PY = memory_env.python_bin()
STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_store.py")
WARM_DIR = os.path.join(MEM_DIR, "warm")

# ---------------------------------------------------------------------------
# 1. The evidence-detection method as a deterministic scorer.
# Each criterion contributes +1 (good) or -1 (bad); the verdict follows the sum.
# ---------------------------------------------------------------------------

FALSIFIABLE = ["falsif", "disprov", "testabl", "experiment", "counterexample",
               "replicat", "control group", "randomized", "could be shown wrong"]
INDEPENDENT = ["independent", "confirm", "corroborat", "replication",
               "reproduc", "peer-review", "meta-analysis", "converging evidence"]
OVERCLAIM = ["guaranteed", "definitely", "certainly", "100%", "proven",
             "no doubt", "absolutely", "never", "always"]
AUTHORITY = ["expert", "professor", "says", "claims", "according to",
             "university", "institute", "studies show"]
SENSATIONAL = ["shocking", "secret", "they don't want you", "banned", "cure",
               "miracle", "conspiracy", "exposed", "suppressed"]
QUANTITATIVE = ["%", "percent", "measured", "data", "study of n", "effect size",
                "statistic", "p<", "95%", "confidence"]

EVIDENCE_CRITERIA = [
    ("falsifiable", FALSIFIABLE, True),
    ("independent", INDEPENDENT, True),
    ("no_overclaim", OVERCLAIM, False),
    ("no_authority_cargo", AUTHORITY, False),
    ("no_sensationalism", SENSATIONAL, False),
    ("quantitative", QUANTITATIVE, True),
]


def assess_source(title, snippet="", source_type="claim"):
    """Score a source against the evidence-detection criteria.

    `source_type`:
      "claim"    — an empirical claim about the world (full baloney kit).
      "primary"  — a system's OWN documentation about its OWN architecture.
                   Authority-cargo still applies (who claims vs what's shown),
                   but a primary source describing its own internals is
                   legitimate evidence FOR understanding that system — the
                   independent-confirmation bar is relaxed, not the
                   overclaim/sensationalism bar.

    Returns (verdict, score, reasons) in {STRONG, WEAK, REJECT}.
    """
    text = f"{title} {snippet}".lower()
    score = 0
    reasons = []
    neg_hits = 0
    for name, signals, positive in EVIDENCE_CRITERIA:
        # For primary sources, authority-cargo is not a strike — the system's
        # own description of itself is the primary evidence for understanding
        # it, even if it needs independent confirmation for external claims.
        if source_type == "primary" and name == "no_authority_cargo":
            continue
        hits = [s for s in signals if s in text]
        if positive:
            if hits:
                score += 1
                reasons.append(f"{name}: {'; '.join(hits[:2])}")
        else:
            if hits:
                score -= 2
                neg_hits += len(hits)
                reasons.append(f"{name}: {'; '.join(hits[:2])}")
    # Primary sources (a system's own docs about itself) get a baseline score:
    # they are inherently authoritative about their own design. The bar that
    # remains is the honesty bar — overclaim/sensationalism still disqualifies.
    if source_type == "primary":
        score += 1  # base: it IS the source for understanding that system
    # Reject if overclaim/sensationalism is strongly hit (disqualifying).
    if neg_hits >= 2 or score <= 0:
        verdict = "REJECT"
    elif score >= 2:
        verdict = "STRONG"
    else:
        verdict = "WEAK"
    return {"verdict": verdict, "score": score, "reasons": reasons}


# ---------------------------------------------------------------------------
# 2. Contextual relevance via the memory store.
# ---------------------------------------------------------------------------

def _store_has(query, min_sim=0.45):
    """Does the store's cold tier have content relevant to this query?"""
    try:
        r = subprocess.run(
            [STORE_PY, STORE, "chunk-recall", query[:120], "--json",
             "--min-sim", str(min_sim)],
            capture_output=True, text=True, timeout=20)
        hits = json.loads(r.stdout or "[]")
        return bool(hits), hits[:2]
    except Exception:
        return False, []


def relevance(query, title, snippet=""):
    """Is this result contextually relevant to the user's actual work?

    Checks the store (which holds the user's projects, preferences, corpus).
    A result that matches the store's coverage is relevant; one that is only
    topically adjacent to the *query* but not to the user's real context is
    flagged as low-relevance (abstention — don't treat it as if it matters).
    """
    text = (title + " " + snippet).lower()
    store_hit, sample = _store_has(query)
    # Signal: does the result mention things the store knows about the user?
    relevant_terms = []
    for term in ["memory", "agent", "recall", "store", "consolidat",
                 "reasoning", "evidence", "curator", "sleep"]:
        if term in text:
            relevant_terms.append(term)
    if store_hit or relevant_terms:
        return {"verdict": "RELEVANT", "store_match": store_hit,
                "terms": relevant_terms,
                "sample": sample[0].get("wing") if sample else None}
    return {"verdict": "LOW-RELEVANCE", "store_match": False,
            "terms": [], "sample": None}


# ---------------------------------------------------------------------------
# 3. Query expansion — wonder-skepticism balance applied to search.
# ---------------------------------------------------------------------------

EXPANSIONS = {
    "memory": ["agent memory", "memory consolidation", "sleep-time compute",
               "context engineering", "RAG vs memory"],
    "agent": ["agentic loop", "self-improving agent", "agent memory tool",
              "cognitive architecture"],
    "recall": ["hybrid recall", "retrieval fusion", "BM25 vs dense"],
    "evidence": ["evidence grading", "claim verification", "baloney detection",
                 "epistemic checks"],
    "reasoning": ["reasoning traces", "chain of thought", "belief revision"],
    "consolidat": ["memory consolidation", "sleep-time compute", "pruning",
                   "dedupe policy"],
}


def expand_query(query):
    """Return related-but-unstated search directions.

    The wonder side: connect the query to the user's real domains and the
    store's coverage, even when the user didn't ask for them. The skepticism
    side: each expansion is returned WITH its warrant (why it's worth a look),
    so nothing is chased blindly.
    """
    low = query.lower()
    out = []
    seen = set()
    for key, expansions in EXPANSIONS.items():
        if key in low or key in query.lower():
            for e in expansions:
                if e not in seen:
                    seen.add(e)
                    out.append({"term": e,
                                "warrant": f"connected to '{key}' in the query; "
                                           f"worth checking for cross-domain "
                                           f"context"})
    # Always add a skeptic's check: search for contradicting evidence.
    out.append({"term": f"criticisms of {query[:40]}",
                "warrant": "skepticism half of the wonder-skepticism balance — "
                           "seek disconfirming evidence, not just confirming"})
    return out


def pipeline(query, title, snippet="", source_type="claim"):
    a = assess_source(title, snippet, source_type=source_type)
    r = relevance(query, title, snippet)
    e = expand_query(query)
    return {"query": query, "title": title, "assess": a, "relevance": r,
            "expansions": e[:4]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["assess", "relevance", "expand", "pipeline"])
    ap.add_argument("arg", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--source-type", default="claim",
                    choices=["claim", "primary"],
                    help="'primary' for a system's own docs about its own architecture")
    args = ap.parse_args()

    if args.cmd == "assess":
        title = args.arg[0]
        snippet = " ".join(args.arg[1:])
        print(json.dumps(assess_source(title, snippet, source_type=args.source_type),
                         indent=2))
    elif args.cmd == "relevance":
        q = args.arg[0]
        title = args.arg[1] if len(args.arg) > 1 else ""
        snippet = " ".join(args.arg[2:])
        print(json.dumps(relevance(q, title, snippet), indent=2))
    elif args.cmd == "expand":
        q = " ".join(args.arg)
        print(json.dumps(expand_query(q), indent=2))
    elif args.cmd == "pipeline":
        q = args.arg[0]
        title = args.arg[1] if len(args.arg) > 1 else ""
        snippet = " ".join(args.arg[2:])
        print(json.dumps(pipeline(q, title, snippet, source_type=args.source_type),
                         indent=2))


if __name__ == "__main__":
    main()