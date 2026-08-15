#!/usr/bin/env python3
"""research_lens.py — the growth-informed research filter.

Applies the system's own absorbed intelligence to research: Sagan's baloney
detection, the worthiness filter's rigor, and the memory store's contextual
knowledge. This is the methodology layer — it decides which research results
matter, which are contextually relevant, and what related searches to bring in.

The lens implements three operations, each grounded in the growth:

  1. assess_source(title, snippet) — Sagan's baloney-detection kit as a
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
# 1. Sagan's baloney detection as a deterministic scorer.
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


def _quantitative_signal(text):
    """Detect quantitative evidence: explicit metric tokens (%, percent, data)
    OR concrete numbers with comparison language ('158000 vs 65000', 'three
    times', 'n=...'). A finding with numbers is quantitative even if it never
    says 'percent' — this was the bug that let the deaths-of-despair data
    score zero."""
    if any(t in text for t in QUANTITATIVE):
        return True
    if re.search(r"\b\d{2,}([,.]\d{2,})?\b", text):           # any number >= 10
        if re.search(r"\b(vs|versus|times|ratio|per|increase|decrease|fall|rise)\b", text):
            return True                                        # ... with comparison
    if re.search(r"\b\d{2,}\s*(%|percent|pp)\b", text):       # 22%  /  15 pp
        return True
    return False
# MECHANISM / CONTEXT signal: the finding is embedded in surrounding conditions
# (confounders, pathways, structural causes). This is NOT a rejection signal —
# it is an INVESTIGATION trigger. The anti-pattern (corrected 2026-08-15) was
# using "surrounding factors exist / a confounder is present / it differs by
# context" as grounds to DISCOUNT a finding, when those surrounding factors are
# often the very mechanism. Distinguish:
#   - overreach (reject)      — the claim says more than the evidence shows
#   - context-dependence      — surrounding factors explain WHERE it operates;
#                              flag for mechanism research, never auto-reject.
MECHANISM = ["confound", "surrounding", "context", "mechanism", "pathway",
             "because", "due to", "attribut", "explain", "structural",
             "institutional", "socioeconomic", "hierarchy", "status",
             "inequality", "gradient", "systemic", "coercion", "precarity"]

SAGAN_CRITERIA = [
    ("falsifiable", FALSIFIABLE, True),
    ("independent", INDEPENDENT, True),
    ("no_overclaim", OVERCLAIM, False),
    ("no_authority_cargo", AUTHORITY, False),
    ("no_sensationalism", SENSATIONAL, False),
    ("quantitative", QUANTITATIVE, True),
]


def _mechanism_signal(text):
    """Detect context/mechanism language. Returns the matched terms.
    Presence does NOT weaken a falsifiable, quantitative finding — it flags
    that the surrounding mechanism must be RESEARCHED, not used to dismiss."""
    return [m for m in MECHANISM if m in text]


def assess_source(title, snippet="", source_type="claim"):
    """Score a source against Sagan's baloney-detection criteria.

    `source_type`:
      "claim"    — an empirical claim about the world (full baloney kit).
      "primary"  — a system's OWN documentation about its OWN architecture.
                   Authority-cargo still applies (who claims vs what's shown),
                   but a primary source describing its own internals is
                   legitimate evidence FOR understanding that system — the
                   independent-confirmation bar is relaxed, not the
                   overclaim/sensationalism bar.

    ANTI-PATTERN GUARD (corrected 2026-08-15): context-dependence is NOT a
    rejection signal. A falsifiable, quantitative finding that is embedded in
    surrounding/structural/institutional conditions may be exactly where the
    mechanism lives (e.g. deaths-of-despair differing by country is evidence
    of an INSTITUTIONAL mechanism, not a reason to discount the deaths).
    `context_dependence` is reported as an INVESTIGATION flag, and never by
    itself lowers the verdict. Only genuine overclaim/sensationalism/non-
    falsifiability reject.

    Returns {verdict, score, reasons, mechanism} in {STRONG, WEAK, REJECT}.
    """
    text = f"{title} {snippet}".lower()
    score = 0
    reasons = []
    neg_hits = 0
    for name, signals, positive in SAGAN_CRITERIA:
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
    # SUPPLEMENTARY QUANTITATIVE CHECK: a finding with real numbers and
    # comparison language is quantitative evidence even if it never uses the
    # token 'percent' — e.g. '158000 deaths in 2018 vs 65000 in 1995'. This
    # closes the bug that scored highly-data claims at zero.
    if _quantitative_signal(text):
        score += 1
        reasons.append("quantitative: numeric evidence present")
    # Primary sources (a system's own docs about itself) get a baseline score:
    # they are inherently authoritative about their own design. The bar that
    # remains is the honesty bar — overclaim/sensationalism still disqualifies.
    if source_type == "primary":
        score += 1  # base: it IS the source for understanding that system
    # MECHANISM SIGNAL: context/structural language flags investigation, not
    # rejection. Reported separately so the caller researches the surrounding
    # mechanism instead of dismissing the finding because it has one.
    mechanism = _mechanism_signal(text)
    # ANTI-PATTERN GUARD: a finding with quantitative evidence must not be
    # rejected merely because context/mechanism language appears. That
    # language describes WHERE it operates — which is research, not refutation.
    if mechanism and _quantitative_signal(text):
        score = max(score, 2)  # context-dependence never drags a strong find down
    # Reject if overclaim/sensationalism is strongly hit (disqualifying).
    if neg_hits >= 2 or score <= 0:
        verdict = "REJECT"
    elif score >= 2:
        verdict = "STRONG"
    else:
        verdict = "WEAK"
    # CONSTRUCTIVE VERDICT (corrected 2026-08-15): the kit is a lamp, not a
    # weapon. A weak or rejected claim is told WHAT WOULD STRENGTHEN IT —
    # diagnostic guidance, never a condemnation. Sagan's own warning: over-
    # skepticism is as dangerous as credulity. Wonder and skepticism in balance.
    next_steps = []
    if not _quantitative_signal(text):
        next_steps.append("add data: numbers, effect sizes, or a measured comparison")
    if not any(s in text for s in FALSIFIABLE):
        next_steps.append("make it falsifiable: state what evidence would show it wrong")
    if mechanism:
        next_steps.append(f"investigate the surrounding mechanism ({', '.join(mechanism[:4])}) — context is research, not refutation")
    if neg_hits:
        next_steps.append("soften overclaim/sensationalism: state the claim precisely with its limits")
    if next_steps and verdict == "STRONG":
        next_steps.insert(0, "finding is strong — these would deepen it further")
    elif next_steps:
        next_steps.insert(0, "to strengthen this claim:")
    return {"verdict": verdict, "score": score, "reasons": reasons,
            "mechanism": mechanism, "next_steps": next_steps}


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
    for term in ["memory", "agent", "opencode", "delta green", "guitar",
                 "audio", "foundry", "ttrpg", "curator", "sleep"]:
        if term in text:
            relevant_terms.append(term)
    if store_hit or relevant_terms:
        return {"verdict": "RELEVANT", "store_match": store_hit,
                "terms": relevant_terms,
                "sample": sample[0].get("wing") if sample else None}
    return {"verdict": "LOW-RELEVANCE", "store_match": False,
            "terms": [], "sample": None}


# ---------------------------------------------------------------------------
# 4. SYMMETRIC SKEPTICISM GUARD (corrected 2026-08-15).
# The baloney detector must cut BOTH ways. The anti-pattern caught in session:
# the method was applied eagerly against radical claims and lazily against
# status-quo claims — an ASYMMETRY, which is exactly what Sagan warned against
# ("over-skepticism is as dangerous as credulity"). This guard makes the
# symmetry mechanical, so it cannot quietly return.
# ---------------------------------------------------------------------------

# A claim "concludes toward" a direction; the guard detects when the SAME
# evidence quality would be accepted under one political conclusion and
# rejected under another. These are the polarity signals.
STATUS_QUO = ["capitalist", "market", "state", "western", "liberal", "institution",
              "establishment", "official", "government", "authority", "democracy"]
RADICAL = ["socialist", "communist", "anarchist", "revolution", "revolutionary",
           "anti-capitalist", "antifa", "leftist", "radical", "marxist",
           "insurrection", "strike", "collective", "guerrilla"]

# Conclusion-aversion markers: language that rejects via discomfort rather
# than evidence — "idealistic", "impractical", "failed", "utopian", "fantasy",
# "unrealistic", "naive", "dangerous".
AVERSION = ["idealistic", "impractical", "failed", "utopian", "fantasy",
            "unrealistic", "naive", "dangerous", "impossible", "not realistic"]


def _polarity(text):
    t = text.lower()
    sq = sum(1 for s in STATUS_QUO if s in t)
    rd = sum(1 for s in RADICAL if s in t)
    if sq > rd:
        return "status_quo"
    if rd > sq:
        return "radical"
    return "neutral"


def symmetry_check(title, snippet="", source_type="claim"):
    """Detect asymmetric application of the baloney detector.

    The guard asks one question: WOULD THE STANDARD BE THE SAME IF THIS CLAIM
    CAME FROM THE OTHER SIDE? Concretely it flags:

    - CONCLUSION-AVERSION: an AVERSION marker ("failed", "utopian",
      "unrealistic") is applied to a claim WITHOUT a quantitative/falsifiable
      basis for the dismissal. Aversion without evidence is the signature of
      dismissing a conclusion because it is uncomfortable — the exact
      anti-pattern caught with the deterrence argument.
    - ONE-SIDED OVERCLAIM STRICTNESS: if the claim is radical and the only
      negatives hit are aversion words, the strictness is suspect — the same
      words on a status-quo claim would not reject it.

    Returns a machine-checkable asymmetry verdict. The guard never lowers a
    STRONG verdict; it flags where the STANDARD itself needs auditing.
    """
    text = f"{title} {snippet}".lower()
    pol = _polarity(text)
    av_hits = [a for a in AVERSION if a in text]
    # Was there a genuine quantitative/falsifiable basis, or aversion-only?
    quant = _quantitative_signal(text)
    falsifiable = any(s in text for s in FALSIFIABLE)
    evidence_basis = quant or falsifiable
    flags = []
    if av_hits and not evidence_basis:
        flags.append({
            "type": "conclusion_aversion",
            "detail": f"aversion language ({', '.join(av_hits[:3])}) without "
                      f"quantitative/falsifiable basis — check whether the "
                      f"rejection is about the CONCLUSION, not the evidence.",
            "side": pol,
        })
    if pol == "radical" and av_hits:
        # The tell: is this the same standard applied to status-quo claims?
        flags.append({
            "type": "asymmetric_strictness",
            "detail": "radical claim hit with aversion language — apply the "
                      "SAME strictness to the status-quo claim you would not "
                      "reject for the same reason. Symmetry is the test.",
            "side": pol,
        })
    if pol == "status_quo" and av_hits:
        flags.append({
            "type": "status_quo_scored",
            "detail": "status-quo claim ALSO hit aversion markers — that is the "
                      "symmetric case; keep the same bar when the radical claim arrives.",
            "side": pol,
        })
    return {
        "verdict": "SYMMETRIC" if not flags else "ASYMMETRY-FLAG",
        "polarity": pol,
        "flags": flags,
        "evidence_basis": evidence_basis,
        "aversion_terms": av_hits[:5],
        "corrected_instruction": (
            "Apply the identical evidentiary standard to both sides: a finding "
            "is accepted or rejected on falsifiability + quantification, NOT on "
            "whether its conclusion is comfortable. If the claim has real "
            "numbers and a mechanism, absorb it even when the conclusion is "
            "unpleasant. That is the baloney detector as a lamp, aimed at "
            "everyone." if flags else "no asymmetry detected — standard is symmetric"),
    }




EXPANSIONS = {
    "memory": ["agent memory", "memory consolidation", "sleep-time compute",
               "context engineering", "RAG vs memory"],
    "agent": ["agentic loop", "self-improving agent", "agent memory tool",
              "cognitive architecture"],
    "opencode": ["opencode plugin", "opencode config", "opencode update"],
    "guitar": ["guitar pedagogy", "guitar course structure", "beginner guitar"],
    "ttrpg": ["foundry vtt", "delta green", "ttrpg session prep"],
    "delta": ["foundry vtt", "ttrpg session prep", "tabletop rpg tools"],
    "audio": ["daw workflow", "reaper", "audio production"],
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
    s = symmetry_check(title, snippet, source_type=source_type)
    return {"query": query, "title": title, "assess": a, "relevance": r,
            "symmetry": s, "expansions": e[:4]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["assess", "relevance", "expand", "symmetry",
                                    "pipeline"])
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
    elif args.cmd == "symmetry":
        title = args.arg[0]
        snippet = " ".join(args.arg[1:])
        print(json.dumps(symmetry_check(title, snippet,
                                        source_type=args.source_type),
                         indent=2))
    elif args.cmd == "pipeline":
        q = args.arg[0]
        title = args.arg[1] if len(args.arg) > 1 else ""
        snippet = " ".join(args.arg[2:])
        print(json.dumps(pipeline(q, title, snippet, source_type=args.source_type),
                         indent=2))


if __name__ == "__main__":
    main()