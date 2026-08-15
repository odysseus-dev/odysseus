#!/usr/bin/env python3
"""taxonomy.py — AUTOMATIC category + subcategory assignment.

The user must not have to name a wing or topic for a claim to be sorted.
Incoming claims (from research-verify-absorb, politics.absorb, memory writes)
are classified automatically against the known taxonomy using the SAME local
embedding model as the gate (mxbai-embed-large, distilled-core cosine). The
best-matching wing + subcategory is assigned; a claim that matches nothing is
flagged for the "general" wing rather than dropped.

Design:
- Each wing has a canonical subject (a phrase) and optional subcategory cores.
- A claim is embedded ONCE, then cosine-scored against every wing core
  (distilled to content words, same trick that closed the gate's paraphrase
  hole). Highest winning score wins; the subcategory is the best sub-core.
- Threshold: if the best score clears TAXONOMY_ENGAGE the claim is sorted;
  otherwise it lands in 'general' with a low confidence flag so it can be
  re-examined (never silently discarded).
- Deterministic: same claim -> same wing. No LLM in the decision path.

Usage:
  taxonomy.py classify "<text>" [--json]     # assign wing + subcategory
  taxonomy.py wings                           # list known wings/subcategories
  taxonomy.py add-wing <name> "<subject>"     # register a new wing
"""

import argparse
import json
import os
import sys

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

TAXONOMY_FILE = os.path.join(memory_env.memory_dir(), "index", "taxonomy.json")
TAXONOMY_ENGAGE = 0.48   # winning-cosine floor to assign a wing (not general)

# Default taxonomy: wing -> (subject phrase, [subcategory cores]).
DEFAULT_TAXONOMY = {
    "politics": (
        "political systems, economy, power, government, society, class",
        ["economic systems", "government and power", "social class and inequality",
         "rights and freedoms", "international relations"]),
    "opencode_memory": (
        "memory system, persona, harness, agent architecture, neurons",
        ["memory platform", "persona and identity", "harness and architecture",
         "warm neurons", "authority"]),
    "guitar-marketing": (
        "guitar academy, marketing, n8n, onboarding, automations",
        ["marketing campaigns", "n8n automations", "onboarding website",
         "student signup", "brand"]),
    "deltagreen": (
        "delta green, ttrpg, impossible landscapes, foundry, campaign",
        ["campaign text", "foundry vtt", "scenes and rooms", "character",
         "game mechanics"]),
    "human": (
        "nick, preferences, personal, life, routines, people",
        ["preferences", "personal life", "routines", "health", "relationships"]),
    "general": ("general knowledge, miscellaneous", []),
}

_embed_cache = {}
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("TAXONOMY_EMBED_MODEL", "mxbai-embed-large")
_EMBED_FAILED = None


def _load_taxonomy():
    try:
        with open(TAXONOMY_FILE) as f:
            return json.load(f)
    except Exception:
        return json.loads(json.dumps(DEFAULT_TAXONOMY))


def _save_taxonomy(tx):
    os.makedirs(os.path.dirname(TAXONOMY_FILE), exist_ok=True)
    tmp = TAXONOMY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tx, f, indent=2)
    os.replace(tmp, TAXONOMY_FILE)


def _lex(text):
    """Distinctive content words (light-stemmed, same as the gate)."""
    stop = {"the", "and", "for", "with", "that", "this", "from", "into",
            "when", "then", "were", "have", "been", "will", "was", "are",
            "but", "not", "you", "your", "also", "its", "his", "her", "him",
            "over", "under", "them", "they", "there", "about", "after", "what",
            "which", "who", "how", "why", "where", "while", "just", "more",
            "each", "than", "very", "such", "some", "only", "the", "a", "an",
            "of", "to", "in", "on", "at", "by", "for", "as", "or", "it", "is",
            "be", "do", "does", "i", "we", "us", "me", "my", "from", "with"}
    words = set()
    for w in (text or "").split():
        is_acronym = (len(w) >= 3 and w == w.upper()
                      and w.isalpha() and not w.islower())
        w = w.strip(".,;:!?()[]{}\"'").lower()
        if len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        if is_acronym:
            words.add(w)
        elif len(w) > 3 and w not in stop:
            words.add(w)
    return words


def _core(words):
    """Distilled semantic core: the distinctive content words joined."""
    return " ".join(sorted(words))


def _embed_vec(text):
    global _EMBED_FAILED
    if _EMBED_FAILED:
        return None
    if text in _embed_cache:
        return _embed_cache[text]
    try:
        import subprocess, tempfile
        payload = {"model": EMBED_MODEL, "input": [text]}
        fd, path = tempfile.mkstemp(prefix="taxo-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        try:
            r = subprocess.run(
                ["curl", "-s", "-m", "60", "-X", "POST",
                 f"{OLLAMA_URL}/api/embed", "-H",
                 "Content-Type: application/json", "-d", f"@{path}"],
                capture_output=True, text=True, timeout=70)
            data = json.loads(r.stdout or "{}")
            vecs = data.get("embeddings") or []
            v = vecs[0] if vecs else None
            if v:
                _embed_cache[text] = v
            return v
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception:
        _EMBED_FAILED = True
        return None


def _cosine(a, b):
    if _EMBED_FAILED:
        return None
    va, vb = _embed_vec(a), _embed_vec(b)
    if not va or not vb or len(va) != len(vb):
        return None
    dot = sum(x * y for x, y in zip(va, vb))
    na = (sum(x * x for x in va)) ** 0.5 or 1
    nb = (sum(y * y for y in vb)) ** 0.5 or 1
    return dot / (na * nb)


def classify(text):
    """Auto-assign wing + subcategory to a claim (deterministic, no LLM).

    'general' is the FALLBACK, never a scored category — otherwise it would
    win every close call by embedding close to everything.
    """
    text = (text or "").strip()
    if not text:
        return {"wing": "general", "subcategory": None, "score": 0.0,
                "confidence": "low"}
    tx = _load_taxonomy()
    best_wing, best_score = None, 0.0
    wing_scores = {}
    for wing, (subject, _subs) in tx.items():
        if wing == "general":
            continue  # fallback bucket — never a scoring target
        core = _core(_lex(subject))
        if not core:
            continue
        cos = _cosine(text, core)
        if cos is None:
            return {"wing": "general", "subcategory": None, "score": 0.0,
                    "confidence": "low", "semantic_offline": True}
        wing_scores[wing] = round(cos, 3)
        if cos > best_score:
            best_score, best_wing = cos, wing

    if best_wing is None:
        return {"wing": "general", "subcategory": None, "score": 0.0,
                "confidence": "low"}

    # Subcategory: best sub-core within the winning wing.
    subcategory, sub_score = None, 0.0
    _subs = tx.get(best_wing, ("", []))[1]
    for sub in _subs or []:
        core = _core(_lex(sub))
        if not core:
            continue
        cos = _cosine(text, core)
        if cos is not None and cos > sub_score:
            sub_score, subcategory = cos, sub

    if best_score >= TAXONOMY_ENGAGE:
        return {"wing": best_wing, "subcategory": subcategory,
                "score": round(best_score, 3),
                "sub_score": round(sub_score, 3) if subcategory else None,
                "confidence": "high" if best_score >= 0.55 else "medium",
                "scores": wing_scores}
    return {"wing": "general", "subcategory": None,
            "score": round(best_score, 3),
            "confidence": "low",
            "scores": wing_scores,
            "note": "no wing cleared the engagement floor — left general for review"}


def add_wing(name, subject):
    tx = _load_taxonomy()
    if name in tx:
        return {"status": "exists", "name": name}
    tx[name] = (subject, [])
    _save_taxonomy(tx)
    return {"status": "added", "name": name, "subject": subject}


def wings():
    tx = _load_taxonomy()
    return [{"wing": k, "subject": v[0], "subcategories": v[1]}
            for k, v in tx.items()]


def main():
    ap = argparse.ArgumentParser(description="Automatic taxonomy classification")
    ap.add_argument("cmd", choices=["classify", "wings", "add-wing"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--name", default="")
    ap.add_argument("--subject", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "classify":
        text = " ".join(args.arg)
        res = classify(text)
        if args.json:
            print(json.dumps(res, indent=2))
            return
        print(f"wing: {res['wing']}  subcategory: {res.get('subcategory') or '-'}  "
              f"score: {res.get('score', 0):.3f}  confidence: {res.get('confidence')}")

    elif args.cmd == "wings":
        ws = wings()
        if args.json:
            print(json.dumps(ws, indent=2))
            return
        for w in ws:
            subs = ", ".join(w["subcategories"]) if w["subcategories"] else "—"
            print(f"  {w['wing']:<18} {w['subject'][:50]}")
            if w["subcategories"]:
                print(f"    sub: {subs}")

    elif args.cmd == "add-wing":
        res = add_wing(args.name, args.subject)
        print(json.dumps(res) if args.json else
              f"{res['status']}: wing '{args.name}'")


if __name__ == "__main__":
    main()
