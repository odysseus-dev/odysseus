#!/usr/bin/env python3
"""warm_neuron_store.py — the ONE store-based reader for warm neurons.

The old schema stored warm blocks as markdown files in memory/warm/. That is
retired (.pasture). Warm neurons now live in the store as kind='neuron'
entries. EVERY consumer must read through this module — never mention the old
markdown files again.

Provides:
  list_neurons()      -> [{slug, triggers, text, importance, ...}]
  neuron_has(term)    -> True if any active neuron's text/triggers mention term
  neuron_digests()    -> {slug: text} (the fired-body view)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from . import memory_env
except ImportError:
    import memory_env

STORE_DB = memory_env.store_db()


def _db():
    import sqlite3
    return sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)


def list_neurons():
    """All active warm neurons from the store (kind='neuron')."""
    out = []
    try:
        db = _db()
        rows = db.execute(
            "SELECT text, slug, triggers, importance FROM entries "
            "WHERE kind='neuron' AND status='active' ORDER BY slug"
        ).fetchall()
        db.close()
    except Exception:
        return out
    for text, slug, triggers, importance in rows:
        out.append({
            "slug": slug or "warm",
            "triggers": [t.strip().lower() for t in (triggers or "").split(",")
                         if t.strip()],
            "importance": float(importance or 0.5),
            "text": (text or "").strip(),
        })
    return out


def neuron_has(term):
    """True if any active neuron's body or triggers mention `term`."""
    term = (term or "").lower()
    if not term:
        return False
    for n in list_neurons():
        if term in n["text"].lower():
            return True
        if any(term in t for t in n["triggers"]):
            return True
    return False


def neuron_digests():
    """{slug: body} for all active neurons (what gets fired into context)."""
    return {n["slug"]: n["text"] for n in list_neurons()}


def neuron_slugs():
    return sorted(n["slug"] for n in list_neurons())
