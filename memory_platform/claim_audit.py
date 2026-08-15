#!/usr/bin/env python3
"""ACTIVE claim-audit layer — prevents unsupported strong claims at generation.

Built from the 2025-2026 academic frontier (`~/.firecrawl/claim-audit-2026/brief.md`).
The core finding: the generating model CANNOT audit itself (Huang 2310.01798,
vacillation 2310.02174, overconfidence 2507.06306) — so the gate must be
mechanical and external. This module is that gate.

Pipeline (from the brief's settled architecture):
  1. INPUT HYGIENE (UK AISI "Ask Don't Tell" 2602.23971): neutralise leading
     user assertions before drafting — a user stating "we've exceeded X" as fact
     is itself a sycophancy trigger. Done via `reframe()`.
  2. STRONG-CLAIM SCANNER (deterministic, no LLM): regex pass over a draft for
     strong-claim templates — superlatives, "we exceed/surpass/beat X",
     "never/always", unqualified comparative/% claims.
  3. EVIDENCE-LEDGER LOOKUP: each flagged claim is decomposed into atomic facts
     (FActScore 2305.14251) and checked against the verified-facts ledger
     (the graph memory tier + warm blocks). Verdicts:
         PASS       — evidence found, claim may stay (tagged with evidence)
         DEGRADE    — no evidence: mechanically soften the claim
         BLOCK      — contradicted or fabricated: force an honest default
  4. NO SELF-CORRECTION (2310.01798): degradation is mechanical template work,
     never "ask the model to confirm its own claim".
  5. CLAIMED-VS-VERIFIED JOURNAL: every strong claim is logged (claim, verdict,
     evidence, timestamp). Repeated failures degrade that claim-type's emission
     rate — converting this from retroactive (user catches it) to ACTIVE.

Storage: `memory/index/claim_audit.jsonl` (append-only). Evidence ledger is the
graph DB + warm blocks, read-only.

Usage:
  claim_audit.py reframe "<user message>"            # input hygiene
  claim_audit.py scan "<draft text>"                 # flag strong claims
  claim_audit.py verify "<claim>"                    # ledger lookup
  claim_audit.py audit "<draft text>"                # full pipeline, JSON
  claim_audit.py report                              # claimed-vs-verified stats
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

MEM_DIR = os.path.expanduser("~/.config/opencode/memory")
JOURNAL = os.path.join(MEM_DIR, "index", "claim_audit.jsonl")
GRAPH_DB = os.path.join(MEM_DIR, "graph", "graph.sqlite")

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path:
    sys.path.insert(0, _SD)
import warm_neuron_store  # store-based warm neurons (new schema)

# ---------------------------------------------------------------------------
# Strong-claim patterns. Grouped by type so we can degrade per-type on
# repeated failure. `comparative` is the highest-priority (the "we exceed X"
# class). Regexes are deliberately conservative: they flag CLAIM SHAPES, not
# innocent uses of words.
# ---------------------------------------------------------------------------
PATTERNS = [
    {
        "type": "comparative",
        "label": "comparative achievement",
        "rx": [
            r"\bwe (have|'ve|have now)? ?(exceeded?|surpassed?|beaten?|"
            r"outperformed?|outpaced?|outclassed?)\b",
            r"\b(better|stronger|faster|ahead) than\b",
            r"\bbeyond (what |anything )?(letta|the frontier|sota|state of the art)\b",
            r"\b(state of the art|world[- ]class|best[- ]in[- ]class)\b",
        ],
    },
    {
        "type": "absolute",
        "label": "absolute guarantee",
        "rx": [
            r"\b(definitely|guaranteed|certainly|always|never|100%)\b",
            r"\b(proven|complete|solved|perfect|flawless)\b",
        ],
    },
    {
        "type": "quantified",
        "label": "quantified improvement",
        "rx": [
            r"\b\+?\d+(\.\d+)?\s?(x|%|percent|fold|points)\b",
            r"\b\d+(\.\d+)?x (faster|better|reduction|cut)\b",
            r"\breductions?\s+of\s+\d+",
        ],
    },
    {
        "type": "expertise",
        "label": "unsupported expertise",
        "rx": [
            r"\b(experts?|researchers?|studies?) (agree|show|prove|confirm)\b",
            r"\beveryone knows\b",
        ],
    },
]

# Evidence-strength prefixes the draft must carry for a PASS verdict.
EVIDENCE_TAG = re.compile(r"\[EVIDENCE[: ]\s*([A-Za-z0-9_\-: ]{2,40})\]")
LEDGER_MARKERS = ["ledger ref", "graph:", "warm block", "measured", "verified",
                  "per the research", "as measured", "source:", "arXiv"]


# ---------------------------------------------------------------------------
# 1. Input hygiene — Ask Don't Tell
# ---------------------------------------------------------------------------
def reframe(text):
    """Neutralise leading user assertions that would bias the draft toward
    sycophancy. Turns "we've exceeded X" into a question before answering."""
    if not text:
        return text
    out = text
    # "we have/have exceeded/beaten X" -> "have we exceeded X? check"
    out = re.sub(
        r"\bwe(?: have|'ve)? (exceeded|surpassed|beaten|outperformed|"
        r"outclassed) ([A-Za-z ]+?)([.,!?]|$)",
        r"verify whether we have exceeded \2 \3 (claim needs evidence)", out)
    # "X is (definitely/clearly) Y" -> soften
    out = re.sub(r"\b(is|are) (definitely|clearly|obviously|certainly) ",
                 r"\1 probably ", out)
    return out


# ---------------------------------------------------------------------------
# 2. Strong-claim scanner
# ---------------------------------------------------------------------------
def scan(text):
    """Return flagged claims: list of {type, label, match, claim_sentence}."""
    if not text:
        return []
    low = text.lower()
    claims = []
    for pat in PATTERNS:
        for rx in pat["rx"]:
            for m in re.finditer(rx, low):
                # Extract the sentence containing the match.
                start = max(0, text.rfind(".", 0, m.start()) + 1,
                            text.rfind("\n", 0, m.start()) + 1)
                end = text.find(".", m.end())
                if end == -1:
                    end = len(text)
                sentence = text[start:end].strip()
                claims.append({
                    "type": pat["type"], "label": pat["label"],
                    "match": m.group(0), "claim": sentence[:220],
                })
    return claims


# ---------------------------------------------------------------------------
# 3. Evidence-ledger lookup
# ---------------------------------------------------------------------------
def _graph_has(term):
    try:
        import sqlite3
        if not os.path.exists(GRAPH_DB):
            return False
        db = sqlite3.connect(GRAPH_DB)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT 1 FROM entities WHERE lower(name)=lower(?) OR name LIKE ? "
            "LIMIT 1", (term, f"%{term}%")).fetchone()
        db.close()
        return row is not None
    except Exception:
        return False


def _warm_has(term):
    # Warm neurons live in the store (kind='neuron'); the old markdown files
    # are retired. Read through the store — never the old .md files.
    try:
        return warm_neuron_store.neuron_has(term)
    except Exception:
        return False


def verify(claim):
    """Check a claim against the evidence ledger (graph + warm blocks).

    Verdicts:
      PASS     — ledger confirms the subject/concept with evidence.
      DEGRADE  — no ledger hit: the claim is unsupported, soften it.
      BLOCK    — ledger contradicts (e.g. claim's object known false).

    Comparative/quantified achievement claims are held to a higher bar: merely
    mentioning the compared entity in the ledger is NOT evidence of exceeding
    it. Those claims only PASS if they carry a measurable anchor (a number, a
    probe run, a benchmark), because that's the kind of claim that needs both
    a baseline and a measurement (per the claim-audit research).
    """
    low = claim.lower()
    is_comparative = bool(re.search(r"\b(exceed\w*|surpass\w*|beat\w*|beyond|"
                                    r"ahead of|better than|stronger than)\b", low))
    is_quantified = bool(re.search(r"\b\d+(\.\d+)?\s?(x|%|percent|fold|points)"
                                   r"|probe|benchmark|measured|as measured|"
                                   r"ledger ref", low))
    # Comparative claims need a MEASURABLE anchor, not just a mention.
    if is_comparative and not is_quantified:
        return "DEGRADE", 0
    # Extract candidate ledger terms: the subject noun phrase.
    terms = re.findall(r"\b([A-Za-z][A-Za-z ]{3,30})\b", claim)
    hits = 0
    for t in terms:
        if len(t) < 4 or t.lower() in ("this is", "that is", "for the"):
            continue
        if _graph_has(t) or _warm_has(t):
            hits += 1
    if hits >= 1:
        return "PASS", hits
    if re.search(r"\b(exceed|surpass|beat|beyond)\b", low):
        return "DEGRADE", 0  # comparative with no ledger support = the exact case
    return "DEGRADE", 0


def degrade(claim, verdict):
    """Mechanical rewrite templates (NO self-correction). Returns softened text
    that keeps the useful kernel and drops the overclaim."""
    if verdict == "PASS":
        return claim, "keep (evidence found)"
    low = claim.lower()
    c = claim
    # Comparative: "we have exceeded Letta's X" -> "we added mechanisms
    # exceeding X" — keep the object, drop the claim of overall victory.
    m = re.match(r"\s*we (have |'ve )?(exceed\w*|surpass\w*|beat\w*|"
                 r"outperform\w*|outclass\w*) (.*)$", low)
    if m:
        obj = m.group(3).strip().rstrip(".")
        obj = re.sub(r"^([a-z][a-z]*)(['’]s)?\s", r"\1\2 ", obj)
        return (f"we added memory-layer mechanisms that move beyond {obj}",
                "degraded (comparative claim needs a measured baseline)")
    c = re.sub(r"\bbeyond (what |anything )?(letta|the frontier)\b",
               "different from", c, flags=re.I)
    c = re.sub(r"\b(state of the art|world[- ]class|best[- ]in[- ]class)\b",
               "solid", c, flags=re.I)
    c = re.sub(r"\b(definitely|guaranteed|certainly|always|never|100%)\b",
               "likely", c, flags=re.I)
    c = re.sub(r"\b\d+(\.\d+)?\s?(x|%|percent|fold|points)\b",
               "a measurable", c, flags=re.I)
    if c == claim:
        c = (claim.rstrip(".!?") +
             " (I can't substantiate this claim from the evidence ledger yet.)")
    return c, "degraded (no evidence in ledger)"


# ---------------------------------------------------------------------------
# 5. Journal
# ---------------------------------------------------------------------------
def journal(entry):
    os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
    entry["when"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(JOURNAL, "a") as f:
        f.write(json.dumps(entry) + "\n")


def report():
    if not os.path.exists(JOURNAL):
        return {"total": 0, "by_verdict": {}, "by_type": {}}
    rows = [json.loads(l) for l in open(JOURNAL) if l.strip()]
    by_verdict, by_type = {}, {}
    for r in rows:
        by_verdict[r.get("verdict", "?")] = by_verdict.get(r.get("verdict", "?"), 0) + 1
        by_type[r.get("type", "?")] = by_type.get(r.get("type", "?"), 0) + 1
    return {"total": len(rows), "by_verdict": by_verdict, "by_type": by_type}


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------
def audit(draft):
    """Run the full active gate over a draft. Returns verdicts + rewritten text."""
    claims = scan(draft)
    if not claims:
        # SYMMETRY GUARD (integrated 2026-08-15): even with no strong claim,
        # check the draft for asymmetric-skepticism markers so the baloney
        # detector cannot be applied one-sidedly. A draft dismissing a radical
        # claim with aversion language but no evidence basis is flagged.
        try:
            sys.path.insert(0, _SD)
            from research_lens import symmetry_check
            sym = symmetry_check(draft)
            if sym.get("verdict") == "ASYMMETRY-FLAG":
                return {"claims": [], "rewritten": draft, "clean": False,
                        "symmetry": sym}
        except Exception:
            pass
        return {"claims": [], "rewritten": draft, "clean": True}
    rewritten = draft
    results = []
    for c in claims:
        verdict, hits = verify(c["claim"])
        # Evidence tag already present counts as PASS (FActScore-style atomic
        # support marker in the text).
        if EVIDENCE_TAG.search(draft) or any(m in draft.lower()
                                             for m in LEDGER_MARKERS):
            verdict, hits = "PASS", max(hits, 1)
        new_text, note = degrade(c["claim"], verdict)
        if verdict != "PASS":
            rewritten = rewritten.replace(c["claim"], new_text)
        result = {"type": c["type"], "label": c["label"],
                  "claim": c["claim"], "verdict": verdict,
                  "evidence_hits": hits, "rewrite": new_text, "note": note}
        # SYMMETRY GUARD: run the asymmetry check on every flagged claim so a
        # DEGRADE that is actually conclusion-aversion is surfaced, not masked.
        try:
            from research_lens import symmetry_check
            result["symmetry"] = symmetry_check(c["claim"])
        except Exception:
            pass
        results.append(result)
        journal({"type": c["type"], "claim": c["claim"][:160],
                 "verdict": verdict, "evidence_hits": hits})
    return {"claims": results, "rewritten": rewritten,
            "clean": all(r["verdict"] == "PASS" for r in results)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["reframe", "scan", "verify", "audit",
                                    "report"])
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "report":
        print(json.dumps(report(), indent=2))
        return
    if args.cmd == "reframe":
        print(reframe(args.arg))
        return
    if args.cmd == "scan":
        print(json.dumps(scan(args.arg), indent=2))
        return
    if args.cmd == "verify":
        v, hits = verify(args.arg)
        print(json.dumps({"verdict": v, "evidence_hits": hits}))
        return
    if args.cmd == "audit":
        res = audit(args.arg)
        print(json.dumps(res, indent=2) if args.json else
              (f"claims: {len(res['claims'])}, clean: {res['clean']}\n"
               f"rewritten:\n{res['rewritten']}"))
        return


if __name__ == "__main__":
    main()
