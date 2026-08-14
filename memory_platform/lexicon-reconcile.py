#!/usr/bin/env python3
"""lexicon-reconcile.py — retroactively apply the NEW lexical semantics to the store.

The recall layer changed: matching is now CONTEXT-AWARE (conjunctive + rare-word
distinctiveness) instead of loose single-word OR matching. Per the system's own
retroactive rule ("whenever a newer design is introduced, everything tangentially
related migrates to the new schema"), the store's DERIVED aspects must be
reconciled against the new semantics so they are not stale:

  1. ASSOCIATIONS: rebuild from co-occurring DISTINCTIVE words. Under the old
     loose matching, an association could have been formed on a shared common
     word ("black") that is not a real relation. Under the new semantics, two
     entries are associated only when they share a RARE (distinctive) content
     word — the same signal the recall gate uses. Stale/weak associations are
     dropped; genuine distinctive links are kept and strengthened.

  2. NEURON TRIGGERS: a warm neuron fires on its trigger terms. A trigger that
     is a common word (appears in many entries) would fire on noise — the same
     false-association problem. Reconcile flags triggers that are non-
     distinctive (document frequency >= 5% of active entries) and optionally
     rewrites the neuron's trigger list to keep only distinctive terms.

Rare-word definition (shared with the recall gate): a content word is
"distinctive" if it appears in fewer than 5% of active entries.

Usage:
  ~/.venvs/memory/bin/python3 lexicon-reconcile.py --dry-run
  ~/.venvs/memory/bin/python3 lexicon-reconcile.py --associations
  ~/.venvs/memory/bin/python3 lexicon-reconcile.py --neurons
  ~/.venvs/memory/bin/python3 lexicon-reconcile.py --all   # full reconcile
"""

import argparse
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_env
import memory_store as ms

STORE_DB = memory_env.store_db()
DISTINCTIVE_DF = 0.05  # < 5% of active entries = distinctive
# Dense-proximity floor, calibrated against real cases (nomic band ~0.6-0.75):
# a shared distinctive word is a necessary signal but NOT sufficient — the two
# entries must also be semantically close (same research grounding as recall:
# Salton/Fox/Wu conjunction + QPP abstention). This kills "persona dossier" <-> "method
# core" style pairs that share generic-but-rare words without being related.
ASSOC_COSINE_FLOOR = 0.74

_STOP = {"the", "and", "for", "with", "that", "this", "from", "into",
         "when", "then", "were", "have", "been", "will", "was", "are",
         "but", "not", "you", "your", "also", "its", "his", "her", "him",
         "over", "under", "them", "they", "there", "about", "after",
         "what", "which", "who", "how", "why", "where", "while", "just",
         "more", "each", "than", "then", "very", "such", "some", "only",
         "prefers", "needs", "wants", "likes"}


def _connect():
    """Connect via memory_store.connect() so schema migrations (e.g. the
    `summary` column) apply before reconcile reads/writes."""
    return ms.connect()


def _content_words(text):
    return [w for w in re.findall(r"[a-z]{4,}", (text or "").lower())
            if w not in _STOP]


def _df(db, word):
    try:
        return db.execute(
            "SELECT COUNT(*) AS n FROM entries WHERE status='active' "
            "AND text LIKE ?", (f"%{word}%",)).fetchone()["n"]
    except Exception:
        return 0


def _distinctive_words(db, text, total):
    """Content words in `text` that are RARE in the store (< DISTINCTIVE_DF)."""
    out = []
    for w in _content_words(text):
        if _df(db, w) / max(total, 1) < DISTINCTIVE_DF:
            out.append(w)
    return out


# ---------------------------------------------------------------- associations

def reconcile_associations(dry_run=False):
    db = _connect()
    db.row_factory = sqlite3.Row
    total = db.execute("SELECT COUNT(*) FROM entries WHERE status='active'").fetchone()[0]
    rows = db.execute(
        "SELECT id, text, topic FROM entries WHERE status='active'").fetchall()
    entry_words = {}
    for r in rows:
        entry_words[r["id"]] = set(_content_words(r["text"]))
    # Build the new associative map: link A<->B when they share a DISTINCTIVE
    # word (a real relation) AND are semantically close (dense cosine above the
    # calibrated floor). A shared rare word alone is NOT a relation — the same
    # meta-judgement that drives the recall gate ("black coffee" != "black
    # holes"): "persona dossier" and "method core" share generic words like
    # "flat"/"those" without being related, so they must not be linked.
    new_links = {}
    ids = list(entry_words.keys())
    texts = {r["id"]: r["text"] for r in rows}
    # embed entry texts once, dedup by identical text to avoid re-embedding
    seen_texts = {}
    to_embed = []
    for r in rows:
        if r["text"] not in seen_texts:
            seen_texts[r["text"]] = r["id"]
            to_embed.append(f"search_document: {r['text']}")
    emb = ms._embed(to_embed) if to_embed else {}
    vecs = {}
    for k, v in emb.items():
        text = k.replace("search_document: ", "", 1)
        vecs[seen_texts[text]] = v
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            shared = entry_words[a] & entry_words[b]
            if not shared:
                continue
            distinct = [w for w in shared if _df(db, w) / max(total, 1) < DISTINCTIVE_DF]
            if not distinct:
                continue
            va, vb = vecs.get(a), vecs.get(b)
            if va is None or vb is None:
                continue
            dot = sum(x * y for x, y in zip(va, vb))
            na = sum(x * x for x in va) ** 0.5
            nb = sum(x * x for x in vb) ** 0.5
            cos = dot / (na * nb) if na and nb else 0.0
            # SAME meta-judgement as recall/auto-associate: distinctive shared
            # word + floor, OR very strong paraphrase (0.80 escape hatch).
            if cos >= ms.ASSOC_STRONG_COSINE:
                new_links[(a, b)] = min(1.0, 0.3 + 0.1 * min(2, 5))
                continue
            if cos < ASSOC_COSINE_FLOOR:
                continue
            key = tuple(sorted((a, b)))
            # strength scales with the number of shared distinctive words
            new_links[key] = min(1.0, 0.3 + 0.1 * min(len(distinct), 5))
    if dry_run:
        print(f"  would rebuild {len(new_links)} distinctive associations "
              f"(current: {db.execute('SELECT COUNT(*) FROM associations').fetchone()[0]})")
        db.close()
        return
    db.execute("DELETE FROM associations")
    # BOUNDED FANOUT (bloat guard): each entry links to at most
    # ms.ASSOC_FANOUT_CAP others, strongest kept. The graph stays small and
    # indexed — a recall accelerator, not a dense web that slows queries.
    from collections import defaultdict
    fanout = defaultdict(list)
    for (a, b), strength in new_links.items():
        fanout[a].append((strength, b))
        fanout[b].append((strength, a))
    for eid in fanout:
        fanout[eid].sort(key=lambda x: -x[0])
        fanout[eid] = fanout[eid][:ms.ASSOC_FANOUT_CAP]
    _written_links = set()
    written = 0
    for eid, lst in fanout.items():
        for strength, other in lst:
            if (eid, other) not in _written_links:
                db.execute(
                    "INSERT OR REPLACE INTO associations (src_id, dst_id, "
                    "strength, updated_at) VALUES (?,?,?,?)",
                    (eid, other, strength, ms.now_iso()))
                _written_links.add((eid, other))
                written += 1
    db.commit()
    n = db.execute("SELECT COUNT(*) FROM associations").fetchone()[0]
    print(f"  rebuilt associations: {n} links "
          f"(distinctive-word + cosine >= {ASSOC_COSINE_FLOOR}, "
          f"fanout cap {ms.ASSOC_FANOUT_CAP})")
    db.close()


# ------------------------------------------------------------------- neurons

def reconcile_neurons(dry_run=False, fix=False):
    db = _connect()
    db.row_factory = sqlite3.Row
    total = db.execute("SELECT COUNT(*) FROM entries WHERE status='active'").fetchone()[0]
    neurons = db.execute(
        "SELECT id, slug, triggers FROM entries WHERE kind='neuron' "
        "AND status='active'").fetchall()
    for n in neurons:
        trigs = [t.strip().lower() for t in (n["triggers"] or "").split(",")
                 if t.strip()]
        # A trigger that is a GENERIC content word (in the stopword set, e.g.
        # "prefers", "needs", "like") would fire on noise. Note: this is NOT a
        # rare-word check — a persona name like "mentor" appearing in many
        # entries makes the trigger MORE useful (the persona is discussed
        # often). Rare-word enforcement would wrongly strip valid routing.
        bad = [t for t in trigs if t in _STOP or len(t) < 4]
        if not bad:
            continue
        msg = f"  neuron '{n['slug']}': generic/too-short triggers {bad}"
        if dry_run:
            print(f"  [would fix] {msg}")
            continue
        if fix:
            keep = [t for t in trigs if t not in _STOP and len(t) >= 4]
            if not keep:
                keep = trigs  # never empty a neuron's triggers
            db.execute("UPDATE entries SET triggers=? WHERE id=?",
                       (", ".join(keep), n["id"]))
            print(f"  [fixed] {msg} -> kept {keep}")
    db.commit()
    db.close()


# ---------------------------------------------------------------- summaries

def reconcile_summaries(dry_run=False):
    """Backfill AAAK-compressed summaries for entries that lack them.

    The compression route (aaak.py) makes the store LIGHT at rest: a zettel is
    ~30-40x smaller than the verbatim body, so wake-up context and warm firing
    can inject far more signal within the same token budget. Retroactive: every
    pre-existing entry gets a summary on the first reconcile after upgrade."""
    db = _connect()
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, text, topic, source FROM entries "
        "WHERE status='active' AND (summary IS NULL OR summary='')").fetchall()
    if dry_run:
        print(f"  would add AAAK summaries to {len(rows)} entries")
        db.close()
        return
    n = 0
    for r in rows:
        try:
            import aaak
            summ = aaak.compress(r["text"], topic=r["topic"],
                                 source_file=r["source"])
        except Exception:
            summ = ""
        if summ:
            db.execute("UPDATE entries SET summary=? WHERE id=?", (summ, r["id"]))
            n += 1
    db.commit()
    print(f"  added AAAK summaries to {n} entries")
    db.close()


def reconcile_expression(dry_run=False, wing="method-books", min_signals=2,
                         limit=60, apply_to_entries=False):
    """RETROACTIVE two-axis scan: find expressive persona material in a mined
    corpus that the OLD single-axis gate would have suppressed, and surface it
    for promotion to the persona/delivery voice.

    Before the two-axis design, subjective material ("I find wonder in the
    cosmos and tell stories") scored 0 for "no evidence trace" and could never
    promote — so a mine yielded method but not voice. This reconcile
    scans the latest mine's chunks for strong expression signals and runs the
    two-axis worthiness gate, reporting what is now promotable.

    Report-only by default (dry_run=True shows candidates). With apply=True,
    it writes the strongest, worthiness-cleared voice material into the store
    as persona entries (deduped), so the retroactive absorption reaches the
    personality without a full curator re-run.
    """
    import re
    db = _connect()
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT rowid AS id, text FROM chunks WHERE wing=? AND length(text)>200 "
        "ORDER BY rowid LIMIT ?", (wing, 30000)).fetchall()
    try:
        import worthiness as w
    except Exception:
        w = None
        print("  worthiness.py unavailable — report only")
    if w is None:
        db.close()
        return

    # tokenize candidate voice lines from chunks: first-person expressive
    # statements (sentences with "i find/i believe/i am/i prefer/i tell" etc.)
    found = []
    seen = set()
    for r in rows:
        low = r["text"].lower()
        hits = sum(1 for group in w._EXPRESSION_SIGNALS.values()
                   for s in group if s in low)
        if hits < min_signals:
            continue
        # extract the first-person expressive sentence(s)
        for m in re.finditer(r"[^.]*?(?:i find|i believe|i am|i prefer|i tell|"
                             r"i love|i am drawn|i wonder|my sense is)[^.]*\.?",
                             r["text"], re.I):
            cand = m.group(0).strip()
            if len(cand) < 20 or len(cand) > 220:
                continue
            # skip interview/dialogue/title artifacts — we want narrative voice
            if re.search(r"\bQ:\s|\bA:\s", cand) or \
               cand.count('"') >= 2 or cand.isupper() or \
               cand.startswith("EXTRATERRESTRIAL") or \
               re.match(r"^[A-Z][A-Z\s]{8,}$", cand):
                continue
            # PREFER the epistemic-aesthetic signature: wonder, humility, the
            # marriage of skepticism and wonder — the character-defining voice
            sig = ["wonder", "skeptic", "cosmos", "stars", "vast", "humbl",
                   "journeywork", "thirst", "awe", "leaf of grass", "profound"]
            is_signature = any(s in cand.lower() for s in sig)
            if not is_signature and hits < 3:
                continue
            key = cand[:60].lower()
            if key in seen:
                continue
            seen.add(key)
            verdict, score, answers, veto = w.assess(cand, "persona")
            found.append({
                "candidate": cand,
                "verdict": verdict,
                "score": round(score, 2),
                "source_chunk": r["id"],
                "signals": hits,
            })
        if len(found) >= limit:
            break

    # promotable = worthiness PROMOTE (strong, constitution-aligned voice)
    promotable = [f for f in found if f["verdict"] in ("PROMOTE", "ABSORB")]
    promotable.sort(key=lambda f: -f["score"])
    print(f"  scanned {len(rows)} chunks, {len(found)} expressive candidates, "
          f"{len(promotable)} promotable (two-axis)")
    if dry_run:
        print("  --dry-run: candidates (would write the top ones to persona) --")
        for f in promotable[:8]:
            print(f"    [{f['verdict']:<7} {f['score']:+.1f}] {f['candidate'][:75]}")
        db.close()
        return

    if not apply_to_entries:
        print("  apply_to_entries=False — reporting only. Re-run with "
              "--apply-expression to write to persona.")
        db.close()
        return

    # write the strongest promotable voice lines as persona entries (deduped)
    n = 0
    for f in promotable[:6]:
        text = f["candidate"].strip()
        exists = db.execute(
            "SELECT COUNT(*) FROM entries WHERE topic='persona' AND text=?",
            (text,)).fetchone()[0]
        if exists:
            continue
        import memory_store as ms
        ms.add_entry(db, text, importance=0.7, topic="persona",
                     source=f"retroactive two-axis scan (method-books)")
        n += 1
    db.commit()
    print(f"  wrote {n} expressive persona entries (retroactive two-axis)")
    db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--associations", action="store_true")
    ap.add_argument("--neurons", action="store_true")
    ap.add_argument("--summaries", action="store_true")
    ap.add_argument("--expression", action="store_true",
                    help="scan the latest mine for retroactive two-axis voice material")
    ap.add_argument("--wing", default="method-books")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--apply-expression", action="store_true",
                    help="write the strongest expression candidates to persona")
    ap.add_argument("--fix", action="store_true",
                    help="actually rewrite neuron triggers (default: report only)")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    do_assoc = args.associations or args.all
    do_neuron = args.neurons or args.all
    do_summ = args.summaries or args.all
    do_expr = args.expression or args.all
    if not (do_assoc or do_neuron or do_summ or do_expr):
        do_assoc = do_neuron = do_summ = do_expr = True

    print("LEXICON RECONCILE — retroactive application of new lexical semantics")
    if do_assoc:
        print("  associations (rebuild on distinctive words):")
        reconcile_associations(dry_run=args.dry_run)
    if do_neuron:
        print("  neuron triggers (flag/rewrite non-distinctive):")
        reconcile_neurons(dry_run=args.dry_run, fix=args.fix)
    if do_summ:
        print("  AAAK summaries (backfill):")
        reconcile_summaries(dry_run=args.dry_run)
    if do_expr:
        print(f"  expression scan (retroactive two-axis, wing={args.wing}):")
        reconcile_expression(dry_run=args.dry_run, wing=args.wing,
                             limit=args.limit,
                             apply_to_entries=args.apply_expression)


if __name__ == "__main__":
    main()
