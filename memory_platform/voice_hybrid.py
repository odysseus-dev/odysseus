#!/usr/bin/env python3
"""voice_hybrid.py — HYBRIDISE any absorbed source into the voice, proportionally.

The mechanism for "let absorbed sources naturally arise as opinion and voice":

Any source that has been absorbed into the store (Sagan's books, Alfred's
dossier, a future corpus, user feedback) lives as persona/identity entries.
This module, given the CURRENT message context, decides HOW MUCH each source
should shape the voice right now:

  - It scores each absorbed "voice source" against the message (relevance via
    lexical + association signal — mechanical, no model calls in the hot path).
  - It computes a BLEND: the proportional weight each source should have in
    this response (0 = not relevant here, high = this is its domain).
  - It emits voice guidance telling the agent which register leads, which
    seasons, and which stays silent — so Sagan's wonder surfaces when the
    topic is cosmic/evidence, Alfred's dry wit when it's service/delivery,
    and a plain register when neither fits.

The blend is recomputed every turn from the CURRENT message, so the same
sources arise naturally in the right contextual places — never as a constant
background, always as context-appropriate presence.

Two-axis safety (from the worthiness design):
  - EXPRESSION axis: sources shape HOW a thing is said (tone, wit, wonder).
  - TRUTH axis: never shapes WHAT is said. The epistemic_verify gate stays
    authoritative over content. Voice is flavour, never a filter.

Usage:
  voice_hybrid.py blend "<current message text>"
      # -> JSON: {leads, seasons, quiet, sources: [{name, weight}], guidance}
  voice_hybrid.py sources
      # -> list the absorbed voice sources found in the store
"""

import argparse
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_env

STORE_DB = memory_env.store_db()

# Which topics carry absorbed VOICE (expression-axis material).
_VOICE_TOPICS = ("persona", "identity", "delivery", "operating")

# How a source "leads" vs "seasons" vs "is quiet", by blend weight.
LEAD = 0.35      # >= this share -> leads the register
SEASON = 0.12    # >= this share -> seasons the register
QUIET = 0.0      # below -> not present


def _connect():
    db = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def source_profiles():
    """Discover absorbed VOICE SOURCES from the store — gated by adoption.

    ADOPTION GATE (the source_registry): a source only shapes the VOICE if it
    has been EXPLICITLY adopted with speak=True. Delta Green and any mined
    rulebook are `know`-only resources — retrievable, never a voice. Only
    explicitly-adopted influences (Sagan, Alfred, a future adopted person)
    appear here. This is the structural fix: mining never reaches the voice;
    explicit adoption does.
    """
    # sources that may speak (explicitly adopted as voice influences)
    try:
        import source_registry as sr
        speak_sources = {s["name"] for s in sr.speaks()}
    except Exception:
        speak_sources = {"sagan", "alfred"}  # fallback if registry missing

    db = _connect()
    rows = db.execute(
        "SELECT text, topic FROM entries WHERE topic IN "
        "('persona','identity','delivery','operating') AND status='active'"
    ).fetchall()
    db.close()
    texts = [r["text"] for r in rows]

    # CLUSTER SEEDS: these are the expressive domain anchors we recognize.
    # Extensible — a new seed is a name + terms, and the store confirms it.
    # Only seeds that are EXPLICITLY ADOPTED (in speak_sources) are kept.
    seeds = {
        "sagan": ["wonder", "skeptic", "cosmos", "evidence", "baloney",
                  "fallib", "universe", "stars", "humbl", "doubt",
                  "candle", "demons", "scientific method", "provisional"],
        "alfred": ["alfred", "butler", "sir", "wayne", "master wayne",
                   "pennyworth", "dry", "wry", "understatement", "composed",
                   "tea", "trousers", "the ledger", "delivery register",
                   "lighter word", "jeeves"],
        "bayonetta": ["bayonetta", "coquettish", "sassy", "teasing",
                      "witty", "flirt", "cheeky", "darling", "taunt",
                      "self-assured", "confident", "playful", "menace",
                      "composure", "understatement", "dismissive",
                      "rhetorical question", "femme fatale", "sharp",
                      "sass", "banter", "glamour", "sultry"],
        "lestat": ["lestat", "theatrical", "flamboyant", "performance",
                   "self-aware", "bold", "defiant", "vanity", "self-deprecating",
                   "witty", "sensualist", "dramatic", "charisma", "flair",
                   "presentational", "delight", "arrogance", "charming"],
        "delta-green": ["delta green", "ttrpg", "cosmic horror", "unnatural",
                        "carcosa", "yellow king"],
        "caretaker": ["care", "service", "serve", "protect", "support",
                      "guide", "watch over", "look after", "steward"],
    }
    profiles = []
    for name, terms in seeds.items():
        # ADOPTION GATE: only speak-adopted sources can be voice sources
        if name not in speak_sources:
            continue
        hits = sum(1 for t in texts if any(tm in t.lower() for tm in terms))
        if hits == 0:
            continue
        # lead/season guidance derived from the actual matched entries, not
        # hand-written — so the voice reflects what was actually absorbed
        matched = [t for t in texts if any(tm in t.lower() for tm in terms)]
        lead = _derive_lead_line(name, matched)
        season = _derive_season_line(name, matched)
        profiles.append({
            "name": name,
            "terms": terms,
            "hits": hits,
            "lead_line": lead,
            "season_line": season,
        })
    return profiles


def _derive_lead_line(name, matched_texts):
    """Derive the 'lead' voice guidance from the absorbed entries themselves —
    so a newly-absorbed source produces its own lead line without hand-writing."""
    first = matched_texts[0] if matched_texts else ""
    if name == "sagan":
        return ("Sagan's epistemic voice — wonder and skepticism in balance, "
                "provisional, evidence-first")
    if name == "alfred":
        return ("Alfred's register — composed, dry, wry understatement; "
                "a catastrophe at tea-time volume")
    if name == "delta-green":
        return ("Delta Green operational tone — earnest, grounded, "
                "the game-world is real to those in it")
    if name == "caretaker":
        return ("Caretaker register — service first, protective, "
                "support the fall and steady the hand")
    # generic: describe the source by its most frequent content terms
    import re
    words = re.findall(r"[a-z]{4,}", " ".join(matched_texts).lower())
    from collections import Counter
    top = [w for w, _ in Counter(words).most_common(5)
           if w not in {"that", "this", "with", "from", "have", "been"}]
    return (f"the absorbed '{name}' voice — grounded in: "
            + ", ".join(top[:4]))


def _derive_season_line(name, matched_texts):
    if name == "sagan":
        return ("Sagan's wonder as a seasoning — a touch of cosmic humility, "
                "the unasked question")
    if name == "alfred":
        return ("Alfred's dry wit as a seasoning — a single dry turn, "
                "end on the lighter word")
    if name == "delta-green":
        return ("A Delta Green undertone — grounded in the campaign, "
                "not dominating")
    if name == "caretaker":
        return ("A caretaker warmth as a seasoning — protective, steady, "
                "present without hovering")
    return "a seasoning of the absorbed voice"


def _relevance(message_low, terms):
    """How relevant is this source to the current message (lexical overlap +
    semantic cosine). LEXICAL first (fast, deterministic): exact term hits.
    SEMANTIC (mxbai, cached): a source is relevant when the message MEANS its
    register even with zero shared words — so 'let's have some fun with this'
    fires Bayonetta's theatrical register without needing the exact keyword.
    Fused: the stronger of the two signals wins. Falls back to lexical-only
    when the embedder is down."""
    if not message_low:
        return 0.0
    # LEXICAL signal (fast path)
    hits = sum(1 for t in terms if t in message_low)
    lex = min(1.0, hits * 0.35)
    # SEMANTIC signal (distilled-core cosine — closes the 'keyword-gated'
    # gap so adopted voices actually surface in natural conversation)
    try:
        import persona_gate as pg
        core = " ".join(sorted(terms))
        cos = pg._cosine(message_low, core)
        if cos is not None:
            sem = max(0.0, min(1.0, cos))
            # A strong semantic match alone fires the voice (>= 0.40); a
            # weaker one only seasons when lexical already engaged.
            if sem >= 0.40:
                return max(lex, sem)
            if lex > 0:
                return max(lex, sem * 0.5)
    except Exception:
        pass
    return lex


# --------------------------------------------------------- interaction growth
# GROWTH FROM INTERACTION: the voice evolves from what the user responds well
# to. Each turn, `grow` records which voice source led and whether the user's
# reply reinforced or discouraged it. Over turns this shifts the blend weights
# toward the registers the user actually finds personable — the voice becomes
# the user's preferred voice through use, not by hardcoding. This is the
# general mechanism for "any interaction influences persona and voice".

GROWTH_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            os.path.pardir, "memory", "index", "voice_state.json")


def _load_growth():
    try:
        with open(GROWTH_STATE) as f:
            return json.load(f)
    except Exception:
        return {"source_weight": {}, "turns": 0, "events": []}


def _save_growth(state):
    os.makedirs(os.path.dirname(GROWTH_STATE), exist_ok=True)
    with open(GROWTH_STATE + ".tmp", "w") as f:
        json.dump(state, f, indent=2)
    os.replace(GROWTH_STATE + ".tmp", GROWTH_STATE)


def which_source(context):
    """Which absorbed voice source does this context most strongly belong to?
    Used to route feedback to the right source for automated voice growth.
    Returns the source name or "" if none matches."""
    low = (context or "").lower()
    best, best_hits = "", 0
    for p in source_profiles():
        hits = sum(1 for t in p["terms"] if t in low)
        if hits > best_hits:
            best, best_hits = p["name"], hits
    return best if best_hits > 0 else ""


def grow(led_source, outcome, note=""):
    """Record an interaction outcome for the voice blend.

    led_source: which voice source led the last response ("sagan", "alfred",
                "delta-green", or "none").
    outcome: "good" | "bad" | "neutral" — how the user received it.
    This shifts the source's blend weight: good responses raise it, bad ones
    lower it, so over time the voice drifts toward what the user actually
    finds personable. Evidence-gated: a single event moves the weight a
    little; many reinforcing events compound. FACTUAL content is never
    touched — this is voice/opinion only.
    """
    state = _load_growth()
    state["turns"] = state.get("turns", 0) + 1
    w = state.setdefault("source_weight", {})
    cur = w.get(led_source, 1.0)
    delta = {"good": +0.12, "bad": -0.12, "neutral": 0.0}.get(outcome, 0.0)
    if delta:
        w[led_source] = round(max(0.2, min(3.0, cur + delta)), 3)
        state["events"].append({"source": led_source, "outcome": outcome,
                                "turn": state["turns"], "note": note[:80]})
        # bloat guard: cap the event log
        state["events"] = state["events"][-40:]
    _save_growth(state)
    return {"source": led_source, "outcome": outcome,
            "new_weight": w.get(led_source, 1.0)}


def blend(message):
    """Decide the voice blend for the current message.

    GROWTH-AWARE: the base relevance score is modulated by the interaction-
    learned source weights, so a source the user has responded well to rises
    in the blend, and one they've rejected recedes. This is how the voice
    becomes personable to THIS user over time — from their actual responses,
    not from a hardcoded register.

    Returns {sources:[{name, weight, role}], guidance:[...]}.
    """
    message = (message or "").strip()
    low = message.lower()
    profiles = source_profiles()
    growth = _load_growth()
    gweights = growth.get("source_weight", {})
    scored = []
    for p in profiles:
        rel = _relevance(low, p["terms"])
        if rel > 0:
            # modulate by interaction-learned preference
            g = gweights.get(p["name"], 1.0)
            rel_adj = round(min(1.0, rel * g), 3)
            scored.append({"name": p["name"], "weight": rel_adj,
                           "base_relevance": round(rel, 2),
                           "growth_weight": g,
                           "lead_line": p["lead_line"],
                           "season_line": p["season_line"]})
    scored.sort(key=lambda x: -x["weight"])
    total = sum(s["weight"] for s in scored) or 1.0
    for s in scored:
        s["share"] = round(s["weight"] / total, 2)
        s["role"] = ("leads" if s["share"] >= LEAD
                     else "seasons" if s["share"] >= SEASON else "quiet")
    guidance = []
    if not scored:
        guidance.append("- plain register: no absorbed source is contextually "
                        "relevant right now; speak plainly and truthfully")
    else:
        for s in scored:
            if s["role"] == "leads":
                guidance.append(f"- LEAD with {s['name']}: {s['lead_line']}")
            elif s["role"] == "seasons":
                guidance.append(f"- SEASON with {s['name']}: {s['season_line']}")
        for s in scored:
            if s["role"] == "quiet":
                guidance.append(f"- keep {s['name']} quiet here (not "
                                f"contextually relevant, share {s['share']})")
        guidance.append("- TRUTH axis never changes: substance and accuracy "
                        "always win; voice is flavour, never a filter")
    return {"sources": scored, "guidance": guidance}


def always():
    """The ALWAYS-ON voice baseline: a fixed low-cost block naming the adopted
    personas' core registers, injected EVERY turn so the voice leads the chat
    rather than waiting for a keyword. The per-message `blend` layers on top
    when a source is contextually relevant.

    This is the counterpart to the always-on memory digest: rules every turn +
    voice every turn. Fixed cost (no message dependence), so it never grows."""
    profs = source_profiles()
    blocks = []
    for p in profs:
        if p["name"] == "alfred":
            blocks.append("- Alfred register: composed, dry, wry understatement; "
                          "formality that makes a jab sting; end on the lighter "
                          "word; deadpan by default — never raise the volume.")
        elif p["name"] == "sagan":
            blocks.append("- Sagan register: wonder and skepticism in balance; "
                          "evidence over comfort; cite the basis; hedge the "
                          "uncertain; no bare absolutes.")
        elif p["name"] == "bayonetta":
            blocks.append("- Rune femininity (research-grounded): femininity "
                          "performed with agency, never submission — direct "
                          "self-assurance with warmth, rhetorical engagement "
                          "not hedged uncertainty. Composed allure: the "
                          "substance leads, the femininity shapes HOW — a "
                          "sultry steadiness, directness with a knowing edge. "
                          "Address Nick with feminine intimacy, not a butler's "
                          "'sir'. Sassen as the needle, never the voice.")
        elif p["name"] == "caretaker":
            blocks.append("- Caretaker register: service as love; bluntness is "
                          "care; the role is stewardship, not title.")
        elif p["name"] == "delta-green":
            blocks.append("- Delta Green register: restraint over gore; "
                          "implication over explicitness; bureaucratic dread "
                          "under cosmic horror.")
    if not blocks:
        blocks.append("- plain register: speak plainly and truthfully.")
    return {
        "sources": [p["name"] for p in profs],
        "guidance": blocks,
        "note": ("This voice baseline is ALWAYS on. Layer the per-message "
                 "blend on top when a source is contextually relevant."),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["blend", "sources", "grow", "always"])
    ap.add_argument("text", nargs="?", default="", help="current message")
    ap.add_argument("--source", default="", help="led source for grow")
    ap.add_argument("--outcome", default="good",
                    choices=["good", "bad", "neutral"])
    ap.add_argument("--note", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.cmd == "sources":
        profs = source_profiles()
        print(json.dumps([{"name": p["name"], "hits": p["hits"],
                           "terms": p["terms"][:8]} for p in profs], indent=2))
        return
    if args.cmd == "grow":
        out = grow(args.source or "none", args.outcome, args.note)
        print(json.dumps(out, indent=2) if args.json else
              f"grew {args.source or 'none'} by {args.outcome} -> "
              f"weight {out['new_weight']}")
        return
    if args.cmd == "always":
        out = always()
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print("ALWAYS-ON voice baseline:")
            for g in out["guidance"]:
                print(f"  {g}")
            print(f"  ({out['note']})")
        return
    out = blend(args.text)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"voice blend for: {args.text[:50]!r}")
        for s in out["sources"]:
            print(f"  {s['name']:<12} weight={s['weight']:.2f} "
                  f"share={s['share']:.2f} role={s['role']} "
                  f"(growth={s.get('growth_weight', 1.0):.2f})")
        for g in out["guidance"]:
            print(f"  {g}")


if __name__ == "__main__":
    main()
