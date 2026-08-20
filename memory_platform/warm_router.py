#!/usr/bin/env python3
"""warm_router.py — OPTIMIZED warm neurons (new schema).

Warm neurons are atomic entries in the STORE (kind='neuron'). In the new schema
size at rest is FREE — a neuron only costs tokens when it fires. The ONLY
optimization that matters is total token cost at firing time, enforced by a
TOKEN BUDGET at injection (approx chars/4), not by any per-neuron size limit
and never by splitting stored content.

- FIRING: deterministic keyword scan (sub-ms, zero model cost). A neuron fires
  when its triggers match the current message.
- GRAPH EXPANSION: a fired neuron ALSO pulls its strongly-associated neurons
  (precomputed association graph, 1 hop) — the lukewarm tier stays connected
  and MORE ACTIVE: related context surfaces without exact keyword matches.
- BUDGET: fire every relevant neuron in score order; stop when the budget is
  exhausted. The single most-relevant neuron always fires (relevance outranks
  budget). No per-turn block-count cap.
- AUTO-BUILD: facts promoted to the warm tier are written as neurons by
  write_neuron (upsert by slug). No manual sizing, no god-object fear.
- GRAPH ENRICHMENT: fires graph-memory facts alongside keyword neurons
  (HippoRAG-style).

Usage:
  warm_router.py route  "<incoming text>" [--max-tokens N]
  warm_router.py list
  warm_router.py report
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
try:
    from . import memory_env
except ImportError:
    import memory_env

MEM_DIR = memory_env.memory_dir()
STATE = os.path.join(MEM_DIR, "index", "warm_state.json")
STORE_DB = memory_env.store_db()

# New-schema optimization: firing is capped by a TOKEN BUDGET (approx chars/4).
# Neurons are atomic store entries; size at rest is free.
DEFAULT_MAX_TOKENS = 300

# Identity topics are always-relevant — they define who the agent is and how it
# works. They are never demoted or dropped by the warm tier.
PROTECTED_HOT = ("constitution", "persona", "operating")

# CURATED CHARACTER NEURONS: deliberately-absorbed personas (an example persona
# with a distinctive register, the evidence-method core, etc.). When their
# Protected neurons — empty by default. Users or plugins can add protected
# neuron slugs at runtime via `add_protected_neuron()`.
PROTECTED_NEURONS: set = set()
PROTECTED_BOOST = 4.0  # added to their score so they lead on their topic


def add_protected_neuron(slug: str):
    """Add a protected neuron slug at runtime."""
    PROTECTED_NEURONS.add(slug)


def _db():
    import sqlite3
    return sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)


def _aaak(text):
    """AAAK-compress a neuron body (compression route — light at rest)."""
    try:
        import aaak
        return aaak.compress(text, topic="warm")
    except Exception:
        return ""


# ---------------------------------------------------------------- neurons ----

def _parse_trigger(s):
    return [t.strip().lower() for t in s.split(",") if t.strip()]


def list_neurons():
    """All active warm neurons from the STORE (kind='neuron') with slug +
    triggers + importance + an estimated token cost.

    PARITY FIRST, COMPRESSION SECOND: each neuron carries BOTH its full
    verbatim body and its AAAK summary. Firing keeps fidelity where it matters
    (the top-scoring neuron fires its FULL body — substance is not sacrificed
    for tokens) and uses the compressed summary only for the lower-relevance /
    association-expanded additions (the bloat guard)."""
    neurons = []
    try:
        db = _db()
        rows = db.execute(
            "SELECT text, slug, triggers, importance, summary FROM entries "
            "WHERE kind='neuron' AND status='active' ORDER BY slug"
        ).fetchall()
        db.close()
    except Exception:
        return neurons
    for text, slug, triggers, importance, summary in rows:
        full = (text or "").strip()
        summ = (summary or "").strip()
        neurons.append({
            "slug": slug or "warm",
            "triggers": _parse_trigger(triggers or ""),
            "importance": float(importance or 0.5),
            "body": full,
            "summary": summ,
            "tokens": max(1, len(full) // 4),
        })
    return neurons


def write_neuron(slug, triggers, body, importance=0.5):
    """Upsert a warm neuron by slug in the STORE. This is the AUTO-BUILD path:
    facts promoted to the warm tier become optimized neurons automatically."""
    try:
        import sqlite3
        from datetime import datetime, timezone
        db = sqlite3.connect(STORE_DB)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        existing = db.execute("SELECT id FROM entries WHERE kind='neuron' "
                              "AND slug=?", (slug,)).fetchone()
        if existing:
            db.execute("UPDATE entries SET text=?, triggers=?, importance=?, "
                       "last_accessed=?, summary=? WHERE id=?",
                       ((body or "").strip(), ", ".join(triggers),
                        float(importance), ts, _aaak(body), existing[0]))
            eid = existing[0]
        else:
            cur = db.execute(
                "INSERT INTO entries (text, importance, created_at, "
                "last_accessed, topic, source, method, status, valid_from, "
                "confidence, temperature, always_on, priority, triggers, "
                "kind, slug, summary) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ((body or "").strip(), float(importance), ts, ts, "warm",
                 "warm_router", "warm_router", "active", ts, 0.9, 1.0, 0, 5,
                 ", ".join(triggers), "neuron", slug, _aaak(body)))
            eid = cur.lastrowid
        db.commit()
        db.close()
        # auto-associate the neuron with related entries (bounded, precomputed)
        try:
            import memory_store as ms
            dbw = sqlite3.connect(STORE_DB)
            ms._auto_associate(dbw, eid, body or "", topic="warm",
                               source="warm_router")
            dbw.close()
        except Exception:
            pass
    except Exception:
        pass
    return f"store://neuron/{slug}"


# ---------------------------------------------------------------- routing ----

def score_neuron(neuron, low_text):
    """Score how strongly an incoming text fires this neuron's synapses."""
    hits = 0
    for t in neuron["triggers"]:
        if t and t in low_text:
            hits += 1
    if hits == 0:
        return 0.0
    length_bonus = sum(1 for t in neuron["triggers"]
                       if t and len(t) > 6 and t in low_text) * 0.25
    score = hits + neuron["importance"] + length_bonus
    # Curated character neurons lead on their topic (they are the persona —
    # the auto-migrated persona-* entries must not crowd them out).
    if neuron["slug"] in PROTECTED_NEURONS:
        score += PROTECTED_BOOST
    return score


def route(text, max_tokens=DEFAULT_MAX_TOKENS, max_blocks=None,
          session_id=""):
    """Return the neurons that fire for `text`, constrained by a TOKEN budget.

    ACTIVE TIER: when `session_id` is given, the router is stateful. Neurons
    that fired recently stay PRIMED (decayed per turn) and their association-
    neighbours join the candidate set — so a topic drift continues to surface
    related context without an exact keyword. This is proactive continuity,
    not just keyword reaction.

    PARITY: a single highly-relevant neuron always fires its FULL body — the
    thing the user asked about is never compressed (relevance outranks budget).
    COMPRESSION: lower-relevance neurons and association-expanded additions
    fire their AAAK summary instead of the full body, so the budget stretches
    further without sacrificing the substance of the primary hit.
    `max_blocks` is accepted for caller compatibility and ignored.

    Returns (fired, total_neurons, all_scores).
    """
    low = (text or "").lower()
    neurons = list_neurons()
    by_slug = {n["slug"]: n for n in neurons}
    scored = [(score_neuron(n, low), n) for n in neurons]

    # ACTIVE PRIMING: previous-turn neurons and their association-neighbours
    # join the candidate set (decayed), so continuity survives keyword drift.
    primed = _decay_primes(session_id) if session_id else []
    primed_assoc = _primed_associations(session_id) if session_id else []
    primed_slugs = set(primed) | {slug for slug, _ in primed_assoc}
    fired_by_turn = []
    already = {n2["slug"] for s2, n2 in scored if s2 > 0}
    for slug in primed_slugs:
        n = by_slug.get(slug)
        if not n or slug in already:
            continue
        # primed score: recency weight from the priming record
        base = 0.4
        for ps, s2 in primed_assoc:
            if s2 and ps == slug:
                base = max(base, s2)
        scored.append((base, n))
        fired_by_turn.append(slug)
    scored.sort(key=lambda x: -x[0])

    fired = []
    used_tokens = 0
    top_fired = False
    for score, n in scored:
        if score <= 0:
            continue
        # The top-scoring relevant neuron fires its FULL body (parity — the
        # thing the user asked about is never compressed). Everything after
        # uses the AAAK summary unless the budget affords the full body.
        if not top_fired:
            body = n["body"]
            top_fired = True
        else:
            body = n["summary"] or n["body"]
        tokens = max(1, len(body) // 4)
        if fired and used_tokens + tokens > max_tokens:
            continue
        fired.append({"slug": n["slug"], "body": body,
                      "score": round(score, 2), "tokens": tokens,
                      "compressed": body != n["body"],
                      "primed": n["slug"] in primed_slugs})
        used_tokens += tokens
        _touch(n)
    # ASSOCIATION EXPANSION: the fired neuron's precomputed associations also
    # fire (1 hop, strong links only). This makes the lukewarm tier MORE ACTIVE
    # and connected without any extra model cost — a pure indexed SQL walk. The
    # bodies are AAAK-compressed summaries where available, so the expansion
    # stays within the token budget (compression route).
    if fired:
        try:
            associated = _associated_neuron_bodies(fired, max_tokens - used_tokens)
            for a in associated:
                fired.append({"slug": a["slug"], "body": a["body"],
                              "score": a["score"], "tokens": a["tokens"],
                              "via_association": True})
                used_tokens += a["tokens"]
        except Exception:
            pass
    # Record what fired this turn for the next turn's priming.
    if session_id and fired:
        try:
            _prime_neurons(session_id,
                           [f["slug"] for f in fired],
                           max(score for score, _ in scored if score > 0))
        except Exception:
            pass
    return fired, len(neurons), [{"slug": n["slug"], "score": round(s, 2)}
                                 for s, n in scored if s > 0]


def _associated_neuron_bodies(fired, budget_tokens):
    """Pull strongly-associated entries for the fired neurons (1 hop).

    Precomputed association graph = a single indexed SQL join, no model calls.
    Only links with strength >= 0.3 are followed (weak links would re-add
    noise). Bodies prefer the AAAK-compressed summary (compression route) so
    more related context fits the remaining token budget.
    """
    out = []
    seen = set(f["slug"] for f in fired)
    used = 0
    try:
        import sqlite3
        db = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
        # resolve fired slugs -> entry ids
        slugs = [f["slug"] for f in fired]
        ph = ",".join("?" * len(slugs))
        rows = db.execute(
            f"SELECT id, slug FROM entries WHERE kind='neuron' AND slug IN ({ph})",
            slugs).fetchall()
        ids = [r[0] for r in rows]
        if ids:
            iph = ",".join("?" * len(ids))
            links = db.execute(
                "SELECT a.src_id AS src, a.dst_id AS dst, a.strength, "
                "e.slug, e.summary, e.text "
                "FROM associations a JOIN entries e ON e.id=a.dst_id "
                f"WHERE a.src_id IN ({iph}) AND a.strength >= 0.3 "
                "AND e.status='active' "
                "ORDER BY a.strength DESC LIMIT 6", ids).fetchall()
            for src, dst, strength, slug, summary, text in links:
                if slug in seen:
                    continue
                seen.add(slug)
                body = (summary or text or "").strip()
                if not body:
                    continue
                tokens = max(1, len(body) // 4)
                if used + tokens > budget_tokens:
                    break
                out.append({"slug": slug or "assoc", "body": body,
                            "score": round(strength, 2), "tokens": tokens})
                used += tokens
        db.close()
    except Exception:
        pass
    return out


def graph_context(term, hops=1, limit=4):
    """Pull graph-memory facts for a term (concept-mediated KG). Returns a
    compact text block or '' if the graph has nothing. This makes the warm
    tier fire on graph EDGES too, not just keyword neurons (HippoRAG-style:
    find the entry node, traverse from it)."""
    try:
        import subprocess
        r = subprocess.run(
            ["python3", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "graph_memory.py"),
             "query", term, "--hops", str(hops), "--json"],
            capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout)
    except Exception:
        return ""
    facts = data.get("facts") or []
    if not facts:
        return ""
    lines = []
    for f in facts[:limit]:
        lines.append(f"{f['subject']} → {f['predicate']} → {f['object']}")
    return (f"### Graph memory (connected to '{data.get('entity', term)}')\n\n"
            + "\n".join(lines))


def _touch(neuron):
    """Record that a neuron fired (usage feedback for promotion/demotion)."""
    state = load_state()
    entry = state.setdefault("neurons", {}).setdefault(neuron["slug"], {
        "fires": 0, "last_fired": "", "promotion_streak": 0, "demotion_streak": 0})
    entry["fires"] += 1
    entry["last_fired"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["neurons"][neuron["slug"]] = entry
    save_state(state)


# ------------------------------------------------------------------ priming ----
# ACTIVE (not passive) warm tier: the router keeps a per-session record of
# recently-fired neurons so related context stays engaged across turns. A
# neuron that fired recently is PRIMED: it (and its association-neighbours)
# stay in the candidate set on later turns even when the exact keyword is
# absent — topic continuity without a model call in the firing path.

PRIME_DECAY = 0.6    # each turn a primed neuron's priming score decays
PRIME_FLOOR = 0.15   # below this a neuron is no longer primed
ASSOC_PRIME_WEIGHT = 0.5  # association-neighbours prime at half the direct score


def _session_state():
    st = load_state()
    st.setdefault("sessions", {})
    return st


def _prime_neurons(session_id, slugs, score):
    """Record that `slugs` fired this turn (they become primed for later)."""
    st = _session_state()
    sess = st["sessions"].setdefault(session_id, {"primed": {}, "turns": 0})
    sess["turns"] = sess.get("turns", 0) + 1
    sess["last_active"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for slug in slugs:
        sess["primed"][slug] = max(score, sess["primed"].get(slug, 0.0))
    st["sessions"][session_id] = sess
    # BLOAT GUARD: prune sessions that are no longer active. A session with
    # nothing primed, or idle for > 24h, leaves the state file. Hard cap 40.
    import datetime as _dt
    now = datetime.now(timezone.utc)
    stale = []
    for s, v in st["sessions"].items():
        if not v.get("primed"):
            stale.append(s)
            continue
        try:
            last = _dt.datetime.fromisoformat(v.get("last_active", ""))
            if (now - last).total_seconds() > 86400:
                stale.append(s)
        except Exception:
            pass
    for s in stale:
        del st["sessions"][s]
    if len(st["sessions"]) > 40:
        oldest = sorted(st["sessions"],
                        key=lambda s: st["sessions"][s].get("turns", 0))
        for s in oldest[:len(st["sessions"]) - 40]:
            del st["sessions"][s]
    save_state(st)


def _decay_primes(session_id):
    """Decay prior priming before each new turn (recency-weighted)."""
    st = _session_state()
    sess = st["sessions"].get(session_id)
    if not sess:
        return []
    primed = sess.get("primed", {})
    for slug in list(primed):
        primed[slug] = primed[slug] * PRIME_DECAY
        if primed[slug] < PRIME_FLOOR:
            del primed[slug]
    st["sessions"][session_id] = sess
    save_state(st)
    return sorted(primed, key=lambda s: -primed[s])


def _primed_associations(session_id, limit=4):
    """Association-neighbours of the session's primed neurons. This is what
    makes the tier ACTIVE: a topic drift still surfaces related neurons
    without an exact keyword, because the graph was already walked when the
    topic was first engaged."""
    st = _session_state()
    sess = st["sessions"].get(session_id) or {}
    primed = sess.get("primed", {})
    if not primed:
        return []
    slugs = list(primed.keys())
    try:
        import sqlite3
        db = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
        ph = ",".join("?" * len(slugs))
        rows = db.execute(
            f"SELECT id FROM entries WHERE kind='neuron' AND slug IN ({ph})",
            slugs).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            db.close()
            return []
        iph = ",".join("?" * len(ids))
        links = db.execute(
            "SELECT e.slug, a.strength FROM associations a "
            "JOIN entries e ON e.id=a.dst_id "
            f"WHERE a.src_id IN ({iph}) AND a.strength >= 0.3 "
            "AND e.kind='neuron' AND e.status='active' "
            "ORDER BY a.strength DESC LIMIT ?", (limit,)).fetchall()
        db.close()
        return [(r[0], float(r[1]) * ASSOC_PRIME_WEIGHT) for r in links
                if r[0] not in slugs]
    except Exception:
        return []


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"neurons": {}, "divides": []}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE + ".tmp", "w") as f:
        json.dump(state, f, indent=2)
    os.replace(STATE + ".tmp", STATE)


def _derive_triggers(text, existing=()):
    """Pick the best trigger terms for a neuron body: frequent significant
    words + any existing triggers that appear in it."""
    stop = {"the", "and", "for", "with", "that", "this", "from", "into",
            "when", "then", "were", "have", "been", "will", "was", "are",
            "but", "not", "you", "your", "also", "its", "his", "her",
            "over", "under", "them", "they", "there", "about", "after"}
    words = re.findall(r"[a-z]{4,}", text.lower())
    freq = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    top = [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:6]]
    existing_hits = [t for t in existing if t and t in text.lower()]
    return (existing_hits + top)[:8]


# ----------------------------------------------------- resident-set migration -
# The always-on core is now a small RESIDENT SET (voice + inviolables only,
# per LightMem's short-term store + "Lost in the Middle"). The non-resident
# topics (persona, operating, project, human) must fire ACTIVELY via the warm
# tier instead of being pre-loaded. This migrates those entries into neurons.

NON_RESIDENT = ("persona", "operating", "project", "human")


def promote_active(dry_run=False):
    """Convert non-resident always-on entries into firing warm neurons.

    The resident set stays small (voice + inviolables + short universal
    operating rules). Longer/contextual operating rules, persona voice
    material, project state, and human facts become active neurons that fire
    on relevance — the load spreads to where it's needed instead of flooding
    every turn. Returns count promoted."""
    try:
        import sqlite3
        db = sqlite3.connect(STORE_DB)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id, text, topic FROM entries WHERE always_on=1 "
            "AND status='active' AND topic IN ('persona','operating',"
            "'project','human')").fetchall()
        # short universal operating rules stay resident (they define core
        # behaviour every turn); everything else becomes an active neuron
        KEEP_RESIDENT_TOPIC = {"operating"}
        KEEP_MAX = 90  # chars — shorter operating rules are universal behaviour
        promoted = 0
        for r in rows:
            if r["topic"] in KEEP_RESIDENT_TOPIC and len(r["text"]) <= KEEP_MAX:
                continue  # stay resident
            slug = f"{r['topic']}-{r['id']}"
            trigs = _derive_triggers(r["text"])
            if dry_run:
                promoted += 1
                continue
            try:
                db.execute(
                    "UPDATE entries SET kind='neuron', slug=?, triggers=?, "
                    "always_on=0 WHERE id=?",
                    (slug, ", ".join(trigs), r["id"]))
                promoted += 1
            except Exception:
                pass
        db.commit()
        db.close()
        return promoted
    except Exception:
        return 0


# ------------------------------------------------------------------ output ---

def render_fired(fired):
    parts = []
    for f in fired:
        tag = ""
        if f.get("via_association"):
            tag = " (via association)"
        elif f.get("compressed"):
            tag = " (compressed summary — full body in store)"
        parts.append(f"### Warm neuron: {f['slug']}{tag}\n\n{f['body']}")
    return "\n\n".join(parts)


# ----------------------------------------------------- always-on digest ------
# The warm tier has TWO layers:
#   1. ALWAYS-ON digest — a tiny fixed-cost set of the most important neurons
#      injected EVERY turn regardless of keyword match. This is the "always
#      influencing, very low cost" layer the design calls for. It never grows:
#      a hard token cap keeps per-turn spend flat.
#   2. Keyword-fired deep layer — the existing route() behaviour (relevant,
#      primed, association-expanded neurons) on top of the digest.
ALWAYS_DIGEST_TOKENS = 220   # fixed low per-turn cost of the always-on layer

# Lexical stopwords for the digest's information-density gate (mirrors the
# store's lexical logic). A neuron whose body is mostly stopwords carries no
# signal and is not worth always-on tokens.
_DIGEST_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when",
    "then", "were", "have", "been", "will", "was", "are", "but", "not",
    "you", "your", "also", "its", "his", "her", "him", "over", "under",
    "them", "they", "there", "about", "after", "what", "which", "who",
    "how", "why", "where", "while", "just", "more", "each", "than", "then",
    "very", "such", "some", "only", "the", "a", "an", "of", "to", "in",
    "on", "at", "by", "for", "as", "or", "it", "is", "be", "do", "does",
}


def _lexical_signal(body):
    """Distinctive content words in a body (lowercase, >3 chars, not stop).
    Higher = more information-dense = worth always-on tokens."""
    words = set()
    for w in (body or "").lower().split():
        w = w.strip(".,;:!?()[]{}\"'")
        if len(w) > 3 and w not in _DIGEST_STOP:
            words.add(w)
    return words


def _lexical_overlap(a, b):
    """Jaccard similarity of two neuron bodies' distinctive words (0-1).
    Used to DEDUPE: if a candidate repeats an already-selected neuron's idea,
    skip it — the always-on layer must not burn tokens on redundancy."""
    wa, wb = _lexical_signal(a), _lexical_signal(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


_DEDUP_THRESHOLD = 0.45  # Jaccard above this = near-duplicate, skip

# Universality heuristic for the always-on digest. A neuron belongs in the
# every-turn layer only if it reads as a UNIVERSAL RULE, not a dated record.
# Dated specifics (incident reports, one-off fixes, session notes, timestamps)
# are contextual — they belong in the keyword-fired layer, not always-on.
_DATE_RE = re.compile(r"\b(19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/](19|20)\d{2}\b")
# Source markers that identify a dated/incidental record rather than a rule.
_DATED_MARKERS = ("session record", "research + session", "session note",
                  "2026-", "2025-", "fixed", "fix:", "issue:", "bug",
                  "incident", "workaround", "cause")
# Directive verbs/shapes that read as universal rules.
_DIRECTIVE_MARKERS = ("never", "always", "must", "should", "shall", "do not",
                      "use", "keep", "prefer", "when", "if", "before", "after",
                      "the only", "required", "forbidden", "authoritative")


def _universality(body):
    """Score how universal a neuron body is (0-1). Higher = more rule-like.

    Universal rules: terse, imperative, no dates, no incident markers.
    Dated records: timestamps, "session record" provenance, one-off fixes.
    """
    low = (body or "").lower()
    if _DATE_RE.search(body):
        return 0.0                      # dated — never universal
    hits = sum(1 for m in _DATED_MARKERS if m in low)
    if hits >= 2:
        return 0.0                      # strong incident-record signal
    if hits == 1:
        return 0.25
    # Directive language present and body is terse => likely a real rule.
    directive = sum(1 for m in _DIRECTIVE_MARKERS if m in low)
    words = len((body or "").split())
    base = 0.5
    base += min(0.25, directive * 0.05)
    # Terse beats verbose for universal rules (rules are concise; records ramble).
    base += 0.15 if words <= 40 else (0.0 if words <= 90 else -0.2)
    return max(0.0, min(1.0, base))


def always_digest(max_tokens=ALWAYS_DIGEST_TOKENS):
    """Return a compressed always-on digest: the top-priority neurons that
    should influence every turn, capped at a fixed token cost.

    Selection: importance DESC, then always_on/priority, then most-recently-
    touched. Bodies are AAAK-summaries when available so the digest stays tiny;
    the full body lives in the store and is fired by route() when relevant.
    Returns [] if the store is unreachable (degrade to zero injection)."""
    try:
        db = _db()
        rows = db.execute(
            "SELECT text, slug, triggers, importance, summary, always_on, "
            "priority, last_accessed FROM entries "
            "WHERE kind='neuron' AND status='active' "
            "ORDER BY always_on DESC, priority DESC, importance DESC, "
            "last_accessed DESC"
        ).fetchall()
        db.close()
    except Exception:
        return []
    out = []
    used = 0
    selected_bodies = []
    # Prefer substantive UNIVERSAL behavioral neurons over dated records.
    # Sort: topic priority first (operating > constitution/persona > project/
    # human), then UNIVERSALITY (rule-like beats dated), then always_on,
    # importance, then LEXICAL DENSITY (more distinctive content words = more
    # signal per token — the lexical system conserves bandwidth).
    TOPIC_ORDER = {"operating": 0, "constitution": 1, "persona": 2,
                   "project": 3, "human": 4, "general": 5}
    def topic_rank(slug):
        for t, rank in TOPIC_ORDER.items():
            if slug.startswith(t + "-") or slug == t:
                return rank
        return 6
    rows = sorted(rows, key=lambda r: (
        topic_rank(r[1]),             # slug topic
        -_universality(r[4] or r[0] or ""),  # universality desc (rules first)
        0 if r[5] else 1,             # always_on first
        -(float(r[3]) if r[3] else 0.5),  # importance desc
        -len(_lexical_signal(r[4] or r[0] or "")),  # lexical density desc
    ))
    for r in rows:
        if used >= max_tokens:
            break
        text, slug, triggers, importance, summary, always_on, priority, last_accessed = r
        # Skip junk fragments: no triggers OR trivial body (<12 chars) that
        # carries no signal ("Earth.", "I find it"). They belong nowhere.
        if not triggers or len((text or "").strip()) < 12:
            continue
        summ = (summary or "").strip()
        body = summ or text
        if not body:
            continue
        # UNIVERSALITY GATE: dated/incident records (universality 0) are
        # contextual, not always-on — they fire via keyword route() instead.
        if _universality(body) <= 0.0:
            continue
        # Lexical gate: no distinctive content words = stopword soup, skip.
        if not _lexical_signal(body):
            continue
        # Lexical dedup: skip if this body repeats an already-selected neuron's
        # idea (Jaccard overlap) — conserve bandwidth, don't re-inject the same
        # concept under a different slug.
        if any(_lexical_overlap(body, prev) >= _DEDUP_THRESHOLD
               for prev in selected_bodies):
            continue
        tok = max(1, len(body) // 4)
        # Skip oversized neurons — one that alone exceeds the digest budget
        # belongs in the keyword-fired layer, not the always-on digest (which
        # must stay fixed-low-cost every turn).
        if tok > max_tokens:
            continue
        if out and used + tok > max_tokens:
            continue
        out.append({
            "slug": slug,
            "body": body,
            "score": round(float(importance or 0.5), 2),
            "tokens": tok,
            "always": True,
            "compressed": bool(summ),
        })
        selected_bodies.append(body)
        used += tok
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["route", "list", "report", "promote-active", "always"])
    ap.add_argument("arg", nargs="?", default="", help="text / slug")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                    help="token budget for fired neurons (new-schema optimization)")
    ap.add_argument("--always-tokens", type=int, default=ALWAYS_DIGEST_TOKENS,
                    help="fixed per-turn budget for the always-on digest")
    ap.add_argument("--session", default="",
                    help="session id for ACTIVE priming (topic continuity across turns)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "always":
        dig = always_digest(max_tokens=args.always_tokens)
        if args.json:
            print(json.dumps({"digest": dig, "tokens": sum(f["tokens"] for f in dig)}))
            return
        if not dig:
            print("always-on digest: empty (store unreachable or no active neurons)")
            return
        print(f"always-on digest ({sum(f['tokens'] for f in dig)} tokens):\n")
        print(render_fired(dig))
        return

    if args.cmd == "list":
        neurons = list_neurons()
        print(f"{len(neurons)} warm neurons (store):")
        for n in neurons:
            print(f"  {n['slug']:<32} {n['tokens']:>4} tok  "
                  f"imp={n['importance']:.1f} triggers={len(n['triggers'])}")
        return

    if args.cmd == "promote-active":
        n = promote_active(dry_run=args.json)  # --json => dry-run
        print(f"promoted {n} non-resident entries to active neurons")
        return

    if args.cmd == "route":
        fired, total, all_scores = route(args.arg, max_tokens=args.max_tokens,
                                         session_id=args.session)
        graph_ctx = ""
        term = ""
        names = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\b", args.arg)
        if names:
            term = names[0]
        if term:
            graph_ctx = graph_context(term)
        if args.json:
            fired_out = list(fired)
            if graph_ctx:
                fired_out = fired + [{"slug": "graph-memory",
                                      "body": graph_ctx, "score": 0.5,
                                      "tokens": len(graph_ctx) // 4}]
            print(json.dumps({"fired": fired_out, "total_neurons": total}))
            return
        if not fired and not graph_ctx:
            print(f"no warm neurons fired for: {args.arg[:60]}")
            print(f"({total} neurons scanned; all scores 0 — nothing injected, "
                  f"zero tokens spent)")
            return
        print(f"{len(fired)}/{total} warm neurons fired "
              f"({sum(f['tokens'] for f in fired)} tokens):\n")
        if fired:
            print(render_fired(fired))
        if graph_ctx:
            print("\n" + graph_ctx)

    if args.cmd == "report":
        state = load_state()
        print("WARM TIER REPORT")
        print(f"  neurons: {len(list_neurons())} in the store")
        print(f"  recorded fires: {len(state.get('neurons', {}))} neurons "
              f"({sum(n.get('fires', 0) for n in state.get('neurons', {}).values())} total)")
        for n in list_neurons():
            if n["tokens"] > DEFAULT_MAX_TOKENS:
                print(f"  NOTE: '{n['slug']}' is ~{n['tokens']} tokens — it "
                      f"consumes the whole firing budget when it fires")


if __name__ == "__main__":
    main()
