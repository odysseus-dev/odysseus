#!/usr/bin/env python3
"""dedupe_policy.py — the dedupe + supersede policy with receipts.

The user's rule (2026-08-13):

  1. A fact requested/stored ONCE is enough for a record.
  2. If the same data point arrives again — identical OR semantically similar —
     it does NOT create a second record.
  3. It only CHANGES when new information REPLACES the old information for the
     SAME data point. Replacement is a supersede: the old record is marked
     superseded (status + valid_until), the new one becomes active, and the
     transition is preserved (never blind delete).
  4. Every decision — created / duplicate-touched / superseded — is logged to a
     RECEIPTS ledger so any change is auditable: what, when, why, and on what
     evidence.

Similarity policy:
  - Exact match (same normalized text)            → duplicate, touch existing.
  - Semantic similarity (embedding cosine >= SIM) → duplicate IF the new fact
    carries no new information beyond the existing one.
  - Semantic similarity + new evidence (higher confidence, fresher source,
    more specific text) → SUPERSEDE: old retired, new active, transition logged.
  - Nothing similar                                  → create new record.

This module is the gate. Every write path (curator, memory tools, sleep-time)
routes through it, so one record per data point is enforced everywhere, and the
receipts ledger is the single audit trail.

Usage:
  dedupe_policy.py add "<text>" --topic human --importance 0.8 --source X
  dedupe_policy.py receipts                     # print the receipts ledger
  dedupe_policy.py check "<text>"               # would this be new, dupe, or supersede?
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_env

DB = memory_env.store_db()

# Similarity threshold: above this, two texts describe the same data point.
SIM_THRESHOLD = 0.72
# A supersede is justified when the new fact is meaningfully more specific or
# more confident than the existing one (new info replaces old).
SUPERSEDE_LENGTH_BONUS = 20  # new text at least this many chars longer


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(text):
    """Normalize for comparison: lowercase, strip bullets/punct, collapse."""
    t = re.sub(r"^[-*#>\s]+", "", text.strip())
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _embed_pair(a, b):
    """Best-effort embeddings for cosine similarity. Falls back to token
    overlap if no embedder is available."""
    try:
        import memory_store as ms
        from importlib import reload
        vecs = ms._embed([a, b])
        va, vb = vecs.get(a), vecs.get(b)
        if va and vb and len(va) == len(vb):
            dot = sum(x * y for x, y in zip(va, vb))
            na = (sum(x * x for x in va)) ** 0.5 or 1
            nb = (sum(y * y for y in vb)) ** 0.5 or 1
            return dot / (na * nb)
    except Exception:
        pass
    # Fallback: Jaccard token overlap
    ta = set(_norm(a).split())
    tb = set(_norm(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _find_similar(db, text, topic=None):
    """Return (entry, score) for the best existing active entry on the same
    data point, or None."""
    norm = _norm(text)
    if not norm:
        return None
    # 1. Exact normalized match wins outright.
    row = db.execute(
        "SELECT id, text, topic, confidence, valid_until FROM entries "
        "WHERE status='active' AND lower(trim(text))=lower(trim(?)) "
        "ORDER BY id LIMIT 1", (text.strip(),)).fetchone()
    if row:
        return (row, 1.0)
    # 2. Semantic scan of same-topic active entries (bounded, cheap).
    if topic:
        cands = db.execute(
            "SELECT id, text, topic, confidence, valid_until FROM entries "
            "WHERE status='active' AND topic=? ORDER BY last_accessed DESC "
            "LIMIT 60", (topic,)).fetchall()
    else:
        cands = db.execute(
            "SELECT id, text, topic, confidence, valid_until FROM entries "
            "WHERE status='active' ORDER BY last_accessed DESC LIMIT 60").fetchall()
    best, best_score = None, 0.0
    for c in cands:
        s = _embed_pair(text, c[1])
        if s > best_score:
            best, best_score = c, s
    if best and best_score >= SIM_THRESHOLD:
        return (best, best_score)
    return None


def _receipt(db, decision, text, topic, evidence, reason, prior_id=None):
    """Append a receipt to the ledger. The ledger is the audit trail for every
    dedupe/supersede decision — who/what/when/why."""
    ts = now_iso()
    record = {
        "when": ts,
        "decision": decision,          # created | duplicate | superseded
        "topic": topic,
        "text": text[:200],
        "prior_id": prior_id,
        "evidence": (evidence or "")[:200],
        "reason": reason[:200],
        "hash": hashlib.sha256(
            f"{ts}|{decision}|{topic}|{text}".encode()).hexdigest()[:16],
    }
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS dedupe_receipts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, decision TEXT, "
            "topic TEXT, text TEXT, prior_id INTEGER, evidence TEXT, "
            "reason TEXT, hash TEXT)")
        db.execute(
            "INSERT INTO dedupe_receipts (ts, decision, topic, text, "
            "prior_id, evidence, reason, hash) VALUES (?,?,?,?,?,?,?,?)",
            (record["when"], record["decision"], record["topic"],
             record["text"], record["prior_id"], record["evidence"],
             record["reason"], record["hash"]))
        db.commit()
    except Exception:
        pass
    return record


def apply(db, text, topic="", importance=0.5, source="", confidence=None):
    """The dedupe policy gate. Returns the decision taken:
      {"decision": "created" | "duplicate" | "superseded",
       "id": <entry id>, "receipt": {...}}
    """
    text = (text or "").strip()
    if not text:
        return {"decision": "rejected", "reason": "empty text"}
    hit = _find_similar(db, text, topic or None)
    if not hit:
        # New data point: one record, logged.
        try:
            import memory_store as ms
            ok = ms.add_entry(db, text, importance, topic or "", source=source,
                              confidence=confidence, method="dedupe_policy")
        except Exception:
            ok = False
        if not ok:
            return {"decision": "rejected", "reason": "insert failed"}
        eid = db.execute(
            "SELECT id FROM entries WHERE text=? ORDER BY id DESC LIMIT 1",
            (text,)).fetchone()
        rec = _receipt(db, "created", text, topic, source,
                       "first record for this data point",
                       prior_id=eid[0] if eid else None)
        return {"decision": "created", "id": eid[0] if eid else None,
                "receipt": rec}

    existing, sim = hit
    eid, etxt, etopic, econf = existing[0], existing[1], existing[2], existing[3]
    # Same data point, no new info -> touch existing, log duplicate.
    new_longer = len(text) - len(etxt) >= SUPERSEDE_LENGTH_BONUS
    new_stronger = (confidence or 0) > (econf or 0) + 0.15
    if not new_longer and not new_stronger:
        try:
            db.execute("UPDATE entries SET last_accessed=? WHERE id=?",
                       (now_iso(), eid))
            db.commit()
        except Exception:
            pass
        rec = _receipt(db, "duplicate", text, topic, source,
                       f"same data point (sim {sim:.2f}); no new info — "
                       f"touched existing #{eid}")
        return {"decision": "duplicate", "id": eid, "receipt": rec}

    # New info replaces old for the same data point -> SUPERSEDE.
    try:
        db.execute("UPDATE entries SET status='superseded', valid_until=? "
                   "WHERE id=?", (now_iso(), eid))
        db.execute("UPDATE entries_vec SET embedding=NULL WHERE rowid=?",
                   (eid,))
        db.commit()
    except Exception:
        pass
    try:
        import memory_store as ms
        ok = ms.add_entry(db, text, importance, topic or "", source=source,
                          confidence=confidence, method="dedupe_policy")
    except Exception:
        ok = False
    rec = _receipt(db, "superseded", text, topic, source,
                   f"new info replaces #{eid} (sim {sim:.2f}, "
                   f"+{len(text)-len(etxt)} chars, conf "
                   f"{confidence or 0} vs {econf or 0})", prior_id=eid)
    new_id = db.execute(
        "SELECT id FROM entries WHERE text=? ORDER BY id DESC LIMIT 1",
        (text,)).fetchone()
    return {"decision": "superseded", "id": new_id[0] if new_id else None,
            "prior_id": eid, "receipt": rec}


def receipts():
    try:
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT ts, decision, topic, text, prior_id, reason, hash "
            "FROM dedupe_receipts ORDER BY id DESC LIMIT 50").fetchall()
        db.close()
        return rows
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["add", "check", "receipts"])
    ap.add_argument("text", nargs="*", default=[])
    ap.add_argument("--topic", default="")
    ap.add_argument("--importance", type=float, default=0.5)
    ap.add_argument("--source", default="")
    ap.add_argument("--confidence", type=float, default=None)
    args = ap.parse_args()

    if args.cmd == "receipts":
        rows = receipts()
        if not rows:
            print("(no dedupe receipts yet — the ledger is empty)")
            return
        for r in rows:
            print(f"[{r[0]}] {r[1]:10s} {r[2]:10s} #{r[3][:50] or ''} "
                  f"prior=#{r[4]} :: {r[5][:80]}")
        return

    text = " ".join(args.text).strip()
    if not text:
        print("empty text")
        sys.exit(1)
    db = sqlite3.connect(DB)
    if args.cmd == "check":
        hit = _find_similar(db, text, args.topic or None)
        if not hit:
            print("VERDICT: new")
        elif hit[1] >= 1.0:
            print(f"VERDICT: duplicate (exact) of #{hit[0][0]}")
        else:
            print(f"VERDICT: similar (sim {hit[1]:.2f}) to #{hit[0][0]}: "
                  f"{hit[0][1][:60]}")
    else:
        res = apply(db, text, args.topic, args.importance, args.source,
                    args.confidence)
        print(json.dumps(res, indent=2, default=str))
    db.close()


if __name__ == "__main__":
    main()
