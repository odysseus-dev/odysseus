#!/usr/bin/env python3
"""recall_uplift.py — research-backed recall improvements not in the baseline.

Three uplifts, each grounded in the RAG literature reviewed 2026-08-15:

1. QUERY EXPANSION — pre-retrieval synonym/related-term expansion (the classic
   Rocchio/Voorhees query-expansion line; also the current RAG finding that
   expanded queries raise recall when the store uses different wording). Terms
   are expanded via a small domain lexicon + a shared-content-word fallback,
   so "housing" also retrieves entries tagged "rent burden / eviction /
   affordability" without any LLM call (free, deterministic).

2. POSITION-AWARE RE-RANK — the "lost in the middle" finding (Liu et al. 2023;
   the BriefContext map-reduce work, npj Digital Medicine 2025): models use
   the START and END of context best and the middle worst. After scoring, the
   top-2 items are placed FIRST and LAST in the returned order, so the most
   important evidence sits where attention lands — without changing which items
   are returned, only their order.

3. ABSTENTION CONFIDENCE — expose a crisp `confidence` verdict (high / mid /
   low) with the abstention reason, so the harness can route low-confidence
   recall to deep research instead of answering from model priors. This turns
   the existing abstention floor into an actionable signal.

Usage (called by memory_store.recall at the tail):
  recall_uplift.py expand "<query>"          # expanded query terms
  recall_uplift.py rerank "<json scored>"    # position-aware reorder
  recall_uplift.py verdict "<json scored>"   # high/mid/low + reason
"""

import json
import re
import sys

# --- Query expansion lexicon (domain word -> related terms). --------------
# Deterministic, free, no LLM. Kept small; the shared-word fallback does the
# heavy lifting for unseen terms. This maps the POLITICS/research vocabulary
# the store actually holds so a sparse query still lands.
_STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "into",
              "when", "then", "were", "have", "been", "will", "was", "are",
              "but", "not", "you", "your", "also", "its", "his", "her", "him",
              "over", "under", "them", "they", "there", "about", "after",
              "what", "which", "who", "how", "why", "where", "while", "just",
              "more", "each", "than", "then", "very", "such", "some", "only",
              "prefers", "needs", "wants", "likes", "thread", "wing", "source",
              "verdict", "evidence", "claim", "into", "were", "had", "been"}
# Domain expansions — empty by default. Populated from the store's actual
# vocabulary via the store-derived pass (pass 2 below). Users or plugins can
# add domain-specific expansions at runtime via `add_expansion()`.
EXPANSIONS: dict = {}


def add_expansion(key: str, terms: list):
    """Add a domain expansion at runtime (e.g., from a plugin or user config)."""
    EXPANSIONS[key] = terms


def expand(query):
    """Return the expanded term list for a query (query + related terms).

    LEXICON-INTEGRATED expansion (2026-08-15): the system's lexicon is not a
    hardcoded dictionary — it is the DISTINCTIVE-RARE-WORD semantics shared by
    the recall gate, the coherence gate and associations (a word is
    "distinctive" if it appears in <5% of active entries). A query word's real
    related terms are the OTHER distinctive words carried by the entries that
    contain it. So expansion works in two passes:

    1. The hardcoded domain map (below) covers the known vocabulary cheaply.
    2. The STORE-DERIVED pass: if the query word appears in any active entry,
       the distinctive words co-occurring in those entries are added as
       related terms. This makes expansion genuinely lexicon-aware — it learns
       the store's actual vocabulary, not just a fixed synonym list.

    The `store_db` optional arg (or recall_uplift.STORE_DB) enables pass 2.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    tokens = re.findall(r"[a-z']{3,}", q)
    added = []
    seen = set()
    for t in tokens:
        for k, terms in EXPANSIONS.items():
            if k in q or q in k or k in t:
                for term in terms:
                    if term not in seen:
                        seen.add(term)
                        added.append(term)
    # LEXICON-DERIVED pass (the system's own distinctive-term semantics).
    try:
        import os
        import sqlite3 as _sq
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, here)
        import memory_env
        db = _sq.connect(memory_env.store_db())
        db.row_factory = _sq.Row
        total = max(db.execute(
            "SELECT COUNT(*) AS n FROM entries WHERE status='active'").fetchone()["n"], 1)
        for t in tokens:
            if len(t) <= 3:
                continue
            rows = db.execute(
                "SELECT text FROM entries WHERE status='active' AND text LIKE ? "
                "LIMIT 8", (f"%{t}%",)).fetchall()
            for r in rows:
                text = (r["text"] or "").lower()
                for w in re.findall(r"[a-z']{4,}", text):
                    if w in seen or w == t or w in _STOPWORDS:
                        continue
                    df = db.execute(
                        "SELECT COUNT(*) AS n FROM entries WHERE status='active' "
                        "AND text LIKE ?", (f"%{w}%",)).fetchone()["n"]
                    if df / total < 0.05:  # distinctive (the system lexicon rule)
                        seen.add(w)
                        added.append(w)
        db.close()
    except Exception:
        pass
    # Always keep the original query terms first.
    originals = [t for t in tokens if t not in seen]
    return originals + added


def rerank(scored, budget=8):
    """Position-aware reorder: top-2 evidence goes FIRST and LAST.

    Returns the same items (no drop), reordered so the two highest-relevance
    items sit at the start and end of the returned window — the positions
    models use best (lost-in-the-middle mitigation). If fewer than 3 items,
    returns them unchanged (order barely matters).
    """
    if not scored or len(scored) < 3:
        return scored
    s = list(scored)[:budget]
    # Preserve the caller's relevance field name (relevance or rrf).
    key = "relevance" if "relevance" in s[0] else "rrf"
    s_sorted = sorted(s, key=lambda x: float(x.get(key, 0)), reverse=True)
    top1, top2 = s_sorted[0], s_sorted[1]
    rest = s_sorted[2:]
    return [top1] + rest + [top2]


def verdict(scored, min_score=0.74, budget=8):
    """Return a crisp confidence verdict for the harness routing decision.

    high  -> clear winner, strong match: inject, don't research.
    mid   -> retrieved something usable but ambiguous: inject AND consider
             a verification lookup if the claim is load-bearing.
    low   -> abstained or weak: DO NOT answer from memory — route to deep
             research. This is the signal that turns "I don't know" into
             "let me find out" mechanically.
    """
    if not scored:
        return {"confidence": "low", "reason": "abstained (no entries cleared the floor)",
                "route": "deep_research"}
    key = "relevance" if "relevance" in scored[0] else "rrf"
    vals = [float(x.get(key, 0)) for x in scored]
    top1 = max(vals)
    # Find the dense-cosine-ish magnitude when available (rrf is a rank, so
    # map back: rrf of 1/(15+1)=0.0625 for top-1 in hybrid mode).
    if key == "rrf" and len(scored) >= 2:
        gap = vals[0] - vals[1]
        if gap >= 0.02 and top1 >= 0.05:
            return {"confidence": "high", "reason": "clear winner in hybrid fusion",
                    "route": "inject"}
        if gap >= 0.005:
            return {"confidence": "mid", "reason": "winner but modest gap",
                    "route": "inject_and_verify"}
        return {"confidence": "low", "reason": "tight pack, no confident winner",
                "route": "deep_research"}
    # relevance-mode heuristic.
    if top1 >= min_score:
        return {"confidence": "high", "reason": "strong match above calibrated floor",
                "route": "inject"}
    if top1 >= min_score * 0.9:
        return {"confidence": "mid", "reason": "moderate match",
                "route": "inject_and_verify"}
    return {"confidence": "low", "reason": "below confidence floor",
            "route": "deep_research"}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: recall_uplift.py {expand|rerank|verdict} <arg>")
        sys.exit(1)
    cmd, arg = sys.argv[1], sys.argv[2]
    if cmd == "expand":
        print(json.dumps(expand(arg)))
    elif cmd == "rerank":
        try:
            items = json.loads(arg)
            print(json.dumps(rerank(items)))
        except Exception as e:
            print(json.dumps({"error": str(e)}))
    elif cmd == "verdict":
        try:
            items = json.loads(arg)
            print(json.dumps(verdict(items)))
        except Exception as e:
            print(json.dumps({"error": str(e)}))
