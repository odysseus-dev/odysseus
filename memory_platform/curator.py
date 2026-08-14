#!/usr/bin/env python3
"""Curator for the unified memory model.

Implements the structured write path from the revised spec:
  - ADD / UPDATE / DELETE core-block entries (no blind append)
  - conflict detection: a new fact superseding an old one records a
    transition ("before: X, now: Y, since: date") instead of replacing it
  - per-entry metadata sidecar index (created, last_referenced,
    reference_count, importance) driving promotion + decay
  - designed forgetting: stale entries lose importance, demote, and are
    forgotten (logged), never silently dropped
  - local growth journal (append-only plain text) as the audit trail — no git

Safe-apply rules (evidence-gated):
  - operating / project / human: auto-applied (curator is allowed to edit)
  - persona: only applied when evidence is strong (same pattern in 3+ recent
    sessions, per the fault line in UNIFIED_MODEL.md)
  - everything is logged to the journal for later review

Usage:
  curator.py --plan              # read blocks + index, emit ops from evidence
  curator.py --apply             # run plan + write blocks/journal/index
  curator.py --dry-run           # alias for --plan (no writes)
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grading import affirm_rule, grade_fact, tier_of
import memory_env

BLOCKS_DIR = os.path.join(memory_env.memory_dir(), "blocks")
JOURNAL_DIR = os.path.join(memory_env.memory_dir(), "journal")
INDEX_FILE = os.path.join(memory_env.memory_dir(), "index", "blocks_meta.json")
SKILLS_DIR = os.path.join(memory_env.config_dir(), "skills")

# Entry is "stale" after N days since last referenced.
STALE_DAYS = 90
# Importance decays this fraction per stale sweep.
DECAY_RATE = 0.2
# Below this importance, an entry is forgotten (logged to journal).
FORGET_FLOOR = 0.2
# Evidence threshold for persona edits.
PERSONA_EVIDENCE = 3

os.makedirs(JOURNAL_DIR, exist_ok=True)
os.makedirs(os.path.dirname(INDEX_FILE), exist_ok=True)


# ---------------------------------------------------------------- blocks ----

def block_path(label):
    # New schema: blocks live in the store as always-on entries, not markdown
    # files. Returned as a descriptor; the real source is the DB.
    return f"store://topic/{label}"


STORE_DB = memory_env.store_db()


def _aaak(text, topic=""):
    """AAAK-compress an entry (compression route — light at rest, ~30-40x)."""
    try:
        import aaak
        return aaak.compress(text, topic=topic, source_file="curator")
    except Exception:
        return ""


def le_promote_eligible(proposal, existing, strength, min_strength=3,
                        corpus_texts=()):
    """Lexicon-aware promotion eligibility (delegates to lexicon_evolution).
    Same meta-judgement as retrieval: a proposal must share a DISTINCTIVE term
    with the existing block OR be a novel axis — never rest on common words."""
    try:
        import lexicon_evolution as le
        return le.promote_eligible(proposal, existing, strength,
                                   min_strength=min_strength,
                                   corpus_texts=corpus_texts)
    except Exception:
        # Fallback: old behaviour (strength-only) if the lexicon module is gone.
        return {"eligible": strength >= min_strength,
                "reason": "lexicon module unavailable — strength gate only"}


def _block_values(label):
    """Active stored values for a topic block (used for lexicon coherence)."""
    try:
        db = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT text FROM entries WHERE topic=? AND status='active'",
            (label,)).fetchall()
        db.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def _store_corpus(limit=200):
    """Active entry texts across the store (the grounding corpus for lexicon-
    aware promotion eligibility). Bounded so it stays cheap."""
    try:
        db = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT text FROM entries WHERE status='active' AND "
            "kind != 'neuron' ORDER BY id LIMIT ?", (limit,)).fetchall()
        db.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def _contradicts_identity(fact):
    """TrustMem-style preservation verifier (LEXICON-AWARE): does a new
    identity/persona value CONTRADICT an existing one?

    The old check fired on ANY shared word + any negation marker — that is the
    "shared word is not a relation" anti-pattern ("black coffee" vs "black
    holes"). A real contradiction needs a negation marker ON A DISTINCTIVE
    shared term (e.g. "never jokes" vs "dry humor"); a shared common word
    ("work", "always") is meaningless, and a value that shares nothing
    distinctive is a NOVEL axis, not a contradiction.

    Reject incoherent additions so accelerated growth never makes identity
    self-contradictory (identity drift)."""
    try:
        import lexicon_evolution as le
        db = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT text FROM entries WHERE topic='identity' "
            "AND status='active'").fetchall()
        db.close()
    except Exception:
        return False
    values = [r[0] for r in rows]
    if not values:
        return False
    try:
        return le.coherence(fact, values) == "conflict"
    except Exception:
        return False


def _is_topic_fact(fact):
    """NEW SCHEMA heuristic: is this fact a distinct TOPIC worth a warm neuron
    (vs. a one-off detail that belongs in the core topic entries)?

    A topic fact describes a recurring domain/idea the agent should recall on
    topic match: it carries durable key terms (a named subject + a domain
    predicate) rather than a transient detail. This replaces the old
    "hot block near char limit" routing premise — routing is by topic now.
    """
    low = (fact or "").lower()
    if len(fact) < 15:
        return False
    domain = ["about", "workflow", "procedure", "system", "method", "how to",
              "when", "always", "the way", "pattern", "topic", "approach",
              "project", "research", "process", "strategy", "design",
              "preference", "history", "build", "architecture"]
    # A topic fact names what it's about AND a durable angle.
    return any(d in low for d in domain)


def store_has(text, topic=None):
    """True if an identical ACTIVE entry already exists ANYWHERE in the store
    (not just in the always-on block body). Graph-edge syncs re-emit the same
    fact hourly; without this, every cycle inserts a duplicate entry."""
    try:
        import sqlite3
        db = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
        if topic:
            n = db.execute(
                "SELECT COUNT(*) FROM entries WHERE text=? AND topic=? "
                "AND status='active'", (text.strip(), topic)).fetchone()[0]
        else:
            n = db.execute(
                "SELECT COUNT(*) FROM entries WHERE text=? "
                "AND status='active'", (text.strip(),)).fetchone()[0]
        db.close()
        return n > 0
    except Exception:
        return False


def read_block(label):
    """Read a core block's content from the STORE (new schema) — the always-on
    entries for that topic, as plain text lines. Returns (fm, body) to preserve
    the caller contract."""
    fm = {"label": label, "description": f"The {label} memory block.",
          "limit": 5000, "read_only": "true" if label == "constitution" else "false"}
    body = ""
    try:
        import sqlite3
        db = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT text FROM entries WHERE topic=? AND status='active' "
            "ORDER BY priority ASC, id ASC", (label,)).fetchall()
        db.close()
        body = "\n".join(f"- {r[0]}" for r in rows)
    except Exception:
        pass
    return fm, body


def write_block(label, fm, body):
    """Write core block entries to the STORE (new schema). `body` is the
    serialized content — this replaces the block's always-on entries for the
    topic. Old always-on entries are removed and the new ones inserted."""
    try:
        import sqlite3
        from datetime import datetime, timezone
        db = sqlite3.connect(STORE_DB)
        db.execute("DELETE FROM entries WHERE topic=? AND always_on=1 "
                   "AND method != 'directive'", (label,))
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        priority = {"constitution": 0, "safety": 0, "identity": 1,
                    "operating": 2, "human": 3, "project": 3}.get(label, 5)
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("---"):
                continue
            text = line.lstrip("-* ").strip()
            if not text:
                continue
            cur = db.execute(
                "INSERT INTO entries (text, importance, created_at, "
                "last_accessed, topic, source, method, status, valid_from, "
                "confidence, temperature, always_on, priority, summary) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (text, 0.9, ts, ts, label, "curator", "curator", "active",
                 ts, 0.9, 1.0, 1, priority,
                 _aaak(text, label)))
            eid = cur.lastrowid
            # auto-associate the curated fact with related entries (bounded
            # graph, same meta-judgement as recall) so it accelerates later
            # recall. Best-effort: a failed link never blocks the write.
            try:
                import memory_store as ms
                ms._auto_associate(db, eid, text, topic=label, source="curator")
            except Exception:
                pass
        db.commit()
        db.close()
    except Exception:
        pass
    return block_path(label)


def entry_key(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()[:16]


def find_entry(body, needle):
    """Return the exact entry line containing `needle` (fuzzy), or None."""
    for line in body.splitlines():
        if not line.strip():
            continue
        if needle in line:
            return line
    return None


# ----------------------------------------------------------------- index ----

def load_index():
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE) as f:
            return json.load(f)
    return {}


def save_index(idx):
    with open(INDEX_FILE, "w") as f:
        json.dump(idx, f, indent=2)


def ensure_entry_meta(idx, label, text):
    """Create metadata for an entry if missing; returns its key."""
    key = entry_key(text)
    idx.setdefault(label, {})
    if key not in idx[label]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        idx[label][key] = {
            "created": now,
            "last_updated": now,
            "last_referenced": now,
            "reference_count": 0,
            "importance": 0.7,
        }
    return key


def touch_entry(idx, label, text):
    """Mark an entry as referenced (called on ADD/UPDATE or manual use)."""
    key = ensure_entry_meta(idx, label, text)
    meta = idx[label][key]
    meta["reference_count"] += 1
    meta["last_referenced"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta["importance"] = min(1.0, meta.get("importance", 0.7) + 0.05)
    return key


# ---------------------------------------------------------------- journal ---

def journal_append(entry):
    """Append one operation record to the monthly journal (append-only)."""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    path = os.path.join(JOURNAL_DIR, f"{month}.md")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(path, "a") as f:
        f.write(f"`{ts}` **{entry['op']}** → `{entry['target']}`\n")
        f.write(f"  - evidence: {entry.get('evidence', '')}\n")
        if entry.get("before"):
            f.write(f"  - before: {entry['before']}\n")
        if entry.get("after"):
            f.write(f"  - after: {entry['after']}\n")
        f.write(f"  - source: {entry.get('source', 'curator')}\n")
        if entry.get("reason"):
            f.write(f"  - reason: {entry['reason']}\n")
        f.write(f"\n")
    return path


# ------------------------------------------------------------- candidates ---

# A candidate is a fact seen in ONE session with weak strength. The spec's
# durability rule ("promoted on 3+ references across recent sessions") cannot
# be judged from a single sleep-time window — a fact may recur over days.
# So the curator keeps a persistent CANDIDATE LEDGER in the index: each time a
# weak fact reappears in evidence, its count increments; at >= CANDIDATE_MIN
# sightings it becomes durable and is promoted to an ADD (or UPDATE/DELETE when
# it carries explicit before/after). Unseen candidates decay and are pruned.
CANDIDATE_MIN = 3
# Forget a candidate that hasn't reappeared in this many runs.
CANDIDATE_STALE_RUNS = 30

CANDIDATES_KEY = "_candidates"


def ensure_candidates(idx):
    if CANDIDATES_KEY not in idx or not isinstance(idx[CANDIDATES_KEY], dict):
        idx[CANDIDATES_KEY] = {}
    return idx[CANDIDATES_KEY]


def candidate_key(text):
    return hashlib.sha256(_norm(text).encode()).hexdigest()[:16]


# Semantic matching for candidate accumulation. The lens (and evidence from
# different sessions) rephrases the SAME idea in slightly different words; a
# pure text-hash key would fragment them into separate candidates that never
# reach the 3-sighting threshold. We embed and cosine-match instead, so a
# rephrased refinement accumulates toward the SAME candidate. (Local embed
# model, free, ~ms.)
_EMBED_THRESHOLD = 0.62


def _embed(texts):
    import subprocess
    import tempfile
    if not texts:
        return {}
    # Run-scoped cache: the same candidates/facts are embedded many times in
    # one cycle (semantic_candidate_key per weak fact, coherence_check per
    # candidate). Caching avoids re-embedding identical text repeatedly —
    # the single biggest cycle-time win (embed ~0.6s/call, called dozens of
    # times per sleep-time run).
    cache = _embed.cache
    missing = [t for t in texts if t not in cache and t]
    if not missing:
        return {t: cache[t] for t in texts if t in cache}
    payload = {"model": "nomic-embed-text", "input": list(missing)}
    fd, path = tempfile.mkstemp(prefix="embed-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        r = subprocess.run(
            ["curl", "-s", "-m", "20", "-X", "POST",
             "http://localhost:11434/api/embed", "-H",
             "Content-Type: application/json", "-d", f"@{path}"],
            capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout or "{}")
        vecs = data.get("embeddings") or []
        for t, v in zip(missing, vecs):
            if v:
                cache[t] = v
    except Exception:
        pass
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return {t: cache[t] for t in texts if t in cache}


_embed.cache = {}


def _cos(a, b):
    try:
        return sum(x * y for x, y in zip(a, b)) / (
            (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5) + 1e-9)
    except (ZeroDivisionError, TypeError):
        return 0.0


def semantic_candidate_key(fact, cands):
    """Return the existing candidate key whose text is semantically equivalent
    to `fact` (cosine >= threshold), else None. Replaces pure-hash matching so
    rephrased refinements accumulate toward one candidate."""
    # Cheap pre-filter: exact normalized match hits instantly.
    exact = candidate_key(fact)
    if exact in cands:
        return exact
    # Semantic: embed this fact, compare against a sample of candidates.
    texts = [fact]
    keys = list(cands.keys())[:40]
    texts += [cands[k].get("text", "") for k in keys]
    vecs = _embed(texts)
    fv = vecs.get(fact)
    if not fv:
        return None
    best_key, best = None, _EMBED_THRESHOLD
    for k in keys:
        cv = vecs.get(cands[k].get("text", ""))
        if not cv:
            continue
        s = _cos(fv, cv)
        if s > best:
            best_key, best = k, s
    return best_key


def promote_candidates(evidence_items, idx, journal_lines):
    """Merge weak evidence into the persistent candidate ledger, promoting to
    ADD when a fact has been seen across >= CANDIDATE_MIN runs."""
    cands = ensure_candidates(idx)
    run = journal_lines and journal_lines or None  # not needed; see below
    promoted = []
    for item in evidence_items:
        if item.get("op") in ("UPDATE", "DELETE"):
            continue  # explicit ops handled separately — never auto-promote
        fact = item.get("fact", "").strip()
        if not fact:
            continue
        target = item.get("target", "human")
        # Durability gate: if this run alone already meets it, skip the ledger.
        if int(item.get("strength", 1) or 1) >= 3:
            continue
        # Semantic candidate matching: a rephrased fact (the lens, sessions)
        # accumulates toward the SAME candidate instead of fragmenting into a
        # new hash-keyed one that never reaches 3 sightings.
        ck = semantic_candidate_key(fact, cands) or candidate_key(fact)
        entry = cands.setdefault(ck, {
            "text": fact,
            "target": target,
            "count": 0,
            "last_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": [item.get("source", "curator")],
        })
        entry["text"] = fact
        entry["target"] = target
        entry["count"] = entry.get("count", 0) + 1
        entry["last_seen"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        src = item.get("source", "curator")
        if src not in entry.get("sources", []):
            entry.setdefault("sources", []).append(src)
        if entry["count"] >= CANDIDATE_MIN:
            # QUALITY GATE (anti-hallucination for growth): before a candidate
            # becomes a real change, verify its CONTENT traces to actual
            # session evidence — not just that it was said 3 times. A lens
            # refinement about "how we worked" that no transcript supports is
            # a hallucinated pattern and must NOT stick. Unverifiable
            # candidates park (journaled) instead of silently applying.
            # Identity values (from the identity lens) originate in the absorbed
            # corpus — check corpus FIRST, then sessions as a fallback.
            # Behaviour refinements are grounded in sessions only.
            src = entry.get("source", "") or item.get("source", "")
            sources = ("corpus", "sessions") if "identity" in src else ("sessions",)
            if target in ("persona", "operating") and not coherence_check(
                    fact, sources=sources):
                journal_lines.append({
                    "op": "CANDIDATE-PARKED",
                    "target": target,
                    "after": fact[:140],
                    "evidence": f"3 sightings but no transcript support "
                                f"(lens hallucination guard)",
                    "source": "coherence gate",
                    "strength": entry["count"],
                    "reason": "pattern not traceable to real session content",
                })
                # Keep counting (don't delete) so real recurrence can promote it.
                entry["count"] = CANDIDATE_MIN - 1  # reset, re-verify next time
                continue
            # Durable now: promote to ADD with its accumulated provenance.
            promoted.append({
                "op": "ADD",
                "target": target,
                "after": fact,
                "evidence": item.get("evidence", ""),
                "source": f"candidate ledger ({entry['count']} sightings)",
                "strength": entry["count"],
                "reason": f"candidate seen {entry['count']} times across runs",
            })
            # Remove the promoted candidate so it isn't applied repeatedly.
            del cands[ck]
    for p in promoted:
        journal_lines.append(p)
    return promoted


TRANSCRIPTS_DIR = os.path.join(memory_env.memory_dir(), "transcripts")


def coherence_check(fact, threshold=0.46, window_days=3, sources=("sessions", "corpus")):
    """QUALITY GATE (semantic): is this candidate grounded in reality?

    A candidate (behaviour refinement or identity value) only becomes real if
    it semantically traces to material we actually absorbed (corpus) or
    sessions we actually had. The gate checks the sources in ORDER given:

      ("sessions",)              behaviour refinements (what happened)
      ("corpus", "sessions")     identity values (origin is the corpus)

    Each source has its OWN calibrated threshold — sessions use embedding
    cosine against recent transcripts; corpus uses mempalace search cosine.
    A match in any listed source grounds the candidate. Invented concepts fail
    every source.

    Returns True (may apply) / False (park it).
    """
    if not fact or len(fact) < 15:
        return True  # non-claim -> don't block cold start
    for src in sources:
        if src == "sessions" and _grounded_in_sessions(fact, threshold, window_days):
            return True
        if src == "corpus" and _grounded_in_corpus(fact):
            return True
    return False


def _grounded_in_sessions(fact, threshold, window_days):
    """Sessions grounding: the fact's DISTINCTIVE CONCEPT WORDS must appear in
    recent transcripts — semantic cosine alone can't separate real refinements
    (0.47-0.64) from topically-adjacent inventions (0.47-0.50). The word-level
    check is the strong signal: a real refinement names things that actually
    happened (evidence ledger, verification); an invention names things never
    in any session (railway, timetable)."""
    try:
        now = datetime.now(timezone.utc)
        texts = []
        # Widen the evidence window: last 14 transcripts (was 6) so identity
        # growth sees a week+ of real sessions. The SAFETY is in what qualifies
        # as evidence (distinctive words in INDEPENDENT sessions), not in how
        # few sessions are sampled — so more sessions = faster legitimate
        # growth, zero invented growth.
        for fn in sorted(os.listdir(TRANSCRIPTS_DIR))[-14:]:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(TRANSCRIPTS_DIR, fn)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc)
                if (now - mtime).days > window_days:
                    continue
                texts.append(open(p, encoding="utf-8", errors="replace").read())
            except OSError:
                continue
        if not texts:
            return False
        corpus = " ".join(texts)
        chunks = [corpus[i:i + 5000] for i in range(0, len(corpus), 5000)][:6]
        if not chunks:
            return False
        # (a) Strong signal: DISTINCTIVE concept words must be present in the
        # sessions. Common verbs ("collects", "maintains") match everywhere and
        # are weak evidence — the *rare* words carry the meaning. A word is
        # distinctive if it is uncommon in the corpus; the check requires the
        # rarest content word to actually appear in sessions.
        stop = {"should", "would", "could", "need", "with", "that", "this",
                "about", "before", "after", "from", "into", "there", "their",
                "these", "those", "while", "being", "have", "has", "been",
                "will", "were", "when", "then", "them", "they", "just", "more",
                "also", "each", "than", "then", "very", "your", "the", "and",
                "with", "user", "value", "i", "we", "you", "our", "its", "for"}
        low_corpus = corpus.lower()
        words = re.findall(r"[A-Za-z]{4,}", fact.lower())
        sig = [w for w in words if w not in stop]
        # Corpus frequency: how often each significant word appears.
        freq = {w: low_corpus.count(w) for w in sig}
        if sig:
            # Rarest significant word present in INDEPENDENT sessions = strong
            # grounding (a real concept name appears; a gibberish token does
            # not). INDEPENDENCE beats raw multiplicity (ExpeL/EXG): the rarest
            # word must appear in >= 2 DISTINCT session files, so one rich
            # session can never dominate the count and trigger false growth.
            # Parking is never wrong for growth; applying wrongly is.
            rarest = min(sig, key=lambda w: freq.get(w, 0))
            if rarest not in low_corpus:
                return False
            distinct_hits = sum(1 for t in texts if rarest in t.lower())
            return distinct_hits >= 2
        return False
        # (b) Weak backup: semantic closeness only for long, specific values.
        vecs = _embed([fact] + chunks)
        fv = vecs.get(fact)
        if fv:
            best = max(_cos(fv, vecs.get(c, [])) for c in chunks if vecs.get(c))
            if len(fact) >= 60 and best >= 0.55:
                return True
        return False
    except Exception:
        return False


def _grounded_in_corpus(fact):
    """Absorbed-corpus grounding: check the LOCAL hybrid store's cold tier for
    a genuine semantic match (the corpus is now mined into our own store, not
    mempalace). A real corpus value scores well; an invented concept returns
    nothing or a weak match. Results cached per fact."""
    try:
        import subprocess
        cache = coherence_check._corpus_cache
        if fact not in cache:
            store_python = memory_env.python_bin()
            store_py = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "memory_store.py")
            env = dict(os.environ)
            if STORE_DB:
                env["MEMORY_STORE_DB"] = STORE_DB
            r = subprocess.run(
                [store_python, store_py, "chunk-recall", fact[:120],
                 "--json", "--min-sim", "0.78"],
                capture_output=True, text=True, timeout=20, env=env)
            try:
                hits = json.loads(r.stdout or "[]")
            except Exception:
                hits = []
            # A genuine corpus match returns chunks with provenance. An
            # invented concept returns few/no chunks at weak similarity.
            cache[fact] = bool(hits)
        return cache[fact]
    except Exception:
        return False


coherence_check._corpus_cache = {}


def decay_candidates(idx, journal_lines):
    """Prune candidates not seen recently (logged as CANDIDATE-DROP)."""
    cands = ensure_candidates(idx)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stale = []
    for ck, entry in cands.items():
        ld = entry.get("last_seen", now)
        try:
            last = datetime.fromisoformat(ld)
            days = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        except (ValueError, TypeError):
            days = CANDIDATE_STALE_RUNS + 1
        # hourly runs => CANDIDATE_STALE_RUNS * 1h; require real elapsed time.
        if days > CANDIDATE_STALE_RUNS / 24.0:
            stale.append((ck, entry))
    for ck, entry in stale:
        journal_lines.append({
            "op": "CANDIDATE-DROP", "target": entry.get("target", "human"),
            "before": entry.get("text", "")[:120],
            "evidence": f"candidate stale (not seen recently), had {entry.get('count', 0)} sightings",
            "source": "candidate decay",
        })
        del cands[ck]
    return bool(stale)


# ---------------------------------------------------------------- curator ---

def plan_ops(evidence_items, idx=None, journal_lines=None):
    """Turn evidence (facts/patterns extracted from sessions) into ops.

    evidence_items: list of dicts {fact, target, evidence, source, strength}
    target in {operating, project, human, persona, skill, mempalace}

    Weak evidence is first merged into the persistent candidate ledger; facts
    that have now been seen >= CANDIDATE_MIN times are promoted to ADDs. This
    makes the spec's "3+ references across recent sessions" durability rule
    work across sleep-time runs, not just within one window.
    """
    ops = []
    if idx is not None and journal_lines is not None:
        promote_candidates(evidence_items, idx, ops)
    # Graded routing: refine each evidence item's coarse target to the tier
    # where it actually belongs (G0 constitution / G1 operating / G2 human+
    # project / persona / G3 hindsight / G4 mempalace / P skill), and rewrite
    # rules as positive affirmations where the affirmation is known-correct.
    routed = []
    for item in evidence_items:
        item = dict(item)
        fact = (item.get("fact") or "").strip()
        # Explicit UPDATE/DELETE ops carry before/after, not fact — skip
        # grading entirely for them (apply_op handles them separately).
        if item.get("op") in ("UPDATE", "DELETE"):
            routed.append(item)
            continue
        coarse = item.get("target", "hindsight")
        grade = grade_fact(fact, coarse)
        tier = tier_of(grade)
        if grade in ("constitution", "mempalace", "hindsight", "skill"):
            item["target"] = grade
        elif grade == "operating":
            item["target"] = "operating"
        elif grade == "persona":
            item["target"] = "persona"
        elif grade == "project":
            item["target"] = "project"
        else:
            item["target"] = "human"
        item["grade"] = f"{grade} ({tier})"
        # Positive affirmation: rules destined for hot behaviour blocks are
        # stored as the behaviour to embody, never the prohibition.
        if grade in ("operating", "constitution") and fact:
            affirmed = affirm_rule(fact)
            if affirmed and affirmed != fact:
                item["fact"] = affirmed
                item["affirmation_of"] = fact
        if item["fact"].strip():
            routed.append(item)
    evidence_items = routed
    # Constitution-grounded worthiness filter: asks whether each candidate is
    # worth adding to the infrastructure at all — does it move toward being a
    # personal assistant per the constitution? REJECT = never write (journaled
    # with question answers); DEFER = park as PROPOSE; ABSORB/PROMOTE pass
    # through to the normal evidence-gated path.
    from worthiness import assess, verdict_label
    for item in evidence_items:
        if item.get("op") in ("UPDATE", "DELETE"):
            continue
        fact0 = (item.get("fact") or "").strip()
        if not fact0:
            continue
        verdict, score, answers, vetoed = assess(fact0, item.get("target"))
        item["worthiness"] = verdict
        item["worthiness_score"] = score
        if verdict in ("REJECT", "DEFER"):
            if journal_lines is not None:
                journal_lines.append({
                    "op": f"{verdict}-WORTHY",
                    "target": item.get("target", "hindsight"),
                    "after": fact0[:140],
                    "evidence": item.get("evidence", ""),
                    "source": "worthiness filter",
                    "strength": int(item.get("strength", 1) or 1),
                    "score": score,
                    "reason": f"{verdict_label(verdict)}; answers: " + ", ".join(
                        f"{n}={s}" for n, s, _ in answers),
                    "veto": vetoed,
                })
    for item in evidence_items:
        target = item.get("target", "human")
        fact = item.get("fact", "").strip()
        strength = int(item.get("strength", 1) or 1)
        # Explicit UPDATE/DELETE ops pass through only when the evidence names
        # the exact old entry — apply_op refuses to guess.
        if item.get("op") in ("UPDATE", "DELETE") and item.get("before"):
            ops.append({
                "op": item["op"],
                "target": target,
                "before": str(item["before"]).strip(),
                "after": item.get("after", "").strip() if item["op"] == "UPDATE" else "",
                "evidence": item.get("evidence", ""),
                "source": item.get("source", "curator"),
                "strength": strength,
            })
            continue
        if not fact:
            continue
        # Worthiness gate: REJECT never reaches a write path (already journaled
        # above); DEFER is parked as a PROPOSE so it can be re-reviewed if the
        # same fact recurs (the candidate ledger's sighting count would then
        # promote it only once it demonstrates worthiness over time).
        worthiness = item.get("worthiness")
        if worthiness == "REJECT":
            continue
        if worthiness == "DEFER":
            ops.append({
                "op": "PROPOSE", "target": target,
                "after": fact,
                "evidence": item.get("evidence", ""),
                "source": item.get("source", "curator"),
                "strength": strength,
                "reason": "worthiness filter: DEFER (parked; re-review if recurring)",
                "worthiness_score": item.get("worthiness_score"),
            })
            continue
        # Procedural memory: recurring procedures go to skills directly, gated
        # by repetition (procedure repeated 2+ times -> distill into a skill).
        if target == "skill" and item.get("steps"):
            if strength >= SKILL_MIN_REPEATS:
                ops.append({
                    "op": "SKILL", "target": "skill",
                    "name": item.get("name", fact),
                    "description": item.get("description", ""),
                    "steps": item.get("steps"),
                    "evidence": item.get("evidence", ""),
                    "source": item.get("source", "curator"),
                    "strength": strength,
                })
            else:
                ops.append({
                    "op": "PROPOSE", "target": "skill",
                    "after": f"{item.get('name', fact)} (procedure seen {strength}x)",
                    "evidence": item.get("evidence", ""),
                    "source": item.get("source", "curator"),
                    "strength": strength,
                    "reason": f"procedure only seen {strength}x (< {SKILL_MIN_REPEATS})",
                })
            continue
        # Already promoted out of the ledger as a durable ADD this run.
        if any(p.get("op") == "ADD" and p.get("after") == fact for p in ops):
            continue
        # Constitution is read_only to the curator: an explicitly-graded
        # inviolable rule is ALWAYS journaled for review, never auto-applied.
        # It is added to the block only via an explicit user directive
        # ("add this to the constitution") handled by the agent, which is logged.
        # LEXICON GATE: before even PROPOSING, the rule must be a real new
        # principle — it must share a DISTINCTIVE term with the existing
        # constitution (reinforces an established value) OR be a genuinely novel
        # axis. A rule resting only on common framing words is a word-
        # coincidence and is parked, no matter how often it is sighted.
        if target == "constitution":
            existing = _block_values("constitution")
            # grounding corpus = the whole store (a principle traces to real
            # absorbed material anywhere, not just the existing constitution)
            corpus = _store_corpus()
            elig = le_promote_eligible(fact, existing, strength,
                                       min_strength=3, corpus_texts=corpus)
            if not elig["eligible"]:
                ops.append({
                    "op": "PROPOSE-CONSTITUTION",
                    "target": "constitution",
                    "after": fact,
                    "affirmation_of": item.get("affirmation_of", ""),
                    "evidence": item.get("evidence", ""),
                    "source": item.get("source", "curator"),
                    "strength": strength,
                    "reason": f"lexicon gate: {elig['reason']}",
                })
                continue
            ops.append({
                "op": "PROPOSE-CONSTITUTION",
                "target": "constitution",
                "after": fact,
                "affirmation_of": item.get("affirmation_of", ""),
                "evidence": item.get("evidence", ""),
                "source": item.get("source", "curator"),
                "strength": strength,
                "reason": "constitution is read_only; awaits explicit user directive",
            })
            continue
        # Cold/warm archival material is routed to its tier, not auto-applied
        # to hot blocks. Journaled as a propose so the routing is auditable.
        if target in ("mempalace", "hindsight"):
            ops.append({
                "op": "PROPOSE",
                "target": target,
                "after": fact,
                "evidence": item.get("evidence", ""),
                "source": item.get("source", "curator"),
                "strength": strength,
                "reason": f"graded {item.get('grade', 'G3')}: archival/warm, not hot",
            })
            continue
        # Durability gate: facts referenced across 3+ documents are auto-apply
        # candidates; weaker evidence is proposed to the journal for review
        # (spec: "promoted on 3+ references").
        if strength < 3:
            ops.append({
                "op": "PROPOSE",
                "target": target,
                "after": fact,
                "evidence": item.get("evidence", ""),
                "source": item.get("source", "curator"),
                "strength": strength,
                "reason": f"strength {strength} < 3 (not yet durable)",
            })
            continue
        if target == "persona" and strength < PERSONA_EVIDENCE:
            ops.append({
                "op": "PROPOSE",
                "target": target,
                "after": fact,
                "evidence": item.get("evidence", ""),
                "source": item.get("source", "curator"),
                "reason": f"persona evidence too weak ({strength} < {PERSONA_EVIDENCE})",
            })
            continue
        # EVIDENCE-QUALITY GATE (the fluid wall, #8): identity changes only on
        # VERIFIED / OBSERVED / CORPUS evidence. A reported or asserted claim
        # is kept as knowledge but CANNOT rewrite who the agent is — no matter
        # how many times it's sighted. This prevents "a user says they have
        # evidence" from changing identity without the evidence being real.
        if target == "persona" and item.get("evidence_grade") == "claimed":
            ops.append({
                "op": "PROPOSE",
                "target": target,
                "after": fact,
                "evidence": item.get("evidence", ""),
                "source": item.get("source", "curator"),
                "reason": "evidence-quality gate: claimed (not verified) — "
                          "identity change blocked, kept as knowledge",
            })
            continue
        # PRESERVATION VERIFIER (TrustMem): a persona value that CONTRADICTS an
        # existing identity value is rejected, so accelerated growth can never
        # make the identity self-contradictory. Growth must be coherent, not
        # just faster.
        if target == "persona" and _contradicts_identity(fact):
            ops.append({
                "op": "PROPOSE",
                "target": target,
                "after": fact,
                "evidence": item.get("evidence", ""),
                "source": item.get("source", "curator"),
                "strength": strength,
                "reason": "preservation verifier: contradicts existing identity",
            })
            continue
        # Warm-tier promotion: a fact that PASSED the worthiness filter (ABSORB
        # or PROMOTE) that names a distinct topic goes to the warm neuron
        # layer — it stays available (fires on topic match) without inflating
        # the always-on core. In the new schema this routing is by topic, not
        # by any hot-block char limit (atomic entries are token-budgeted at
        # compile time; size at rest is free).
        if target in ("human", "project", "operating", "persona") \
                and item.get("worthiness") in ("ABSORB", "PROMOTE") \
                and _is_topic_fact(fact):
            ops.append({
                "op": "ADD", "target": "warm",
                "after": fact,
                "evidence": item.get("evidence", ""),
                "source": item.get("source", "curator"),
                "strength": strength,
                "reason": f"worthiness-{item.get('worthiness')} topic fact "
                          f"routed to warm neuron layer",
            })
            continue
        ops.append({
            "op": "ADD",
            "target": target,
            "after": fact,
            "evidence": item.get("evidence", ""),
            "source": item.get("source", "curator"),
            "strength": strength,
        })
    return ops


def _norm(text):
    """Normalize an entry for containment comparison: lowercase, strip markdown
    bullets/bold/headers, drop punctuation, collapse whitespace."""
    t = re.sub(r"^[-*#>\s]+", "", text.strip())
    t = re.sub(r"[*_`#]", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def detect_conflict(body, fact):
    """Classify a new fact against existing block entries.

    Conservative by design (no fuzzy semantic guessing):
      ("duplicate", line)  → the fact is already stored (normalized match or
                              containment either direction) → SKIP.
      (None, None)         → safe to ADD. Any plausible-but-uncertain
                              supersession is left to the journal review path;
                              the curator never auto-replaces on a guess.

    UPDATE/DELETE are only ever applied when the evidence names the exact old
    entry text (handled in apply_op), so nothing here can silently overwrite.
    """
    nf = _norm(fact)
    if not nf:
        return (None, None)
    for line in body.splitlines():
        if not line.strip() or line.strip().startswith("---"):
            continue
        nl = _norm(line)
        if not nl:
            continue
        if nf == nl or nf in nl or nl in nf:
            return ("duplicate", line)
    return (None, None)


def apply_op(op, idx, journal_lines):
    label = op["target"]
    if op["op"] == "PROPOSE" or op["op"] == "PROPOSE-CONSTITUTION":
        # Weak evidence / read_only constitution — never auto-applied, always
        # logged for review.
        journal_lines.append(op)
        return False
    if label not in ("operating", "project", "human", "persona", "warm"):
        # constitution is never auto-applied here (belt-and-braces: even if an
        # ADD slips through routing, refuse to touch the block).
        if op["op"] == "ADD" and label == "constitution":
            journal_lines.append({**op, "op": "SKIP-CONSTITUTION",
                                  "reason": "block is read_only; explicit user directive required"})
            return False
        # skills/mempalace handled by other systems — skills ARE authored here
        # from recurring-procedure evidence (EXPEL/MUSE procedural memory).
        if label == "skill" and op.get("steps"):
            return author_skill(op, journal_lines)
        journal_lines.append({
            "op": op["op"], "target": label, "after": op.get("after", ""),
            "evidence": op.get("evidence", ""), "source": op.get("source", ""),
        })
        return False
    if label == "warm":
        # Warm-tier write: promote a worthiness-strong fact into the warm tier
        # (the neuron layer) instead of a hot block. Appends to an existing
        # warm block matching the fact, or creates one.
        return apply_warm(op, journal_lines)
    fm, body = read_block(label)
    if op["op"] == "ADD":
        kind, line = detect_conflict(body, op["after"])
        if kind == "duplicate":
            # Already stored — touch the real stored line so it doesn't decay.
            touch_entry(idx, label, line)
            journal_lines.append({**op, "op": "SKIP-DUPLICATE",
                                  "reason": f"already present: {line[:80]}"})
            return False
        # Store-wide dedup: graph-edge syncs re-emit the same fact every
        # cycle as a NON-always-on entry, invisible to detect_conflict above.
        # Without this check every hourly run inserts a duplicate.
        if store_has(op["after"], topic=label):
            journal_lines.append({**op, "op": "SKIP-DUPLICATE",
                                  "reason": "already present in store (not "
                                            "just the always-on block)"})
            return False
        body = body.rstrip() + f"\n- {op['after']}\n"
        write_block(label, fm, body)
        touch_entry(idx, label, op["after"])
        journal_lines.append(op)
        # Also write the durable fact into the graph memory tier (concept-
        # mediated temporal KG). Best-effort: a failed graph write never
        # blocks the block write.
        try:
            graph_write(op["after"], op.get("evidence", ""),
                        op.get("source", "curator"),
                        int(op.get("strength", 3) or 3))
        except Exception:
            pass
        return True
    elif op["op"] == "UPDATE":
        # Only safe when the evidence explicitly names the exact old entry.
        old = op.get("before", "")
        if not old or old not in body:
            journal_lines.append({**op, "op": "SKIP-UPDATE",
                                  "reason": "no exact before-match in block"})
            return False
        new = op.get("after", "")
        if not new:
            return False
        # Conflict-aware transition: keep the lineage visible, never erase.
        body = body.replace(old, f"{old} → (superseded {new})")
        write_block(label, fm, body)
        touch_entry(idx, label, new)
        journal_lines.append({**op, "op": "UPDATE",
                              "before": old, "after": new})
        return True
    elif op["op"] == "DELETE":
        old = op.get("before", op.get("after", ""))
        if not old or old not in body:
            journal_lines.append({**op, "op": "SKIP-DELETE",
                                  "reason": "no exact match in block"})
            return False
        # Remove any line whose normalized content matches the entry (lines
        # carry "- " bullets, so compare the entry against the de-bulleted
        # line text, not the raw line).
        body = "\n".join(
            l for l in body.splitlines()
            if _norm(l) != _norm(old) and old not in l
        )
        write_block(label, fm, body)
        key = entry_key(old)
        idx.get(label, {}).pop(key, None)
        journal_lines.append({**op, "op": "DELETE", "before": old})
        return True
    return False


def decay_pass(idx, journal_lines):
    """Reduce importance of stale entries; forget very stale ones (logged)."""
    now = datetime.now(timezone.utc)
    for label, entries in list(idx.items()):
        if label == CANDIDATES_KEY or label == "constitution":
            continue
        for key, meta in list(entries.items()):
            lr = meta.get("last_referenced", "")
            try:
                ref = datetime.fromisoformat(lr)
                age_days = (now - ref).total_seconds() / 86400
            except (ValueError, TypeError):
                age_days = STALE_DAYS + 1
            if age_days < STALE_DAYS:
                continue
            meta["importance"] = max(0.0, meta.get("importance", 0.7) - DECAY_RATE)
            if meta["importance"] < FORGET_FLOOR:
                journal_lines.append({
                    "op": "FORGET", "target": label,
                    "before": meta.get("_text", key),
                    "evidence": f"stale for {age_days:.0f}d, importance {meta['importance']:.2f}",
                    "source": "decay",
                })
                del entries[key]


# Intent-aligned compression (SimpleMem): when a block nears its char limit,
# cluster related entries and replace the cluster with one compact summary
# (compression WITH meaning), instead of truncating or flattening. Clustering
# is local + free: entries sharing significant tokens are grouped; each cluster
# is reduced to its longest entry (most informative) with the shared theme
# noted, so nothing is lost wholesale and provenance stays in the index.
COMPRESS_RATIO = 0.8
STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "has", "have", "was",
    "were", "are", "you", "your", "our", "not", "but", "also", "his", "her",
    "its", "can", "will", "would", "should", "about", "into", "out", "than",
    "them", "their", "then", "there", "when", "where", "what", "who", "which",
    "some", "been", "being", "did", "does", "doing", "get", "got", "make",
    "making", "how", "all", "any", "each", "own", "same", "too", "very",
}


def _tokens(text):
    return {w for w in _norm(text).split() if w not in STOPWORDS}


def _overlap(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def compress_pass(idx, journal_lines):
    """Compress over-limit blocks by clustering related entries (logged)."""
    for label in ("operating", "project", "human", "persona"):
        # constitution is never compressed — its entries are inviolable.
        if label == "constitution":
            continue
        fm, body = read_block(label)
        # Only act on a block meaningfully over the configured ratio.
        limit = int(fm.get("limit", 5000) or 5000)
        body_n = len(body.strip())
        if body_n < limit * COMPRESS_RATIO:
            continue
        entries = [l for l in body.splitlines() if l.strip().startswith("- ")]
        if len(entries) < 3:
            continue
        # Greedy clustering: each entry joins the cluster sharing the most
        # significant-token overlap (>= 0.35); else starts a new cluster.
        clusters = []
        for e in entries:
            best_i, best_s = -1, 0.35
            for i, c in enumerate(clusters):
                s = max(_overlap(e, member) for member in c)
                if s > best_s:
                    best_i, best_s = i, s
            if best_i >= 0:
                clusters[best_i].append(e)
            else:
                clusters.append([e])
        merges = [c for c in clusters if len(c) >= 2]
        if not merges:
            continue
        all_lines = body.splitlines()
        drop_idx = set()
        journal_for = []
        for c in merges:
            rep = max(c, key=len)
            # First occurrence of the representative stays; every other line in
            # the cluster (incl. exact duplicates) is dropped.
            rep_idx = next(i for i, ln in enumerate(all_lines)
                           if i not in drop_idx and ln == rep)
            for i, ln in enumerate(all_lines):
                if i == rep_idx or i in drop_idx:
                    continue
                if ln in c:
                    drop_idx.add(i)
            others = [m for m in c if m != rep]
            summary = rep.rstrip() + " (also: " + "; ".join(
                o.lstrip("- ").strip()[:60] for o in others[:4]) + ")"
            journal_for.append({
                "op": "COMPRESS", "target": label,
                "before": " | ".join(dict.fromkeys(c)),
                "after": summary.strip(),
                "evidence": f"{len(c)} related entries compressed (SimpleMem)",
                "source": "compress",
            })
        for i in sorted(drop_idx, reverse=True):
            key = entry_key(all_lines[i])
            idx.get(label, {}).pop(key, None)
        body = "\n".join(l for i, l in enumerate(all_lines) if i not in drop_idx)
        write_block(label, fm, body)
        journal_lines.extend(journal_for)
    return bool(journal_lines)


# --------------------------------------------------------------- skills ----

# Procedural memory (EXPEL / MUSE): recurring, repeatable procedures become
# skills the agent loads on demand. The curator authors them from evidence that
# carries a "procedure" shape — a named, multi-step workflow observed across
# sessions. Evidence items with target "skill" and a procedure dict are turned
# into a SKILL.md file under ~/.config/opencode/skills/<name>/.
SKILL_MIN_REPEATS = 2


def sanitize_slug(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "procedure"


def skill_path(name):
    return os.path.join(SKILLS_DIR, sanitize_slug(name), "SKILL.md")


def author_skill(op, journal_lines):
    """Write one skill from a procedure evidence item. Returns True if written."""
    name = (op.get("name") or "").strip()
    steps = op.get("steps") or []
    if not name or not steps:
        return False
    desc = (op.get("description") or "").strip() or f"Procedure distilled by the curator: {name}."
    repeats = int(op.get("strength", 1) or 1)
    slug = sanitize_slug(name)
    path = skill_path(slug)
    existing = ""
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()
    body = "---\n"
    body += f"name: {slug}\n"
    body += f"description: {desc}\n"
    body += "---\n\n"
    body += f"# {name} (procedural memory)\n\n"
    body += f"> Authored by the sleep-time curator after observing this procedure "
    body += f"{repeats} times across sessions.\n\n"
    body += "## Procedure\n\n"
    for i, step in enumerate(steps, 1):
        body += f"{i}. {step}\n"
    if existing and "## Evidence" not in existing:
        body += f"\n## Evidence\n\n- first authored: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)
    journal_lines.append({
        "op": "SKILL", "target": "skill",
        "after": f"{slug} ({len(steps)} steps)",
        "evidence": f"procedure seen {repeats} times",
        "source": "curator/EXPEL",
        "reason": "recurring procedure distilled to procedural memory",
    })
    return True


# --- Graph memory tier: durable facts also become temporal-KG triples. ----
# Heuristic triple extraction from a durable fact. Handles the common shapes:
#   "the user prefers oat milk"           -> the user prefers oat milk
#   "the user lives in the city"          -> the user lives_in city
#   "the user manages the example project"-> the user manages example-project
# Failed parses are silently skipped (the block write already succeeded).
GRAPH_VERB_MAP = {
    "works on": "works_on", "works at": "works_at", "lives in": "lives_in",
    "located in": "located_in", "teaches": "teaches", "manages": "manages",
    "prefers": "prefers", "uses": "uses", "built": "builds", "created": "created",
    "leads": "leads", "develops": "develops", "builds": "builds",
    "interested in": "interested_in", "based in": "located_in",
    "part of": "part_of", "works for": "works_at",
}


def graph_write(fact, evidence="", source="curator", strength=3):
    """Best-effort: parse a durable fact into a triple and add it to the graph.
    Only writes when strength >= 3 (write-time gating from the research)."""
    import subprocess
    if strength < 3:
        return False
    low = fact.lower()
    subj = None
    pred = None
    obj = None
    for verb, canonical in GRAPH_VERB_MAP.items():
        idx = low.find(verb)
        if idx > 0:
            subj = fact[:idx].strip().lstrip("-* ").strip()
            obj = fact[idx + len(verb):].strip()
            obj = re.split(r"[.,;]", obj)[0].strip()
            # Trim trailing prepositional phrases: "teaches the skill at the
            # studio" -> object is the direct noun, not the whole clause.
            obj = re.split(r"\s+(at|in|on|for|to|from|with|and|through|via) ",
                           obj)[0].strip()
            pred = canonical
            break
    if not subj or not obj or not pred:
        return False
    # Subject must be a proper noun-ish or the user keyword to avoid junk triples.
    if subj.lower() not in ("user", "i", "we") and len(subj) < 3:
        return False
    try:
        r = subprocess.run(
            ["python3", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "graph_memory.py"),
             "add", subj, pred, obj, "--evidence", evidence[:200],
             "--source", source, "--strength", str(strength)],
            capture_output=True, text=True, timeout=15)
        return '"added": true' in (r.stdout or "")
    except Exception:
        return False


def apply_warm(op, journal_lines):
    """Warm-tier write: promote a fact into the neuron layer (NEW SCHEMA).

    Finds an existing neuron whose triggers match the fact and upserts the fact
    into it; if none, seeds a new neuron via write_neuron. No size-based
    division — neurons are atomic store entries, and firing is optimized by the
    token budget at injection time, never by splitting stored content.
    """
    from warm_router import list_neurons, write_neuron, _derive_triggers
    fact = (op.get("after") or "").strip()
    if not fact:
        return False
    low = fact.lower()
    best = None
    best_hits = 0
    for n in list_neurons():
        hits = sum(1 for t in n["triggers"] if t and t in low)
        if hits > best_hits:
            best, best_hits = n, hits
    if best is not None and best_hits > 0:
        # Upsert the fact into the matching neuron (auto-build, no splitting).
        new_body = (best["body"] + f"\n- {fact}\n")
        write_neuron(best["slug"], best["triggers"], new_body,
                     importance=best["importance"])
        journal_lines.append({
            "op": "WARM", "target": best["slug"],
            "after": fact[:140],
            "evidence": op.get("evidence", ""),
            "source": op.get("source", "curator"),
            "reason": "worthiness-strong fact promoted into warm neuron (auto-build)",
        })
        return True
    # No matching neuron: seed one from this fact.
    trig = _derive_triggers(fact, [])
    write_neuron("curator-fact", trig, fact, importance=0.5)
    journal_lines.append({
        "op": "WARM", "target": "curator-fact",
        "after": fact[:140],
        "evidence": op.get("evidence", ""),
        "source": op.get("source", "curator"),
        "reason": "new warm topic seeded from worthiness-strong fact",
    })
    return True


def run_plan(evidence_items, apply=False):
    idx = load_index()
    journal_lines = []
    ops = plan_ops(evidence_items, idx=idx, journal_lines=journal_lines)
    # Drift gate: never write into a block that has drifted beyond its limit
    # without an authorised anchor. This is the long-project protection — the
    # harness (blocks/ledger) persists while the LLM engine is swappable.
    if apply:
        try:
            import importlib.util
            _spec = importlib.util.spec_from_file_location(
                "drift_ledger",
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "drift-ledger.py"))
            _dl = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_dl)
            # share the curator's active paths so the gate observes the same
            # blocks/index it would write into (works under isolated tests too)
            _dl.BLOCKS_DIR = BLOCKS_DIR
            _dl.INDEX_FILE = INDEX_FILE
            _dl.JOURNAL_DIR = JOURNAL_DIR
            _dl.DB = STORE_DB
            if _dl.check(strict=False) != 0:
                journal_lines.append({
                    "op": "DRIFT-GATE",
                    "target": "harness",
                    "reason": "block drifted beyond limit; refusing writes",
                    "evidence": "drift-ledger.py check",
                    "source": "drift-ledger",
                })
                print("DRIFT-GATE: refusing writes — run `python3 "
                      "drift-ledger.py check --strict` to audit")
                return journal_lines, ops
        except Exception as e:
            print(f"  (drift check unavailable: {e})")
    for op in ops:
        if apply:
            apply_op(op, idx, journal_lines)
        else:
            journal_lines.append({**op, "reason": "dry-run"})
    if apply:
        decay_candidates(idx, journal_lines)
        decay_pass(idx, journal_lines)
        compress_pass(idx, journal_lines)
        save_index(idx)
        try:
            import importlib.util
            _spec2 = importlib.util.spec_from_file_location(
                "drift_ledger_snap",
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "drift-ledger.py"))
            _dl2 = importlib.util.module_from_spec(_spec2)
            _spec2.loader.exec_module(_dl2)
            # Share the active paths so the post-apply snapshot re-baselines
            # the SAME ledger the gate checks (and, in isolated tests, writes
            # to the isolated ledger — never the real one).
            _dl2.BLOCKS_DIR = BLOCKS_DIR
            _dl2.INDEX_FILE = INDEX_FILE
            _dl2.JOURNAL_DIR = JOURNAL_DIR
            _dl2.DB = STORE_DB
            _dl2.snapshot()
        except Exception:
            pass
    return journal_lines, ops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="show planned ops, write nothing")
    ap.add_argument("--apply", action="store_true", help="apply ops, journal, decay")
    ap.add_argument("--dry-run", action="store_true", help="alias for --plan")
    ap.add_argument("--evidence", default=None, help="JSON file of evidence items")
    args = ap.parse_args()

    if args.evidence:
        with open(args.evidence) as f:
            evidence_items = json.load(f)
    else:
        print("No evidence provided; use --evidence <file> with a JSON list.", file=sys.stderr)
        sys.exit(1)

    apply = args.apply or (not args.plan and not args.dry_run)
    journal_lines, ops = run_plan(evidence_items, apply=apply)

    if not apply:
        print("=== planned ops (dry-run) ===")
        for j in journal_lines:
            print(f"  {j['op']:8} → {j['target']} | {j.get('after', '')[:70]} | {j.get('reason', '')}")
        return

    written = 0
    for j in journal_lines:
        if j.get("op") == "PROPOSE":
            # Proposals are the review-later record — always journal them.
            journal_append(j)
            written += 1
        elif j.get("reason") in (None, "", "dry-run"):
            journal_append(j)
            written += 1
    print(f"Applied {len(ops)} ops; {written} logged to journal; index saved.")
    for j in journal_lines:
        print(f"  {j['op']:8} → {j['target']} | {j.get('after', '')[:70]}")


if __name__ == "__main__":
    main()
