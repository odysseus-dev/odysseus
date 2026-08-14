#!/usr/bin/env python3
"""memory_compiler.py — the NEW schema's core compiler.

The old schema stored the five core blocks as bounded markdown files. The new
schema makes the STORE the source of truth: atomic entries with metadata,
including an ALWAYS-ON core (constitution, safety, core identity, operating
essentials) flagged `always_on=1` + `priority`.

This compiler assembles the always-in-context `<memory_blocks>` XML from the
store, replacing the markdown block files. It returns the same structure the
memory plugin used to read from files — so the plugin's job becomes "call the
compiler", not "read markdown".

Compilation order (priority high -> low, always_on first):
  constitution (read_only)  — inviolable, injected first
  persona identity          — core values, evidence-receipted
  safety constraints        — non-negotiable
  operating essentials      — how I work with the user
  human/project facts       — always-on core facts

On-demand (non-always_on) entries are NOT compiled here — they're retrieved
by memory_recall when relevant, keeping the always-on context slim.

Usage:
  memory_compiler.py compile            # emit the <memory_blocks> XML
  memory_compiler.py core               # list the always-on core entries
  memory_compiler.py migrate            # (one-time) build store core from old blocks
"""

import argparse
import json
import os
import sqlite3

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

STORE_PY = memory_env.python_bin()
STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_store.py")
DB = memory_env.store_db()

# Compile order by topic (constitution first, then identity, safety, operating).
# delivery (the voice register) compiles FIRST — it is the primary framing the
# model reads, before the constitution, so the personality is salient.
ORDER = ["delivery", "constitution", "identity", "safety", "operating", "human", "project"]


def connect():
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def core_entries(db):
    """The always-on core: entries flagged always_on, ordered by priority then
    topic order."""
    rows = db.execute(
        "SELECT id, text, topic, importance, priority, confidence, "
        "source FROM entries WHERE always_on=1 AND status='active' "
        "ORDER BY priority ASC").fetchall()
    # Stable sort by topic order (constitution first), then priority.
    def key(r):
        t = r["topic"] or ""
        try:
            ti = ORDER.index(t)
        except ValueError:
            ti = len(ORDER)
        return (ti, r["priority"] or 5)
    return sorted(rows, key=key)


def compile_core():
    """Assemble the <memory_blocks> XML from the store's always-on core
    entries (new schema — atomic entries, not markdown block files).

    BUDGET-AWARE (the fix for "the influence isn't in responses"): the
    always-on core was 31K chars and drowned the voice. Now each TOPIC emits
    ONE compact block:
      - FULL FIDELITY for the VOICE and the INVOLABLES: delivery (the
        register the voice lives in), identity, constitution, safety — small,
        must surface verbatim.
      - AAAK-COMPRESSED, CAPPED for the large masses: persona, operating,
        project, human — one block per topic holding the top-N entries as
        zettels, so substance is present without flooding context.
    The persona voice stays visibly in every response; bulk facts stay
    recallable (never forgotten, just not saturating the prompt)."""
    db = connect()
    entries = core_entries(db)
    db.close()
    if not entries:
        return ("<memory_blocks>\n  (core not yet migrated — run "
                "`memory_compiler.py migrate`)\n</memory_blocks>")
    # RESIDENT SET (LightMem short-term store; Liu et al. "Lost in the Middle"):
    # only the VOICE + INVOLABLES are always-on and compiled FIRST (beginning =
    # highest model attention). Persona, operating, project, human are NOT
    # resident — they fire via the warm neuron tier on relevance (LightMem's
    # active on-demand retrieval), so the context stays small and the middle
    # is never flooded.
    RESIDENT = ("delivery", "identity", "constitution", "safety")

    parts = []
    for label in RESIDENT:
        rows = [r for r in entries if (r["topic"] or "memory").lower() == label]
        if not rows:
            continue
        desc = {
            "delivery": "The voice register — how the agent delivers substance (composed, dry, wry)",
            "constitution": "Inviolable rules, positive-affirmation framed",
            "identity": "Core identity values (evidence-grounded)",
            "safety": "Non-negotiable safety constraints",
        }.get(label, "Memory")
        bodies = [f"- {r['text']}" for r in rows]
        meta = f"read_only={1 if label=='constitution' else 0}"
        parts.append(
            f"<{label}>\n<description>\n{desc}\n</description>\n"
            f"<metadata>\n- {meta}\n</metadata>\n"
            f"<value>\n" + ("\n".join(bodies) or "(none)") + "\n</value>\n</{label}>"
        )
    # Note for the agent: the non-resident tiers (persona, operating, project,
    # human) are served by the active warm-neuron tier + memory_recall — they
    # are NOT pre-loaded, so the resident set stays small.
    note = ("<note>\nThe resident set above is always-on (voice + inviolables). "
            "Persona, operating, project and human material is NOT pre-loaded — "
            "it fires through the warm-neuron tier and memory_recall when "
            "relevant, so context stays small and focused.\n</note>")
    return ("<memory_blocks>\nThe following core memory entries are engaged in "
            "your core memory unit (compiled from the store):\n\n"
            + "\n\n".join(parts) + "\n\n" + note + "\n\n</memory_blocks>")


_summary_cache = {}


def _summary_for(label, text):
    """AAAK-compress a core entry body (the compression route). Cached."""
    if len(text) <= 300:
        return ""  # short entries stay verbatim — not worth compressing
    if text in _summary_cache:
        return _summary_cache[text]
    try:
        import aaak
        summ = aaak.compress(text, topic=label)
    except Exception:
        summ = ""
    _summary_cache[text] = summ
    return summ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["compile", "core", "migrate"])
    ap.add_argument("arg", nargs="*", default=[])
    args = ap.parse_args()

    if args.cmd == "compile":
        print(compile_core())
    elif args.cmd == "core":
        db = connect()
        rows = core_entries(db)
        db.close()
        print(f"{len(rows)} always-on core entries:")
        for r in rows:
            print(f"  [{r['topic'] or 'memory':12s} p{r['priority'] if r['priority'] is not None else 5}] "
                  f"{r['text'][:60]}")
    elif args.cmd == "migrate":
        print("migration handled by migrate_blocks.py (one-time, from old blocks)")


if __name__ == "__main__":
    main()