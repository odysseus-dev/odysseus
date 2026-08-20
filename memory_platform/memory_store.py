#!/usr/bin/env python3
"""memory_store.py — the hybrid memory store (core of the rebuild).

Replaces the bounded block model with atomic factoid entries in one SQLite
file running sqlite-vec (dense vectors) + FTS5 (BM25) side by side.

Research-grounded design (2026-08-12, four briefs):
  - ATOMIC ENTRIES, not blocks: short facts with metadata. Mem0/A-MEM/Zep all
    beat narrative blocks on LOCOMO at ~7k tokens vs 600k+ for chunk-caches.
  - HYBRID RETRIEVAL: dense (vec0) + BM25 (FTS5) fused by RRF (rank-based,
    immune to scale mismatch). Real terms BM25 catches that dense misses.
  - METADATA on every entry: importance, recency, topic, entities, source,
    validity window — drives recall ranking + decay + provenance.
  - PROVENANCE: every entry carries source transcript + method, feeding the
    claim-audit ledger.
  - WORKING MEMORY: a separate table for current-task state, cleared per task.
  - EFFICIENCY: 256-dim embeddings (Matryoshka-truncated), content-hash so we
    never re-embed unchanged text, batched embed calls.

Schema:
  entries(id, text, embedding[256], importance, created_at, last_accessed,
          topic, entities, source, method, status, valid_from, valid_until)
  working(id, task_id, text, created_at)
  audit_log(id, prev_hash, hash, claim, verdict, evidence, created_at)  -- hash chain

Uses Odysseus-native paths via memory_env — all data under DATA_DIR.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

# Odysseus-native path resolution — no hardcoded user paths.
try:
    from . import memory_env
except ImportError:
    import memory_env

STORE_DIR = memory_env.store_dir()
DB_PATH = memory_env.store_db()
OLLAMA_URL = memory_env.ollama_url()
EMBED_MODEL = memory_env.embed_model()
EMBED_DIM = memory_env.embed_dim()

# Token budget per recall (research: 3-8 factoids, ~<=2k tokens).
RECALL_BUDGET = 8
RECALL_MIN_SCORE = 0.42  # below this -> abstain (don't inject distractors)
RRF_K = 15  # small-corpus RRF constant (large corpora want 60)
# Recency half-life: a fact last accessed RECENCY_HALF_LIFE days ago scores
# at half the recency weight of one accessed today. A gentle ranking nudge.
RECENCY_HALF_LIFE = 30.0

# Lexicon semantics version. Bump this whenever the lexical logic changes
# (conjunctive matching, stopwords, rare-word distinctiveness, fusion). On
# startup, if the stored lexicon version differs, the store retroactively
# reconciles DERIVED aspects — associations (rebuilt on distinctive-word +
# dense-proximity signal) and neuron triggers — so nothing built on the old
# loose matching stays stale. This is the system's retroactive rule applied
# to the lexicon itself.
LEXICON_VERSION = 2


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cosine_sim(a, b):
    """Pure Python cosine similarity — no numpy needed."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _dense_search(db, qvec, budget):
    """Brute-force dense search over embedding BLOBs.

    Loads all embeddings, computes cosine similarity, returns top-k.
    Fast for <100k entries (pure Python, no C extensions).
    """
    if not qvec:
        return []
    rows = db.execute(
        "SELECT id, embedding FROM entries WHERE status='active' "
        "AND embedding IS NOT NULL").fetchall()
    scored = []
    for r in rows:
        try:
            vec = json.loads(r["embedding"])
            sim = _cosine_sim(qvec, vec)
            scored.append((r["id"], sim))
        except (json.JSONDecodeError, TypeError):
            continue
    scored.sort(key=lambda x: -x[1])
    return scored[:budget]


def connect():
    """Open the hybrid store. Pure SQLite — no C extensions needed.

    Dense vectors are stored as JSON blobs in a regular BLOB column.
    Cosine similarity is computed in Python (brute-force, fast for <100k entries).
    FTS5 handles BM25 lexical search (built into SQLite).
    """
    os.makedirs(STORE_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
            text, content='', tokenize='porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            importance REAL DEFAULT 0.5,
            created_at TEXT NOT NULL,
            last_accessed TEXT,
            topic TEXT DEFAULT '',
            entities TEXT DEFAULT '[]',
            source TEXT DEFAULT '',
            method TEXT DEFAULT 'curator',
            status TEXT DEFAULT 'active',
            valid_from TEXT,
            valid_until TEXT,
            embedding BLOB
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text, content='', tokenize='porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            source_path TEXT,
            start_line INTEGER,
            end_line INTEGER,
            page INTEGER,
            text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            importance REAL DEFAULT 0.5,
            ingested_at TEXT NOT NULL,
            embedding BLOB
        );
        CREATE TABLE IF NOT EXISTS documents (
            path TEXT PRIMARY KEY,
            mtime REAL,
            content_hash TEXT,
            status TEXT DEFAULT 'pending',
            error TEXT DEFAULT '',
            wing TEXT DEFAULT '',
            ingested_at TEXT
        );
        CREATE TABLE IF NOT EXISTS working (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prev_hash TEXT NOT NULL,
            hash TEXT NOT NULL,
            claim TEXT,
            verdict TEXT,
            evidence TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS associations (
            src_id INTEGER NOT NULL,
            dst_id INTEGER NOT NULL,
            strength REAL DEFAULT 0.1,
            updated_at TEXT,
            PRIMARY KEY (src_id, dst_id)
        );
        CREATE TABLE IF NOT EXISTS scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            summary TEXT DEFAULT '',
            member_ids TEXT DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    # Schema migration: add new columns to EXISTING tables (so the upgrades are
    # retroactive — the live store and any pre-existing DB get the new fields
    # without a destructive rebuild).
    cols = {r[1] for r in db.execute("PRAGMA table_info(entries)")}
    for col, ddl in (("confidence", "REAL DEFAULT 0.7"),
                     ("temperature", "REAL DEFAULT 1.0"),
                     ("always_on", "INTEGER DEFAULT 0"),
                     ("priority", "INTEGER DEFAULT 5"),
                     ("triggers", "TEXT DEFAULT ''"),
                     ("kind", "TEXT DEFAULT 'fact'"),
                     ("slug", "TEXT DEFAULT ''"),
                     ("summary", "TEXT DEFAULT ''")):
        if col not in cols:
            db.execute(f"ALTER TABLE entries ADD COLUMN {col} {ddl}")
    # chunks.page — page number for book-sourced chunks (campaign-accuracy
    # verification: statements must be traceable to a written page).
    ccols = {r[1] for r in db.execute("PRAGMA table_info(chunks)")}
    if "page" not in ccols:
        db.execute("ALTER TABLE chunks ADD COLUMN page INTEGER")
    return db


def _embed(texts):
    """Batch-embed texts (256-dim via Matryoshka truncation). Returns
    {text: [float...]}."""
    if not texts:
        return {}
    # Content-hash cache: never re-embed unchanged text.
    cache = _embed.cache
    missing = [t for t in texts if t not in cache and t]
    if missing:
        payload = {"model": EMBED_MODEL,
                   "input": [f"search_document: {t}" for t in missing],
                   "options": {"num_ctx": 8192}}
        fd, path = _temp_json(payload)
        try:
            r = subprocess.run(
                ["curl", "-s", "-m", "60", "-X", "POST",
                 f"{OLLAMA_URL}/api/embed", "-H",
                 "Content-Type: application/json", "-d", f"@{path}"],
                capture_output=True, text=True, timeout=70)
            data = json.loads(r.stdout or "{}")
            vecs = data.get("embeddings") or []
            for t, v in zip(missing, vecs):
                if v:
                    cache[t] = v[:EMBED_DIM]
        except Exception:
            pass
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    return {t: cache[t] for t in texts if t in cache}


_embed.cache = {}


def _temp_json(obj):
    import tempfile
    fd, path = tempfile.mkstemp(prefix="memembed-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    return fd, path


def _entities_to_json(entities):
    return json.dumps(list(entities) if entities else [])


# Association fanout cap: each entry may link to at most this many others. This
# is the BLOAT GUARD — the precomputed graph stays small and indexed, so recall
# walks it in microtime. Bounded fanout + strong links only = fast, not slow.
ASSOC_FANOUT_CAP = 6
# A link is worth keeping only if it is a REAL relation, judged by the SAME
# meta-judgement as the recall gate:
#   (a) shared distinctive word (rare in the store) AND cosine >= 0.74, or
#   (b) cosine >= 0.80 (strong paraphrase — no shared word needed).
# Shared common words ("black", a person's first name) are NOT relations — the
# meta-judgement holds regardless of who runs the system ("black coffee" is not
# "black holes"). This is a precomputed accelerator, so the check happens once
# at write time, not per query.
ASSOC_MIN_COSINE = 0.74
ASSOC_STRONG_COSINE = 0.80


def _aaak_summary(text, topic="", source=""):
    """Compress `text` to an AAAK zettel (lightweight, no model calls)."""
    try:
        import aaak
        return aaak.compress(text, topic=topic, source_file=source)
    except Exception:
        return ""


def _auto_associate(db, eid, text, topic="", source=""):
    """Link a NEW entry to existing entries it shares a REAL relation with.

    Relation = shared distinctive word AND dense cosine >= floor (the same
    meta-judgement as recall). Bounded: at most ASSOC_FANOUT_CAP links per
    entry, strongest kept. Association is a PRECOMPUTED recall accelerator —
    one pass here, free graph walks at query time (no per-query embedding).
    """
    try:
        total = max(db.execute("SELECT COUNT(*) FROM entries "
                               "WHERE status='active'").fetchone()[0], 1)
        vec = _embed([text]).get(text)
        if not vec:
            return
        cur = db.execute(
            "SELECT id, text, topic FROM entries "
            "WHERE status='active' AND id != ?",
            (eid,)).fetchall()
        candidates = []
        for r in cur:
            ov = _embed([r["text"]]).get(r["text"])
            if not ov:
                continue
            dot = sum(a * b for a, b in zip(vec, ov))
            na = sum(a * a for a in vec) ** 0.5
            nb = sum(b * b for b in ov) ** 0.5
            cos = dot / (na * nb) if na and nb else 0.0
            # same meta-judgement as recall: distinctive shared word + floor,
            # OR very strong match (0.80 escape hatch for paraphrases)
            if cos >= ASSOC_STRONG_COSINE:
                candidates.append((cos, r["id"], 1))
                continue
            if cos < ASSOC_MIN_COSINE:
                continue
            # shared distinctive words (rare in the store) = real relation
            qw = {t for t in text.lower().split() if len(t) > 3}
            ow = {t for t in r["text"].lower().split() if len(t) > 3}
            shared = qw & ow
            if not shared:
                continue
            distinct = [w for w in shared if _df_word(db, w) / total < 0.05]
            if not distinct:
                continue
            candidates.append((cos, r["id"], len(distinct)))
        candidates.sort(key=lambda x: -x[0])
        for cos, other_id, n_distinct in candidates[:ASSOC_FANOUT_CAP]:
            boost = 0.35 + 0.1 * min(n_distinct, 4)
            associate(db, eid, other_id, boost=boost)
    except Exception:
        pass


def _df_word(db, word):
    try:
        return db.execute(
            "SELECT COUNT(*) AS n FROM entries WHERE status='active' "
            "AND text LIKE ?", (f"%{word}%",)).fetchone()["n"]
    except Exception:
        return 0


def _run_lexicon_reconcile():
    """Invoke lexicon-reconcile.py (associations + AAAK summaries) once on a
    lexicon version bump. Runs in a subprocess so a slow rebuild never blocks
    the calling command."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        subprocess.run(
            ["python3", os.path.join(here, "lexicon-reconcile.py"), "--all",
             "--fix"],
            capture_output=True, text=True, timeout=600)
    except Exception:
        pass


def add_entry(db, text, importance=0.5, topic="", entities=None,
              source="", method="curator", valid_until=None,
              confidence=None, temperature=None, always_on=0, priority=5):
    """Add an atomic memory entry with its embedding + FTS + metadata.

    #4 confidence-honesty: `confidence` (0-1) records how sure the evidence is
    (verified vs claimed) so merged facts never sound more certain than their
    source. #1 temperature: `temperature` (0-1) records how current the fact
    is — stale facts rank lower but are never deleted (lukewarm, not cold).
    `always_on` (0/1) + `priority` (0-10) mark the always-in-context core:
    constitution, safety, core identity, operating essentials. These are
    compiled by memory_compiler.py into the session's core context."""
    text = (text or "").strip()
    if not text or len(text) < 8:
        return False
    vec = _embed([text]).get(text)
    ts = now_iso()
    conf = 0.7 if confidence is None else float(confidence)
    temp = 1.0 if temperature is None else float(temperature)
    cur = db.execute(
        "INSERT INTO entries (text, importance, created_at, last_accessed, "
        "topic, entities, source, method, status, valid_from, valid_until, "
        "confidence, temperature, always_on, priority) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (text, float(importance), ts, ts, topic or "",
         _entities_to_json(entities), source or "", method or "curator",
         "active", ts, valid_until, conf, temp, int(always_on), int(priority)))
    eid = cur.lastrowid
    if vec:
        db.execute("UPDATE entries SET embedding=? WHERE id=?",
                   (json.dumps(vec), eid))
    db.execute("INSERT INTO entries_fts (rowid, text) VALUES (?,?)",
               (eid, text))
    db.execute("UPDATE entries SET summary=? WHERE id=?",
               (_aaak_summary(text, topic=topic, source=source), eid))
    db.commit()
    _auto_associate(db, eid, text, topic=topic, source=source)
    return True


def recall(db, query, budget=RECALL_BUDGET, min_score=RECALL_MIN_SCORE):
    """Hybrid recall: dense + BM25 fused by RRF, metadata-aware.

    Returns (entries, scores) — the top `budget` active entries. If nothing
    clears the score floor, returns [] (ABSTAIN — don't inject distractors).

    QUERY EXPANSION (research uplift, 2026-08-15): the query is expanded via
    the recall_uplift lexicon before retrieval, so a sparse query ("housing")
    also surfaces entries stored under related terms ("rent burden",
    "eviction", "affordability"). This is the classic query-expansion line
    (Voorhees; Rocchio) applied deterministically — free, no LLM, no latency.
    Expansion is best-effort: if it produces nothing new, retrieval is
    unchanged.
    """
    query = (query or "").strip()
    if not query:
        return []
    # --- Query expansion (research uplift #1). -----------------------------
    _orig_query = query
    try:
        _expand = __import__("recall_uplift", fromlist=["expand"]).expand
        _extra = _expand(query)
        if _extra and len(_extra) > 1:
            # Prefer the original query for the dense embedding (semantic
            # precision); use expanded terms ONLY as additional lexical probes
            # so we don't blunt the vector signal with synonyms.
            query = _orig_query
    except Exception:
        _extra = []
    # ------------------------------------------------------------------------
    qvec = _embed([f"search_query: {query}"]).get(f"search_query: {query}")
    # (1) Dense: brute-force cosine similarity over embedding BLOBs.
    # Pure Python, no C extensions. Fast for <100k entries.
    dense = []
    if qvec:
        raw_dense = _dense_search(db, qvec, RECALL_BUDGET * 2)
        # META-JUDGEMENT: vector proximity is not a relation by itself.
        # A dense hit must also share a CONTENT WORD with the query —
        # otherwise "black coffee" would pull in "black holes".
        q_content = {t for t in query.lower().split()
                     if len(t) > 3 and t not in _STOPWORDS}
        total_entries = max(db.execute(
            "SELECT COUNT(*) AS n FROM entries WHERE status='active'").fetchone()["n"], 1)
        def _df(word):
            try:
                return db.execute(
                    "SELECT COUNT(*) AS n FROM entries WHERE status='active' "
                    "AND text LIKE ?", (f"%{word}%",)).fetchone()["n"]
            except Exception:
                return 0
        for eid, cos in raw_dense:
            entry_text = db.execute(
                "SELECT text FROM entries WHERE id=?", (eid,)).fetchone()
            etxt = (entry_text["text"] or "").lower() if entry_text else ""
            distinctive = [w for w in q_content
                           if w in etxt and _df(w) / total_entries < 0.05]
            if distinctive:
                dense.append((eid, cos))
            elif not q_content or cos >= 0.80:
                dense.append((eid, cos))
    # (2) BM25 via FTS5.
    # CONJUNCTIVE matching (Salton, Fox & Wu, "Extended Boolean IR", CACM 1983;
    # SQLite FTS5 implicit AND): `MATCH "black coffee"` requires BOTH terms, so
    # a doc sharing only "black" ("black holes") is EXCLUDED outright. This is
    # the primary precision fix — a shared single word is not a relation.
    # Phrase clauses add dependence evidence (Metzler & Croft, SIGIR 2005).
    bm25 = []
    try:
        rows = db.execute(
            "SELECT rowid, bm25(entries_fts) AS score FROM entries_fts "
            "WHERE entries_fts MATCH ? "
            "ORDER BY bm25(entries_fts) LIMIT ?",
            (_fts_query_conjunctive(query), RECALL_BUDGET * 2)).fetchall()
        # Keep REAL BM25 scores (Robertson & Zaragoza 2009) for the QPP
        # threshold — never flatten them.
        bm25 = [(r["rowid"], float(r["score"])) for r in rows]
        # QUERY-EXPANSION OR-branch (uplift #1): a sparse query that matches
        # nothing conjunctively still has a chance via its expanded related
        # terms. Any expanded-term phrase that co-occurs is a genuine relation
        # candidate; we append it (not replace) so original-precision stays
        # first and expansion is strictly additive.
        if not bm25 and _extra:
            _expanded_hits = set()
            for _term in _extra[1:]:
                _match = _fts_query_conjunctive(_term)
                if not _match:
                    continue
                try:
                    _r = db.execute(
                        "SELECT rowid, bm25(entries_fts) AS score FROM entries_fts "
                        "WHERE entries_fts MATCH ? "
                        "ORDER BY bm25(entries_fts) LIMIT 4",
                        (_match,)).fetchall()
                    for _row in _r:
                        _expanded_hits.add(_row["rowid"])
                except Exception:
                    pass
            if _expanded_hits:
                _ids = list(_expanded_hits)[:RECALL_BUDGET * 2]
                _ph = ",".join("?" * len(_ids))
                _rows = db.execute(
                    f"SELECT rowid, bm25(entries_fts) AS score FROM entries_fts "
                    f"WHERE rowid IN ({_ph})", _ids).fetchall()
                bm25 = [(r["rowid"], float(r["score"])) for r in _rows]
    except Exception:
        pass
    # (3) Fusion. With BOTH rankers: RRF (Cormack et al. 2009). With ONLY
    # lexical (embeddings absent): RRF degenerates to a rank transform that
    # destroys the score signal needed for thresholding (Bruch et al.,
    # arXiv:2210.11934) — use raw BM25 scores directly.
    ranks = {}
    best_dense = 0.0
    if dense and bm25:
        for idx, lst in enumerate([dense, bm25]):
            for pos, (eid, _score) in enumerate(lst):
                ranks[eid] = ranks.get(eid, 0.0) + 1.0 / (RRF_K + pos + 1)
                if idx == 0:
                    best_dense = max(best_dense, _score)
    elif dense:
        for pos, (eid, _score) in enumerate(dense):
            ranks[eid] = 1.0 / (RRF_K + pos + 1)
            best_dense = max(best_dense, _score)
    elif bm25:
        # Lexical-only: keep the real BM25 score (it's negative; higher = better).
        for eid, score in bm25:
            ranks[eid] = float(score)
    # Fetch full entries + metadata for the fused set.
    scored = []
    if ranks:
        ids = list(ranks.keys())
        placeholders = ",".join("?" * len(ids))
        rows = db.execute(
            f"SELECT * FROM entries WHERE id IN ({placeholders}) "
            f"AND status='active'", ids).fetchall()
        by_id = {r["id"]: r for r in rows}
        for eid, base in sorted(ranks.items(), key=lambda x: -x[1]):
            row = by_id.get(eid)
            if not row:
                continue
            # #1 temperature ranking (not deletion): a stale fact ranks lower
            # but stays in the store. #4 confidence: a hedged/claimed fact
            # carries its honesty into ranking. Blend into a relevance score.
            # RECENCY: a fact last accessed recently ranks slightly higher than
            # an untouched one at equal relevance (the store's last_accessed
            # field). Decays over RECENCY_HALF_LIFE days — a gentle nudge, not
            # a rewrite of the relevance ranking.
            conf = float(row["confidence"] or 0.7)
            temp = float(row["temperature"] or 1.0)
            recency = 1.0
            try:
                from datetime import datetime, timezone
                la = row["last_accessed"]
                if la:
                    t_last = datetime.fromisoformat(la.replace("Z", "+00:00"))
                    days = (datetime.now(timezone.utc) - t_last).total_seconds() / 86400.0
                    if days >= 0:
                        recency = RECENCY_HALF_LIFE / (RECENCY_HALF_LIFE + days)
            except Exception:
                pass
            # In lexical-only mode `base` is a raw BM25 score (negative).
            # Normalize it to a positive relevance for the metadata, keeping
            # the ranking identical. In hybrid mode `base` is an RRF rank.
            if dense and bm25:
                relevance = base * (0.4 + 0.6 * conf) * (0.5 + 0.5 * temp) * (0.85 + 0.15 * recency)
            elif dense:
                relevance = base * (0.4 + 0.6 * conf) * (0.5 + 0.5 * temp) * (0.85 + 0.15 * recency)
            else:
                relevance = (base + 5.0) * (0.4 + 0.6 * conf) * (0.5 + 0.5 * temp) * (0.85 + 0.15 * recency)
            scored.append({
                "id": eid, "text": row["text"],
                "importance": row["importance"],
                "topic": row["topic"], "entities": row["entities"],
                "source": row["source"],
                "last_accessed": row["last_accessed"],
                "rrf": round(base, 4),
                "confidence": round(conf, 2),
                "temperature": round(temp, 2),
                "relevance": round(relevance, 4),
            })
    # Enforce budget + abstention floor.
    if not scored:
        return []
    top = scored[:budget]
    # ABSTENTION via query-difficulty estimation (QPP — Cronen-Townsend et al.,
    # SIGIR 2002; Zhou & Croft, CIKM 2006; LongMemEval arXiv:2410.10813 makes
    # abstention a first-class memory ability). The principled signal is the
    # RANKING GAP, not an absolute score:
    #  - lexical-only: the conjunction already filters to co-occurring terms;
    #    a single weak hit (no runner-up) abstains (no confident winner).
    #  - dense-only: abstain when the top-1 dense match is not clearly above
    #    the runner-up (an ambiguous, no-confident-winner query) OR the top is
    #    below an absolute "probably unrelated" floor. The floor is a coarse
    #    guard; the gap is the real difficulty signal.
    if not dense and not bm25:
        return []
    if not dense:  # lexical-only
        if len(scored) < 1:
            return []
        if len(scored) == 1:
            return top  # single conjunctive match = confident enough
        # multiple: keep the top; a tight pack = weak, abstain
        gap = top[0]["rrf"] - top[1]["rrf"]
        return top if gap >= 0.5 else []
    # dense present. Score-based abstention (QPP, Shtok et al. TOIS 2012).
    # The absolute floor is CALIBRATED against real cases (nomic band ~0.6-0.75):
    #   "what does the user drink" vs "black coffee" = 0.766 -> relevant
    #   "quantum physics of black holes" vs "coconut milk" = 0.712 -> noise
    # The separation sits at ~0.74. Below it, only a clear winner (large gap to
    # the runner-up) may pass; a weak top with no clear winner abstains.
    if not bm25:
        if best_dense < min_score:
            return []  # below the "probably unrelated" floor
        if best_dense >= 0.74:
            return _position_aware(top)  # calibrated: genuine semantic match
        if len(dense) >= 2:
            d_scores = sorted(s for _, s in dense)
            top1 = d_scores[-1]
            top2 = d_scores[-2]
            if top1 - top2 >= 0.08:
                return _position_aware(top)  # clear winner despite mid-band score
            return []  # weak + ambiguous -> abstain
        return _position_aware(top)
    return _position_aware(top)


def _position_aware(top):
    """POSITION-AWARE RE-RANK (research uplift #2): lost-in-the-middle
    mitigation (Liu et al. 2023; BriefContext, npj Digital Medicine 2025).
    Models use the START and END of context best and the middle worst, so the
    two highest-relevance items are placed FIRST and LAST in the returned
    window. Returns the SAME items — only their order changes, never which
    items are returned. Degrades gracefully (no-op for <3 items)."""
    try:
        if not top or len(top) < 3:
            return top
        s = list(top)
        s_sorted = sorted(s, key=lambda x: float(x.get("relevance", 0)),
                          reverse=True)
        return [s_sorted[0]] + s_sorted[2:] + [s_sorted[1]]
    except Exception:
        return top


_STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "into",
              "when", "then", "were", "have", "been", "will", "was", "are",
              "but", "not", "you", "your", "also", "its", "his", "her", "him",
              "over", "under", "them", "they", "there", "about", "after",
              "what", "which", "who", "how", "why", "where", "while", "just",
              "more", "each", "than", "then", "very", "such", "some", "only",
              "prefers", "needs", "wants", "likes"}


def _fts_query_conjunctive(query):
    """Build a CONJUNCTIVE FTS5 MATCH query (implicit AND).

    Extended Boolean IR (Salton, Fox & Wu, CACM 1983): a document must contain
    ALL significant query terms to match — a shared single word is not a
    relation ("black coffee" excludes "black holes"). Phrase clauses are added
    as additive dependence evidence (Metzler & Croft, SIGIR 2005). Short/stop
    words are dropped so the conjunction stays meaningful.
    """
    toks = [t for t in query.lower().split()
            if len(t) > 3 and t not in _STOPWORDS]
    if not toks:
        toks = [t for t in query.lower().split() if len(t) > 2]
    if not toks:
        return query
    # Join with implicit AND (space). All terms must co-occur.
    return " ".join(f'"{t}"' for t in toks[:8])


def _fts_query(query):
    """Backward-compat alias for the conjunctive builder."""
    return _fts_query_conjunctive(query)


def update_entry(db, eid, text=None, importance=None, valid_until=None):
    sets, params = [], []
    if text is not None:
        sets.append("text=?")
        params.append(text)
        # re-embed + refresh FTS
        vec = _embed([text]).get(text)
        if vec:
            db.execute("UPDATE entries SET embedding=? WHERE id=?",
                       (json.dumps(vec), eid))
        _fts_delete(db, eid)
        db.execute("INSERT INTO entries_fts (rowid, text) VALUES (?,?)",
                   (eid, text))
    if importance is not None:
        sets.append("importance=?")
        params.append(float(importance))
    if valid_until is not None:
        sets.append("valid_until=?")
        params.append(valid_until)
    if not sets:
        return False
    sets.append("last_accessed=?")
    params.append(now_iso())
    params.append(eid)
    db.execute(f"UPDATE entries SET {', '.join(sets)} WHERE id=?", params)
    db.commit()
    return True


def _fts_delete(db, eid):
    """Remove a row from the CONTENTLESS FTS5 index.

    A contentless FTS5 table cannot use `DELETE FROM` — SQLite raises
    OperationalError. The only supported removal is the special 'delete'
    command, which requires the row's stored text (contentless tables hold no
    text, so we read it from the entries table first)."""
    try:
        old = db.execute("SELECT text FROM entries WHERE id=?",
                         (eid,)).fetchone()
        if old and old["text"]:
            db.execute("INSERT INTO entries_fts(entries_fts, rowid, text) "
                       "VALUES('delete', ?, ?)", (eid, old["text"]))
    except Exception:
        pass


def delete_entry(db, eid):
    db.execute("DELETE FROM entries WHERE id=?", (eid,))
    _fts_delete(db, eid)
    db.commit()
    return True


def touch(db, eid):
    db.execute("UPDATE entries SET last_accessed=? WHERE id=?",
               (now_iso(), eid))
    db.commit()


# ------------------------------------------------------------------ working ----

def working_set(db, task_id, text):
    db.execute("DELETE FROM working WHERE task_id=?", (task_id,))
    cur = db.execute("INSERT INTO working (task_id, text, created_at) VALUES (?,?,?)",
                     (task_id, text, now_iso()))
    db.commit()
    return cur.lastrowid


def working_get(db, task_id):
    rows = db.execute("SELECT text FROM working WHERE task_id=? ORDER BY id "
                      "DESC LIMIT 5", (task_id,)).fetchall()
    return [r["text"] for r in rows]


def working_clear(db, task_id):
    db.execute("DELETE FROM working WHERE task_id=?", (task_id,))
    db.commit()


# ------------------------------------------------------------------- chunks ----

def _chunk_text(text, size=1500, overlap=150):
    """Recursive-ish split on paragraph/heading boundaries, ~1500 chars (the
    local CPU-friendly size; research: fixed chunking beats semantic for
    free systems). Headings stay with their content."""
    text = text or ""
    if len(text) <= size:
        return [text] if text.strip() else []
    # Split on blank lines first (paragraph boundaries), then recombine.
    paras = [p for p in text.split("\n\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 2 <= size:
            cur = f"{cur}\n\n{p}" if cur else p
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    # If any chunk is still huge (no paragraph breaks), hard-split it.
    out = []
    for c in chunks:
        if len(c) > size * 1.5:
            for i in range(0, len(c), size):
                piece = c[i:i + size]
                if piece.strip():
                    out.append(piece)
        else:
            out.append(c)
    return out


def add_chunk(db, text, wing="", room="", source_path="", doc_id="",
              start_line=None, end_line=None, importance=0.5, page=None):
    """Store one document chunk with provenance + embedding + FTS.
    Idempotent: deterministic chunk_id means re-ingest upserts, never dupes.
    `page` records the source page for book-sourced chunks (campaign-accuracy
    verification)."""
    text = (text or "").strip()
    if not text or len(text) < 20:
        return False
    ch = hashlib.sha256(text.encode()).hexdigest()[:16]
    chunk_id = f"{doc_id or source_path or 'chunk'}::{ch}"
    vec = _embed([text]).get(text)
    ts = now_iso()
    # Contentless FTS5 cannot use DELETE FROM — capture the old row first so a
    # re-ingest (same chunk_id) can be removed via the special 'delete' command.
    old = db.execute("SELECT rowid, text FROM chunks WHERE chunk_id=?",
                     (chunk_id,)).fetchone()
    db.execute(
        "INSERT INTO chunks (chunk_id, doc_id, wing, room, source_path, "
        "start_line, end_line, page, text, content_hash, importance, "
        "ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(chunk_id) DO UPDATE SET text=excluded.text, "
        "importance=excluded.importance, ingested_at=excluded.ingested_at",
        (chunk_id, doc_id or source_path or "chunk", wing, room, source_path,
         start_line, end_line, page, text, ch, float(importance), ts))
    # sqlite-vec rowid = sqlite rowid of the chunks table row.
    rid = db.execute("SELECT rowid FROM chunks WHERE chunk_id=?",
                     (chunk_id,)).fetchone()["rowid"]
    if old is not None:
        db.execute("INSERT INTO chunks_fts(chunks_fts, rowid, text) "
                   "VALUES ('delete', ?, ?)", (old["rowid"], old["text"]))
    if vec:
        db.execute("UPDATE chunks SET embedding=? WHERE chunk_id=?",
                   (json.dumps(vec), chunk_id))
    db.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?,?)",
               (rid, text))
    db.commit()
    return True


def chunk_recall(db, query, budget=6, wing=None, min_sim=0.0, doc=None):
    """Retrieve document chunks (cold tier) by hybrid similarity + optional
    wing + doc filters. `doc` restricts to one source document (e.g.
    'impossible-landscapes.txt') so recall never mixes books. Returns chunk
    text + provenance + best dense similarity.
    `min_sim` (0-1) sets a floor on the best dense cosine. Default 0.0: cold
    tier is broad recall — the precision filters (coherence gate with 0.78,
    worthiness) apply at the point of use, not here. An absolute floor on a
    general corpus is unreliable because nomic compresses prose into a
    ~0.78-0.82 band regardless of topic."""
    query = (query or "").strip()
    if not query:
        return []
    qvec = _embed([f"search_query: {query}"]).get(f"search_query: {query}")
    dense = []
    best_dense = 0.0
    if qvec:
        rows = db.execute(
            "SELECT rowid, embedding FROM chunks "
            "WHERE embedding IS NOT NULL").fetchall()
        for r in rows:
            try:
                vec = json.loads(r["embedding"])
                cos = _cosine_sim(qvec, vec)
                dense.append((r["rowid"], cos))
                best_dense = max(best_dense, cos)
            except (json.JSONDecodeError, TypeError):
                continue
        dense.sort(key=lambda x: -x[1])
        dense = dense[:budget * 3]
    if best_dense < min_sim:
        return []  # below the grounding floor -> abstain
    bm25 = []
    try:
        rows = db.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
            "ORDER BY bm25(chunks_fts) LIMIT ?",
            (_fts_query(query), budget * 3)).fetchall()
        bm25 = [(r["rowid"], 0.5) for r in rows]
    except Exception:
        pass
    ranks = {}
    for lst in [dense, bm25]:
        for pos, (eid, _s) in enumerate(lst):
            ranks[eid] = ranks.get(eid, 0.0) + 1.0 / (RRF_K + pos + 1)
    if not ranks:
        return []
    ids = list(ranks.keys())
    ph = ",".join("?" * len(ids))
    sql = f"SELECT rowid, * FROM chunks WHERE rowid IN ({ph})"
    params = ids
    if wing:
        sql += " AND wing=?"
        params.append(wing)
    if doc:
        sql += " AND doc_id=?"
        params.append(doc)
    rows = db.execute(sql, params).fetchall()
    by_id = {r["rowid"]: r for r in rows}
    out = []
    for eid, rrf in sorted(ranks.items(), key=lambda x: -x[1]):
        r = by_id.get(eid)
        if not r:
            continue
        out.append({
            "chunk_id": r["chunk_id"], "text": r["text"][:400],
            "wing": r["wing"], "room": r["room"],
            "source": r["source_path"],
            "page": r["page"] if "page" in r.keys() else None,
            "rrf": round(rrf, 4),
        })
        if len(out) >= budget:
            break
    return out


# ------------------------------------------------------------------- mining ----

def _extract_epub(path):
    """Extract text from an EPUB (a zip of XHTML documents).

    The legacy `ebook2text` rejects many EPUBs; this unzips directly, reads the
    spine documents, and strips HTML tags — robust for the standard format.
    Falls back to `ebook2text` if unzip is unavailable."""
    try:
        import zipfile
        import re as _re
        with zipfile.ZipFile(path) as z:
            # content.opf declares the spine (reading order)
            opf = next((n for n in z.namelist()
                        if n.endswith("content.opf") or n.endswith("package.opf")), None)
            if not opf:
                return ""
            manifest_order = []
            try:
                opf_text = z.read(opf).decode("utf-8", "replace")
                spine_ids = _re.findall(r'idref="([^"]+)"', opf_text)
                id_map = dict(_re.findall(r'id="([^"]+)"\s+href="([^"]+)"', opf_text))
                manifest_order = [id_map[i] for i in spine_ids if i in id_map]
            except Exception:
                manifest_order = []
            if not manifest_order:
                manifest_order = [n for n in z.namelist()
                                  if n.endswith((".xhtml", ".html", ".htm"))
                                  and not n.startswith("OEBPS/Images")]
            parts = []
            names = set(z.namelist())
            opf_dir = opf.rsplit("/", 1)[0] if "/" in opf else ""
            for n in manifest_order:
                # resolve relative hrefs against the OPF directory
                full = f"{opf_dir}/{n}" if opf_dir and not n.startswith("/") else n
                full = full.replace("//", "/").lstrip("/")
                candidates = [full]
                if full not in names:
                    candidates = [f"OEBPS/{full}", n]
                got = False
                for cand in candidates:
                    if cand not in names:
                        continue
                    try:
                        raw = z.read(cand).decode("utf-8", "replace")
                        got = True
                    except KeyError:
                        continue
                    raw = _re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw,
                                  flags=_re.S | _re.I)
                    txt = _re.sub(r"<[^>]+>", " ", raw)
                    txt = _re.sub(r"\s+", " ", txt).strip()
                    if txt:
                        parts.append(txt)
                    break
                if not got:
                    continue
            return "\n\n".join(parts)
    except Exception:
        r = subprocess.run(["ebook2text", path], capture_output=True,
                           text=True, timeout=60)
        return r.stdout


def mine_file(db, path, wing="", room="", chunk_size=1500):
    """Extract text from a file (md/txt/pdf/epub/html), chunk, and store with
    provenance. Returns (chunks_added, error)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".md", ".txt", ".markdown", ".py", ".sh", ".ts", ".js",
                   ".json", ".yaml", ".yml", ".toml"):
            text = open(path, encoding="utf-8", errors="replace").read()
        elif ext == ".pdf":
            # UPGRADED EXTRACTION: PyMuPDF column-aware order + printed page
            # numbers (pdf_extract.py), NOT the legacy pdftotext that
            # interleaved two-column book pages. Page text is joined in
            # reading order so books mine correctly.
            try:
                import subprocess
                r = subprocess.run(
                    [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "pdf_extract.py"), path],
                    capture_output=True, text=True, timeout=300)
                pages = json.loads(r.stdout)
                text = "\n\n".join(p["text"] for p in pages if p.get("text"))
            except Exception:
                # fallback: legacy extractor if the upgraded one is unavailable
                r = subprocess.run(["pdftotext", "-layout", path, "-"],
                                   capture_output=True, text=True, timeout=60)
                text = r.stdout
        elif ext in (".epub",):
            # EPUB = zip of XHTML. Robust extraction: unzip, read spine
            # documents, strip tags (handles EPUBs the legacy `ebook2text`
            # rejected). Falls back to ebook2text if unzip is unavailable.
            text = _extract_epub(path)
        elif ext in (".html", ".htm"):
            r = subprocess.run(["python3", "-c",
                                "import sys,trafilatura;"
                                "print(trafilatura.extract(sys.stdin.read()))"],
                               input=open(path, encoding="utf-8", errors="replace").read(),
                               capture_output=True, text=True, timeout=60)
            text = r.stdout
        else:
            return 0, f"unsupported ext {ext}"
    except Exception as e:
        return 0, str(e)
    if not text or not text.strip():
        return 0, "empty after parse"
    chunks = _chunk_text(text, chunk_size)
    added = 0
    for i, c in enumerate(chunks):
        # line provenance (approximate: count newlines before chunk offset)
        offset = 0
        for j in range(i):
            offset += chunks[j].count("\n") + 1
        start = text[:offset].count("\n") + 1
        end = start + c.count("\n")
        if add_chunk(db, c, wing=wing, room=room, source_path=path,
                     doc_id=os.path.basename(path), start_line=start,
                     end_line=end):
            added += 1
    return added, ""


def mine_directory(db, directory, wing="", room="", max_files=0):
    """Incrementally mine a directory: only new/changed files (content-hash),
    dead-letter on corrupt files. Returns summary."""
    SKIP_DIRS = {".venv", "venv", "node_modules", ".git", "__pycache__",
                 "site-packages", "dist", "build", ".mypy_cache", ".pytest_cache"}
    results = {"files": 0, "chunks": 0, "skipped": 0, "errors": []}
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if "/node_modules/" in root + "/" or root.endswith("/node_modules"):
            continue
        for fn in sorted(files):
            if not fn.lower().endswith((".md", ".txt", ".markdown", ".pdf",
                                        ".epub", ".html", ".htm",
                                        ".py", ".sh", ".ts", ".js", ".json",
                                        ".yaml", ".yml", ".toml")):
                continue
            path = os.path.join(root, fn)
            if max_files and results["files"] >= max_files:
                return results
            try:
                mtime = os.path.getmtime(path)
                ch = hashlib.sha256(
                    open(path, "rb").read(1 << 20)).hexdigest()[:16]
            except OSError:
                continue
            reg = db.execute("SELECT mtime, content_hash, status FROM documents "
                             "WHERE path=?", (path,)).fetchone()
            if reg and reg["status"] == "ok" and reg["content_hash"] == ch:
                results["skipped"] += 1
                continue
            added, err = mine_file(db, path, wing=wing, room=room)
            results["files"] += 1
            results["chunks"] += added
            if err:
                # Empty files (e.g. __init__.py stubs) are not errors — nothing
                # to mine. Mark them ok so they're skipped on future runs.
                if "empty" in err:
                    db.execute("INSERT OR REPLACE INTO documents (path, mtime, "
                               "content_hash, status, error, wing, ingested_at) "
                               "VALUES (?,?,?,?,?,?,?)",
                               (path, mtime, ch, "ok", "", wing, now_iso()))
                else:
                    results["errors"].append(f"{path}: {err}")
                    db.execute("INSERT OR REPLACE INTO documents (path, mtime, "
                               "content_hash, status, error, wing, ingested_at) "
                               "VALUES (?,?,?,?,?,?,?)",
                               (path, mtime, ch, "error", err, wing, now_iso()))
            else:
                db.execute("INSERT OR REPLACE INTO documents (path, mtime, "
                           "content_hash, status, error, wing, ingested_at) "
                           "VALUES (?,?,?,?,?,?,?)",
                           (path, mtime, ch, "ok", "", wing, now_iso()))
            db.commit()
    return results


# ------------------------------------------------------------------ wake-up ----

def wake_up(db, budget_chars=800):
    """Session-start context (L0 core + L1 top memories). No LLM calls.

    PARITY: L1 carries the FULL text of the top entries (grouped by topic,
    ~800 chars total) — wake-up is context the agent reads directly, so it
    must carry substance, not compressed pointers. The token-light AAAK
    compression belongs in the warm-firing tier (where the budget is tight
    and the top neuron already fires at full fidelity), not here."""
    parts = []
    used = 0
    rows = db.execute(
        "SELECT text, topic, importance, source FROM entries "
        "WHERE status='active' ORDER BY importance DESC LIMIT 10").fetchall()
    for r in rows:
        line = f"- {r['text']}"
        if used + len(line) > budget_chars:
            break
        parts.append(line)
        used += len(line)
    if parts:
        return ("# What matters (wake-up context)\n\n" + "\n".join(parts))
    return ""


# ---------------------------------------------------------------- audit chain ---

def audit_append(db, claim, verdict, evidence=""):
    """Append to the hash-chained audit ledger (provenance as control)."""
    last = db.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev = last["hash"] if last else "GENESIS"
    payload = f"{prev}|{claim}|{verdict}|{evidence}"
    h = hashlib.sha256(payload.encode()).hexdigest()[:24]
    db.execute("INSERT INTO audit_log (prev_hash, hash, claim, verdict, "
               "evidence, created_at) VALUES (?,?,?,?,?,?)",
               (prev, h, claim, verdict, evidence, now_iso()))
    db.commit()
    return h


def audit_verify(db):
    """Verify the chain integrity: each hash derives from its predecessor AND
    matches a recomputation of its own content (so tampering with a stored
    hash is detected even at the tail of the chain)."""
    rows = db.execute("SELECT id, prev_hash, hash, claim, verdict, evidence "
                      "FROM audit_log ORDER BY id").fetchall()
    prev = "GENESIS"
    for r in rows:
        if r["prev_hash"] != prev:
            return (False, f"chain break at id {r['id']}")
        recomputed = hashlib.sha256(
            f"{prev}|{r['claim']}|{r['verdict']}|{r['evidence']}".encode()
        ).hexdigest()[:24]
        if recomputed != r["hash"]:
            return (False, f"hash mismatch at id {r['id']} (tampered)")
        prev = r["hash"]
    return (True, f"chain intact ({len(rows)} entries)")


def stats(db):
    e = db.execute("SELECT COUNT(*) FROM entries WHERE status='active'").fetchone()[0]
    w = db.execute("SELECT COUNT(*) FROM working").fetchone()[0]
    a = db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    by_topic = {r["topic"]: r["n"] for r in db.execute(
        "SELECT topic, COUNT(*) AS n FROM entries WHERE status='active' "
        "GROUP BY topic ORDER BY n DESC LIMIT 6") if r["topic"]}
    return {"active_entries": e, "working": w, "audit": a, "top_topics": by_topic}


# ------------------------------------------------------------------ associations ----

def associate(db, eid_a, eid_b, boost=0.1):
    """Hebbian association: when two entries co-occur (same session/topic),
    strengthen their link. Repeated co-occurrence strengthens recall — the
    'association layer' over the store (HeLa-Mem pattern)."""
    if eid_a == eid_b:
        return
    ts = now_iso()
    db.execute(
        "INSERT INTO associations (src_id, dst_id, strength, updated_at) "
        "VALUES (?,?,?,?) "
        "ON CONFLICT(src_id, dst_id) DO UPDATE SET "
        "strength=MIN(1.0, strength+?), updated_at=?",
        (eid_a, eid_b, boost, ts, boost, ts))
    db.execute(
        "INSERT INTO associations (src_id, dst_id, strength, updated_at) "
        "VALUES (?,?,?,?) "
        "ON CONFLICT(src_id, dst_id) DO UPDATE SET "
        "strength=MIN(1.0, strength+?), updated_at=?",
        (eid_b, eid_a, boost, ts, boost, ts))
    db.commit()


def linked_entries(db, eid, limit=5):
    """Return entries strongly associated with `eid` (for recall enrichment)."""
    rows = db.execute(
        "SELECT e.id, e.text, e.summary, a.strength FROM associations a "
        "JOIN entries e ON e.id=a.dst_id "
        "WHERE a.src_id=? AND e.status='active' "
        "ORDER BY a.strength DESC LIMIT ?", (eid, limit)).fetchall()
    return [{"id": r["id"], "text": r["text"], "summary": r["summary"] or "",
             "strength": r["strength"]} for r in rows]


def recall_with_associations(db, query, budget=RECALL_BUDGET,
                             min_score=RECALL_MIN_SCORE, hops=1,
                             assoc_limit=4):
    """Hybrid recall ACCELERATED by the precomputed association graph.

    The base `recall()` applies the precision gate (dense+BM25, QPP abstention).
    When it clears, this walks the association graph from each confident seed —
    a single indexed SQL join (microseconds, ZERO extra embeddings) — and merges
    the linked entries into the result. The relation strength boosts relevance,
    so a fact the query implies via association surfaces even without an exact
    term match. `hops=1` keeps the walk bounded (no graph explosion).
    """
    base = recall(db, query, budget=budget, min_score=min_score)
    if not base:
        return base
    if hops < 1:
        return base
    by_id = {r["id"]: r for r in base}
    boost_map = {}
    for seed in base:
        for linked in linked_entries(db, seed["id"], limit=assoc_limit):
            if linked["id"] in by_id:
                continue
            # A link is worth following only if it is genuinely strong — weak
            # links would reintroduce the noise the gate removed. Auto-assoc
            # floors at ~0.35 (first link), so follow >= 0.3.
            if linked["strength"] < 0.3:
                continue
            boost_map[linked["id"]] = max(
                boost_map.get(linked["id"], 0.0),
                linked["strength"])
    if not boost_map:
        return base
    ids = list(boost_map.keys())
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT * FROM entries WHERE id IN ({placeholders}) "
        f"AND status='active'", ids).fetchall()
    by_id2 = {r["id"]: r for r in rows}
    for eid, strength in sorted(boost_map.items(), key=lambda x: -x[1]):
        row = by_id2.get(eid)
        if not row:
            continue
        conf = float(row["confidence"] or 0.7)
        temp = float(row["temperature"] or 1.0)
        base_relevance = max(r["relevance"] for r in base) if base else 0.0
        relevance = base_relevance * strength * (0.4 + 0.6 * conf) * (0.5 + 0.5 * temp)
        base.append({
            "id": eid, "text": row["text"],
            "importance": row["importance"],
            "topic": row["topic"], "entities": row["entities"],
            "source": row["source"],
            "last_accessed": row["last_accessed"],
            "rrf": round(strength, 4),
            "confidence": round(conf, 2),
            "temperature": round(temp, 2),
            "relevance": round(relevance, 4),
            "via_association": True,
        })
    base.sort(key=lambda r: -r["relevance"])
    return base[:budget]


# -------------------------------------------------------- lifecycle (scenes) ----

def make_scene(db, name, member_ids, summary=""):
    """Consolidate related entries into a themed scene (EverMemOS-style):
    episodic traces -> semantic scene. Members keep their own identity; the
    scene is a navigable grouping for hierarchical recall."""
    ts = now_iso()
    cur = db.execute("INSERT INTO scenes (name, summary, member_ids, created_at) "
                     "VALUES (?,?,?,?)",
                     (name, summary, json.dumps(list(member_ids)), ts))
    db.commit()
    return cur.lastrowid


def scene_entries(db, scene_id):
    """Return the entries grouped under a scene."""
    row = db.execute("SELECT member_ids FROM scenes WHERE id=?",
                     (scene_id,)).fetchone()
    if not row:
        return []
    ids = json.loads(row["member_ids"] or "[]")
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    rows = db.execute(f"SELECT id, text, topic FROM entries WHERE id IN ({ph})",
                      ids).fetchall()
    return [{"id": r["id"], "text": r["text"][:80], "topic": r["topic"]}
            for r in rows]


def list_scenes(db):
    rows = db.execute("SELECT id, name, summary, member_ids FROM scenes "
                      "ORDER BY id").fetchall()
    out = []
    for r in rows:
        members = json.loads(r["member_ids"] or "[]")
        out.append({"id": r["id"], "name": r["name"],
                    "summary": r["summary"], "members": len(members)})
    return out


# --------------------------------------------- hierarchy (HORMA-style routing) --

def hierarchy(db, top=3):
    """A navigation view: domains (topics) -> scenes -> entries. The
    'smallest sufficient' retrieval frontier — route by domain, then scene,
    then entry, instead of dumping everything."""
    domains = {}
    for r in db.execute("SELECT DISTINCT topic FROM entries WHERE status='active' "
                        "AND topic != ''"):
        topic = r[0]
        n = db.execute("SELECT COUNT(*) FROM entries WHERE topic=? AND "
                       "status='active'", (topic,)).fetchone()[0]
        domains[topic] = n
    top_domains = sorted(domains.items(), key=lambda x: -x[1])[:top]
    return [{"domain": d, "entries": n,
             "scenes": [s for s in list_scenes(db)
                        if d.lower() in s["name"].lower() or d.lower() in s["summary"].lower()]}
            for d, n in top_domains]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["add", "recall", "update", "delete",
                                    "working", "audit", "verify", "stats",
                                    "mine", "chunk-recall", "wake", "export",
                                    "associate", "linked", "scene", "hierarchy",
                                    "query-core"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--importance", type=float, default=0.5)
    ap.add_argument("--topic", default="")
    ap.add_argument("--confidence", type=float, default=None)
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--text", default=None)
    ap.add_argument("--source", default="")
    ap.add_argument("--method", default="curator")
    ap.add_argument("--wing", default="")
    ap.add_argument("--doc", default="", help="filter chunk-recall to one source document (doc_id)")
    ap.add_argument("--min-sim", type=float, default=0.0)
    ap.add_argument("--room", default="")
    ap.add_argument("--no-dedupe", action="store_true",
                    help="bypass the dedupe policy gate (raw insert, receipted)")
    ap.add_argument("--version", action="store_true",
                    help="also create a git version snapshot of the memory state")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    db = connect()
    # RETROACTIVE LEXICON MIGRATION: when the lexical semantics change
    # (LEXICON_VERSION bump), derived aspects (associations, AAAK summaries)
    # must catch up so nothing built on the old loose matching stays stale.
    # Runs once per version via the meta table — not per-command overhead.
    try:
        row = db.execute("SELECT value FROM meta WHERE key='lexicon_version'").fetchone()
        cur_ver = int(row["value"]) if row else 0
    except Exception:
        cur_ver = 0
    if cur_ver < LEXICON_VERSION:
        _run_lexicon_reconcile()
        db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES "
                   "('lexicon_version', ?)", (str(LEXICON_VERSION),))
        db.commit()

    if args.cmd == "add":
        text = " ".join(args.arg)
        # Dedupe policy gate: one record per data point; similar content only
        # supersedes when new info replaces old; every decision is receipted.
        # --no-dedupe bypasses the gate (explicit override, still receipted).
        if args.no_dedupe:
            ok = add_entry(db, text, args.importance, args.topic, source=args.source,
                           method=args.method, confidence=args.confidence,
                           temperature=args.temperature)
            print(json.dumps({"added": ok, "dedupe": "bypassed"}))
        else:
            try:
                from dedupe_policy import apply as dedupe_apply
                res = dedupe_apply(db, text, args.topic, args.importance,
                                   args.source, args.confidence)
                print(json.dumps({"added": True, "decision": res["decision"],
                                  "id": res.get("id"),
                                  "prior_id": res.get("prior_id"),
                                  "reason": res.get("receipt", {}).get("reason", "")}))
                ok = True
            except Exception as e:
                print(json.dumps({"added": False, "error": str(e)[:120]}))
                ok = False
        if ok and args.version:
            try:
                from importlib import util as _u
                _sp = _u.spec_from_file_location("version_mod",
                    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "version.py"))
                _vm = _u.module_from_spec(_sp); _sp.loader.exec_module(_vm)
                _vm.snapshot(reason="memory-write", summary=text[:80])
            except Exception:
                pass
        sys.exit(0 if ok else 1)
    if args.cmd == "recall":
        q = " ".join(args.arg)
        res = recall_with_associations(db, q)
        print(json.dumps(res, indent=2) if args.json else
              json.dumps(res, indent=2))
        return
    if args.cmd == "update":
        eid = int(args.arg[0]) if args.arg else 0
        # HARD AUTHORITY BOUNDARY: never update a constitution entry via the
        # general path. The constitution's only writer is constitution-add.py.
        row = db.execute("SELECT topic FROM entries WHERE id=?", (eid,)).fetchone()
        if row and (row["topic"] or "") == "constitution":
            print(json.dumps({"updated": False,
                              "reason": "constitution is read_only — only "
                                        "constitution-add.py may write it"}))
            return
        if args.text is not None:
            text = args.text
        else:
            text = " ".join(args.arg[1:]) if len(args.arg) > 1 else None
        ok = update_entry(db, eid, text=text)
        print(json.dumps({"updated": ok}))
        return
    if args.cmd == "query-core":
        rows = db.execute(
            "SELECT id, text, topic, priority, confidence FROM entries "
            "WHERE always_on=1 AND status='active' ORDER BY priority ASC"
        ).fetchall()
        out = [{"id": r[0], "text": r[1], "topic": r[2] or "memory",
                "priority": r[3] if r[3] is not None else 5,
                "confidence": r[4]} for r in rows]
        print(json.dumps(out, indent=2))
        return
    if args.cmd == "delete":
        ok = delete_entry(db, int(args.arg[0]))
        print(json.dumps({"deleted": ok}))
        return
    if args.cmd == "working":
        task = args.arg[0]
        rest = " ".join(args.arg[1:])
        if rest:
            working_set(db, task, rest)
            print(json.dumps({"working_set": True}))
        else:
            print(json.dumps(working_get(db, task)))
        return
    if args.cmd == "audit":
        claim, verdict, evidence = (args.arg + ["", ""])[:3]
        print(json.dumps({"hash": audit_append(db, claim, verdict, evidence)}))
        return
    if args.cmd == "verify":
        ok, msg = audit_verify(db)
        print(json.dumps({"ok": ok, "msg": msg}))
        sys.exit(0 if ok else 1)
    if args.cmd == "stats":
        print(json.dumps(stats(db), indent=2))
        return
    if args.cmd == "associate":
        a = int(args.arg[0]) if args.arg else 0
        b = int(args.arg[1]) if len(args.arg) > 1 else 0
        associate(db, a, b)
        print(json.dumps({"associated": [a, b]}))
        return
    if args.cmd == "linked":
        eid = int(args.arg[0]) if args.arg else 0
        print(json.dumps(linked_entries(db, eid), indent=2))
        return
    if args.cmd == "scene":
        if args.arg and args.arg[0] == "list":
            print(json.dumps(list_scenes(db), indent=2))
        elif len(args.arg) >= 2:
            name = args.arg[0]
            members = [int(x) for x in args.arg[1:] if x.isdigit()]
            sid = make_scene(db, name, members)
            print(json.dumps({"scene_id": sid, "members": len(members)}))
        return
    if args.cmd == "hierarchy":
        print(json.dumps(hierarchy(db), indent=2))
        return
    if args.cmd == "mine":
        target = args.arg[0] if args.arg else ""
        if not target:
            print("mine needs <file-or-directory>", file=sys.stderr)
            sys.exit(1)
        # Accept a FILE or a DIRECTORY — the compaction plugin hands a single
        # transcript file, while the sleep-time cycle hands a directory.
        if os.path.isfile(target):
            res = mine_file(db, target, wing=args.wing, room=args.room)
        else:
            res = mine_directory(db, target, wing=args.wing, room=args.room)
        print(json.dumps(res, indent=2) if args.json else json.dumps(res))
        return
    if args.cmd == "chunk-recall":
        q = " ".join(args.arg)
        print(json.dumps(chunk_recall(db, q, wing=args.wing,
                                      min_sim=args.min_sim, doc=args.doc), indent=2))
        return
    if args.cmd == "wake":
        print(wake_up(db))
        return
    if args.cmd == "export":
        rows = db.execute("SELECT chunk_id, wing, room, source_path, text "
                          "FROM chunks").fetchall()
        out = [{"chunk_id": r["chunk_id"], "wing": r["wing"], "room": r["room"],
                "source": r["source_path"], "text": r["text"]} for r in rows]
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
