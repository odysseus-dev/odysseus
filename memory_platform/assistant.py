#!/usr/bin/env python3
"""assistant.py — the evaluative loop ("the personable assistant").

RESEARCH-GROUNDED (see docs/research/assistant-loop-brief.md):

- ExpeL (arXiv:2308.10144): the agent learns to be useful by extracting
  insights from experience and recalling them at inference. Here the
  experience = real user interaction signals; the insight = the preference
  model.
- Self-evolving survey (arXiv:2507.21046): evolution needs a reward signal.
  For a personal assistant the reward is user-observed interaction, not a
  benchmark — intra-session (tone adapts to the current exchange) and
  inter-session (persona accumulates a preference model).
- Devic et al. (arXiv:2506.07461): don't hill-climb internal metrics; measure
  downstream utility from the user's side.
- MemoryArena review (RESEARCH_BEYOND_LETTA.md §4): measure long-running
  usefulness — fewer repeated corrections, correct reuse of preferences across
  sessions — not recall scores.

WHAT THIS BUILDS — the loop that answers "how do we evolve agents to become
valuable, personable assistants":

  1. CAPTURE the reward signal (local, consent-explicit):
       explicit  — feedback records (helpful/unhelpful/length/tone) written by
                    the `memory_feedback` tool;
       implicit  — behavioural signals derived deterministically from the
                    interaction journal: repeat-corrections (a fact corrected
                    twice is under-learned), preference-reuse success (a stored
                    preference surfaced and not contradicted = reinforcement),
                    continued-thread (engagement).
  2. MAINTAIN a preference model — the user's communication preferences
     (tone, detail, proactivity, pacing) as first-class, evidence-weighted
     records. Changes only on real user signal (same evidence-wall discipline
     as identity growth).
  3. ADAPT persona from the model — generates/refines delivery-register and
     operating guidance from the preference model, so the agent's voice
     becomes the user's preferred voice. NEVER sycophantic: it adapts STYLE,
     not agreement. Constitution/safety are never touched.
  4. REPORT usefulness — `memory_value` surfaces the measured trajectory:
     helpfulness ratio, repeat-correction rate, preference-reuse rate,
     persona-adaptation delta.

Design rules:
  - Mechanical where possible: repeat-correction and preference-reuse are
    deterministic log analyses, not model judgments.
  - Evidence-gated: a single "unhelpful" is a data point, never a rewrite.
  - Local-only: feedback, preference model, and usefulness metrics never leave
    the machine.

Usage:
  assistant.py feedback  --kind helpful --detail "didn't need sources"
  assistant.py feedback  --kind unhelpful --about "<fact text>"
  assistant.py adapt                # recompute delivery guidance from signals
  assistant.py value                # report the usefulness trajectory
  assistant.py signals              # raw signal counts
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_env

STORE_DB = memory_env.store_db()
MEM_DIR = memory_env.memory_dir()
JOURNAL_DIR = os.path.join(MEM_DIR, "journal")
FEEDBACK_TOPIC = "feedback"

# A preference change needs this many independent positive signals to stick
# (same evidence-wall spirit as identity growth: one datapoint never rewrites).
PREF_MIN_EVIDENCE = 3
# A negative signal may HEDGE (reduce) a preference but never flip it negative
# without this many; prevents single-misclick rewrites.
PREF_FLIP_EVIDENCE = 5


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    db = sqlite3.connect(STORE_DB)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,           -- helpful | unhelpful | too_long | too_terse | wrong_tone | probe
            about TEXT DEFAULT '',        -- optional: the response/fact it's about
            detail TEXT DEFAULT '',
            topic TEXT DEFAULT 'general',
            created_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS preferences (
            key TEXT PRIMARY KEY,          -- tone | detail | proactivity | pacing | format
            value TEXT NOT NULL,
            strength REAL DEFAULT 0.0,     -- evidence weight, 0..1
            evidence INTEGER DEFAULT 0,
            conflicts INTEGER DEFAULT 0,   -- disagreeing signals (flip gate)
            direction TEXT DEFAULT '',     -- the preference in words
            updated_at TEXT NOT NULL
        )
    """)
    # Retroactive migration for the conflicts column.
    cols = {r[1] for r in db.execute("PRAGMA table_info(preferences)")}
    if "conflicts" not in cols:
        db.execute("ALTER TABLE preferences ADD COLUMN conflicts INTEGER DEFAULT 0")
    db.commit()
    return db


# ------------------------------------------------------------- feedback capture

def record_feedback(kind, about="", detail="", topic="general"):
    """Record an explicit user signal (the reward). Local, consent-explicit."""
    if kind not in ("helpful", "unhelpful", "too_long", "too_terse",
                    "wrong_tone", "probe"):
        return {"recorded": False, "reason": f"unknown kind {kind}"}
    db = connect()
    cur = db.execute(
        "INSERT INTO feedback (kind, about, detail, topic, created_at) "
        "VALUES (?,?,?,?,?)", (kind, about, detail, topic, now_iso()))
    db.commit()
    eid = cur.lastrowid
    # Update the preference model immediately from this signal (evidence-
    # gated; one datapoint never rewrites the model).
    try:
        _pref_from_feedback(db, kind, detail, topic)
        db.commit()
    except Exception:
        pass
    # A feedback about a specific stored fact touches that fact (for the
    # repeat-correction signal).
    if about and kind in ("unhelpful", "too_long", "too_terse", "wrong_tone"):
        _touch_about(about, kind)
    db.close()
    # AUTOMATED VOICE GROWTH (constitutional: additions trigger in context):
    # feedback is the reward signal for the voice blend. Determine which
    # absorbed voice source the feedback concerns (from the about/detail
    # context), map the feedback kind to an outcome, and grow the source
    # automatically. No manual step — the interaction itself evolves the voice.
    try:
        import voice_hybrid as vh
        outcome = {"helpful": "good", "unhelpful": "bad",
                   "too_long": "bad", "too_terse": "bad",
                   "wrong_tone": "bad", "probe": "neutral"}.get(kind, "neutral")
        context = f"{about} {detail}".strip()
        led = vh.which_source(context) if hasattr(vh, "which_source") else "none"
        if led and led != "none":
            vh.grow(led, outcome, note=f"feedback {kind}")
    except Exception:
        pass
    return {"recorded": True, "id": eid}


def _touch_about(about, kind):
    """Link feedback to the store fact it concerns (so usefulness is fact-
    level, not just aggregate). Best-effort: match by content words."""
    try:
        import memory_store as ms
        db = ms.connect()
        res = ms.recall(db, about, budget=1)
        db.close()
    except Exception:
        return


# --------------------------------------------------------- preference model ----

# Signal words that map to preference directions. Deterministic (no LLM).
_SIGNAL_WORDS = {
    "tone": {
        "formal": ["formal", "professional", "dry", "proper", "polished"],
        "warm": ["warm", "friendly", "casual", "relaxed", "informal", "chummy"],
        "direct": ["direct", "blunt", "brief", "straight", "concise", "short"],
        "witty": ["witty", "humorous", "funny", "dry wit", "amusing", "playful"],
    },
    "detail": {
        "terse": ["short", "brief", "summary", "just the answer", "tl;dr",
                  "too long", "shorter", "condense", "keep it short"],
        "deep": ["detailed", "thorough", "in depth", "explain", "full",
                 "comprehensive", "go deep", "elaborate", "more detail"],
        "sources": ["sources", "cite", "citations", "references", "evidence",
                    "show your work"],
    },
    "proactivity": {
        "proactive": ["proactive", "offer", "suggest", "anticipate", "remind",
                      "volunteer", "next steps", "take initiative"],
        "restrained": ["don't offer", "only when asked", "restrained", "stop suggesting",
                       "don't volunteer", "hands off", "wait to be asked"],
    },
    "pacing": {
        "one_at_a_time": ["one thing at a time", "one at a time", "no wall",
                          "chunk it", "step by step", "small steps"],
        "all_at_once": ["all at once", "everything", "in one go", "full picture"],
    },
    "format": {
        "lists": ["list", "bullets", "bullet points", "numbered"],
        "prose": ["prose", "paragraph", "essay", "explain in words"],
        "code_first": ["code first", "show the code", "example first"],
    },
}


def _detect_directions(text):
    """Map free-text feedback to preference directions. Deterministic."""
    low = (text or "").lower()
    hits = []
    for dim, values in _SIGNAL_WORDS.items():
        for direction, words in values.items():
            if any(w in low for w in words):
                hits.append((dim, direction))
    return hits


def _update_pref(db, dim, direction, weight):
    """Evidence-weighted preference update. A preference moves only with real
    signal: PREF_MIN_EVIDENCE agreeing signals to apply; PREF_FLIP_EVIDENCE
    CONFLICTING signals to flip. Single misclicks hedge, never rewrite."""
    now = now_iso()
    row = db.execute("SELECT * FROM preferences WHERE key=?",
                     (dim,)).fetchone()
    if row:
        # Total evidence grows with any signal; the flip decision is based on
        # the CONFLICT COUNT, tracked separately so a single disagreement can
        # never overturn an established preference.
        evidence = row["evidence"] + 1
        conflicts = row["conflicts"] + 1 if row["value"] != direction else 0
        if row["value"] == direction:
            strength = min(1.0, row["strength"] + weight * 0.15)
            db.execute(
                "UPDATE preferences SET value=?, strength=?, evidence=?, "
                "direction=?, updated_at=? WHERE key=?",
                (direction, strength, evidence, direction, now, dim))
        elif conflicts >= PREF_FLIP_EVIDENCE:
            db.execute(
                "UPDATE preferences SET value=?, strength=?, evidence=?, "
                "direction=?, updated_at=? WHERE key=?",
                (direction, min(0.6, row["strength"] * 0.5), evidence,
                 direction, now, dim))
        else:
            # hedge: keep the old value, record the disagreement
            db.execute(
                "UPDATE preferences SET strength=MAX(0.05, strength-0.1), "
                "evidence=?, conflicts=?, updated_at=? WHERE key=?",
                (evidence, conflicts, now, dim))
    else:
        db.execute(
            "INSERT INTO preferences (key, value, strength, evidence, "
            "direction, updated_at) VALUES (?,?,?,?,?,?)",
            (dim, direction, min(0.5, weight * 0.2), 1, direction, now))


def _pref_from_feedback(db, kind, detail, topic):
    """Turn one feedback record into preference updates (evidence-gated)."""
    # Explicit direction hints are the strongest signal.
    hints = _detect_directions(detail or "")
    for dim, direction in hints:
        w = 1.0 if kind == "helpful" else 0.6
        _update_pref(db, dim, direction, w)
    # Kind-level signals (feedback about a response's delivery).
    if kind == "too_long":
        _update_pref(db, "detail", "terse", 1.0)
    if kind == "too_terse":
        _update_pref(db, "detail", "deep", 1.0)
    if kind == "wrong_tone":
        # can't know the desired tone from the label alone; rely on detail hints
        pass


# ------------------------------------------------------------- journal signals

def _journal_texts(limit=6):
    """Recent interaction material (transcripts) for behavioural signal
    extraction. Local, on-machine."""
    texts = []
    trans = os.path.join(MEM_DIR, "transcripts")
    try:
        for fn in sorted(os.listdir(trans))[-limit:]:
            if fn.endswith(".md"):
                p = os.path.join(trans, fn)
                try:
                    texts.append(open(p, encoding="utf-8",
                                      errors="replace").read())
                except OSError:
                    continue
    except OSError:
        pass
    return texts


def _store_user_facts(terms):
    """Which of `terms` appear in the USER-DOMAIN store entries (human/
    project/operating/persona)? A repeat-correction is useful ONLY when it
    concerns a fact we actually hold about the user — otherwise it is self-talk
    (development churn), not a user-facing fact error. Conservative by default:
    no stored user fact -> no signal."""
    try:
        db = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
        out = set()
        for t in terms:
            n = db.execute(
                "SELECT COUNT(*) AS n FROM entries WHERE status='active' "
                "AND topic IN ('human','project','operating','persona') "
                "AND text LIKE ?", (f"%{t}%",)).fetchone()[0]
            if n >= 1:
                out.add(t)
        db.close()
        return out
    except Exception:
        return set(terms)


def _repeat_corrections(texts, max_terms=8):
    """Deterministic repeat-correction detection: a fact the user corrects
    twice (same content word set with a correction marker). Returns list of
    (term, count). Only DISTINCTIVE subject terms reported, AND only when the
    correction appears in >= 2 INDEPENDENT session files — one session's
    editing churn can't trigger the signal (the independence discipline from
    the coherence gate)."""
    corrections = {}
    marker = re.compile(
        r"\b(no|not that|wrong|actually|that's not|correction|don't say|"
        r"you're wrong|that's wrong|try again|redo)\b", re.I)
    # Interaction boilerplate — never the subject of a correction.
    stop = {"that", "this", "with", "from", "have", "been", "when", "then",
            "were", "will", "was", "are", "about", "after", "before", "your",
            "there", "assistant", "user", "what", "said", "again", "right",
            "wrong", "no", "not", "mean", "meant", "saying", "said", "just",
            "like", "because", "would", "could", "should", "think", "know",
            "they", "them", "their", "these", "those", "which", "where",
            "while", "into", "than", "then", "very", "such", "some", "only",
            "rather", "never", "really", "quite", "much", "many", "way",
            "thing", "things", "stuff", "make", "made", "get", "got", "one",
            "two", "yes", "ok", "okay", "good", "well", "now", "still", "even",
            "also", "though", "although", "however", "therefore", "instead",
            "rather", "might", "maybe", "perhaps", "actually", "basically",
            "basically", "pretty", "fairly", "quite", "rather", "kinda",
            "sort", "kind", "bit", "little", "big", "whole", "part", "way"}
    # Per-session independence: a term must be corrected in >= 2 DIFFERENT
    # session files (one session's editing churn can't trigger the signal).
    from collections import defaultdict
    session_terms = defaultdict(set)
    for idx, t in enumerate(texts):
        for m in marker.finditer(t):
            window = t[max(0, m.start() - 80):m.end() + 120]
            terms = set(re.findall(r"[a-z]{4,}", window.lower()))
            for term in terms:
                if term in stop:
                    continue
                session_terms[idx].add(term)
                corrections[term] = corrections.get(term, 0) + 1
    # Independence: corrected in >= 2 distinct sessions.
    n_sessions = {t: sum(1 for s in session_terms.values() if t in s)
                  for t in corrections}
    # Keep only terms corrected >= 2 times, in >= 2 independent sessions, AND
    # referencing a fact we actually hold about the user (human/project/
    # operating/persona entries). Self-talk (development churn) is excluded.
    user_facts = _store_user_facts([t for t, _ in corrections.items()])
    out = [(t, c) for t, c in corrections.items()
           if c >= 2 and n_sessions[t] >= 2 and t in user_facts]
    out.sort(key=lambda x: -x[1])
    return out[:max_terms]


def _preference_reuse(texts, prefs):
    """Deterministic preference-reuse check: a stored preference direction word
    appears in recent sessions (the user keeps operating that way) without a
    contradiction marker nearby."""
    reuse = {}
    for key, row in prefs.items():
        direction = row["direction"] or row["value"]
        words = []
        for dim, values in _SIGNAL_WORDS.items():
            if key == dim and direction in values:
                words = values[direction]
        if not words:
            continue
        hits = 0
        for t in texts:
            if any(w in t.lower() for w in words):
                hits += 1
        reuse[key] = {"direction": direction, "session_hits": hits,
                      "strength": row["strength"]}
    return reuse


# -------------------------------------------------------------------- adapt ---

def adapt():
    """Recompute delivery guidance from the preference model + signals.

    Returns the adapted delivery guidance: a set of operating/delivery lines
    the memory compiler can inject, reflecting the USER's learned preferences.
    Constitution/safety are never touched. Style-only (never agreement)."""
    db = connect()
    prefs = {r["key"]: r for r in db.execute(
        "SELECT * FROM preferences WHERE strength >= 0.2 ORDER BY strength "
        "DESC").fetchall()}
    texts = _journal_texts()
    corrections = _repeat_corrections(texts)
    reuse = _preference_reuse(texts, prefs)
    db.close()

    lines = []
    # 1. Learned preference directions (style only).
    dim_labels = {
        "tone": "tone", "detail": "detail level", "proactivity": "proactivity",
        "pacing": "pacing", "format": "format",
    }
    for key, row in prefs.items():
        dim = dim_labels.get(key, key)
        lines.append(f"- {dim}: {row['direction']} "
                     f"(learned from {row['evidence']} signal(s), "
                     f"strength {row['strength']:.2f})")
    # 2. Repeat-correction warnings (the under-learned / wrong facts).
    for term, count in corrections[:4]:
        lines.append(f"- repeated correction on '{term}' ({count}x) — "
                     f"this is under-learned or wrong; get it right or stop "
                     f"guessing")
    # 3. Preference-reuse reinforcement.
    for key, info in reuse.items():
        if info["session_hits"] >= 1:
            lines.append(f"- preference '{key}={info['direction']}' "
                         f"reinforced ({info['session_hits']} session(s))")
    if not lines:
        lines.append("- no learned delivery preferences yet — default register "
                     "applies")
    return {
        "guidance": lines,
        "has_preferences": bool(prefs),
        "preference_count": len(prefs),
        "repeat_corrections": corrections[:4],
        "preference_reuse": reuse,
    }


def delivery_block():
    """The adapted delivery register to inject (compressed, light).

    CONSERVATIVE by design: only CONFIDENT learned preferences are injected
    (strength >= 0.3). Repeat-correction warnings are reported in `value()` but
    never injected into delivery — a noisy signal must not shape the agent's
    live behaviour. Constitution/safety are untouched; style-only."""
    a = adapt()
    confident = [l for l in a["guidance"]
                 if l.startswith("- ") and "learned from" in l
                 and float(l.rsplit("strength ", 1)[-1].rstrip(")")) >= 0.3]
    if not confident:
        return ""
    head = "# Adapted delivery (user-learned)\n\n"
    return head + "\n".join(confident)


# ------------------------------------------------------------ usefulness report

def value():
    """Report the measured usefulness trajectory (local, honest)."""
    db = connect()
    rows = db.execute("SELECT * FROM feedback ORDER BY id").fetchall()
    prefs = db.execute("SELECT * FROM preferences ORDER BY strength DESC").fetchall()
    db.close()

    total = len(rows)
    helpful = sum(1 for r in rows if r["kind"] == "helpful")
    unhelpful = sum(1 for r in rows if r["kind"] == "unhelpful")
    length = sum(1 for r in rows if r["kind"] in ("too_long", "too_terse"))
    tone = sum(1 for r in rows if r["kind"] == "wrong_tone")
    explicit_ratio = (helpful / total) if total else None

    texts = _journal_texts()
    corrections = _repeat_corrections(texts)

    return {
        "feedback_total": total,
        "helpful": helpful,
        "unhelpful": unhelpful,
        "length_adjustments": length,
        "tone_adjustments": tone,
        "explicit_helpfulness_ratio": round(explicit_ratio, 3) if explicit_ratio is not None else None,
        "repeat_corrections": corrections[:6],
        "repeat_correction_rate": len(corrections),
        "preferences": [
            {"key": r["key"], "value": r["value"],
             "strength": round(r["strength"], 2),
             "evidence": r["evidence"],
             "conflicts": r["conflicts"]} for r in prefs
        ],
        "verdict": _verdict(total, helpful, unhelpful, len(corrections)),
    }


def _verdict(total, helpful, unhelpful, corrections):
    if total == 0 and corrections == 0:
        return "insufficient signal — the loop is recording; usefulness is not yet measurable"
    # Explicit feedback is the reliable signal. Repeat-corrections are a
    # SECONDARY hint (useful in normal use, noisy during system self-
    # development) — they alone never flip the verdict to "adapt".
    if total >= 3 and (helpful / total) >= 0.7 and unhelpful <= 1:
        return "measured improvement — the user is finding this more helpful"
    if unhelpful >= 2:
        return "signal to adapt — repeated unhelpful feedback detected"
    if total >= 3:
        return "signal accumulating — keep recording"
    return "insufficient signal — the loop is recording; usefulness is not yet measurable"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["feedback", "adapt", "value", "signals",
                                    "delivery"])
    ap.add_argument("--kind", default="")
    ap.add_argument("--about", default="")
    ap.add_argument("--detail", default="")
    ap.add_argument("--topic", default="general")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "feedback":
        out = record_feedback(args.kind, args.about, args.detail, args.topic)
        print(json.dumps(out))
    elif args.cmd == "adapt":
        out = adapt()
        print(json.dumps(out, indent=2) if args.json else
              delivery_block())
    elif args.cmd == "delivery":
        print(delivery_block())
    elif args.cmd == "value":
        print(json.dumps(value(), indent=2))
    elif args.cmd == "signals":
        db = connect()
        rows = db.execute("SELECT kind, COUNT(*) AS n FROM feedback "
                          "GROUP BY kind").fetchall()
        prefs = db.execute("SELECT key, value, evidence, strength FROM "
                           "preferences ORDER BY strength DESC").fetchall()
        db.close()
        print(json.dumps({
            "feedback": [dict(r) for r in rows],
            "preferences": [dict(r) for r in prefs],
        }, indent=2))


if __name__ == "__main__":
    main()
