#!/usr/bin/env python3
"""Add an entry to the constitution — INVARIANT: this script exists solely so
the agent can record an explicit user directive ('always do X', 'add this to
the constitution'). It is the ONLY path that writes to the constitution.

NEW SCHEMA (2026-08-12): the constitution now lives in the STORE as atomic
entries (topic=constitution, always_on=1, priority=0), compiled into context
by memory_compiler.py — not in a markdown file. The old constitution.md was
retired to .pasture.

Usage:
  python3 constitution-add.py "<positive affirmation text>" "<source directive>"

The directive text is stored as given (the agent is expected to pass the
positive-affirmation form it derived from the user's exact words). Every write is
logged to the growth journal with the original directive, so the expansion is
always auditable. If the entry already exists, nothing is written.
"""
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

STORE_PY = memory_env.python_bin()
STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_store.py")
DB = memory_env.store_db()
JOURNAL_DIR = os.path.join(memory_env.memory_dir(), "journal")


def norm(text):
    return " ".join(text.lower().replace("-", " ").split())


def add_to_store(affirmation, directive):
    """Write the directive to the store as an always-on constitution entry.
    Idempotent: if the affirmation already exists, nothing is written."""
    db = sqlite3.connect(DB)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = db.execute(
        "SELECT 1 FROM entries WHERE topic='constitution' AND always_on=1").fetchall()
    existing_texts = {norm(r[0]) for r in
                      db.execute("SELECT text FROM entries WHERE "
                                 "topic='constitution' AND always_on=1")}
    if norm(affirmation) in existing_texts:
        db.close()
        return "already present; nothing written"
    # Record the affirmation with the source directive in the text so
    # provenance survives.
    entry_text = (f"{affirmation} (_source:_ \"{directive}\", "
                  f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')})")
    db.execute(
        "INSERT INTO entries (text, importance, created_at, last_accessed, "
        "topic, entities, source, method, status, valid_from, confidence, "
        "temperature, always_on, priority) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (entry_text, 1.0, ts, ts, "constitution", "[]", "constitution-add",
         "directive", "active", ts, 1.0, 1.0, 1, 0))
    db.commit()
    db.close()
    # Journal the directive -> affirmation pair.
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    jpath = os.path.join(JOURNAL_DIR, f"{month}.md")
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    ts2 = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(jpath, "a") as f:
        f.write(f"`{ts2}` **CONSTITUTION-ADD** → `constitution` (store schema)\n")
        f.write(f"  - directive: {directive}\n")
        f.write(f"  - affirmation: {affirmation}\n")
        f.write(f"  - source: explicit user directive\n\n")
    return f"added to constitution (store) + journaled: {affirmation}"


def main():
    if len(sys.argv) < 3:
        print("usage: constitution-add.py <affirmation> <source directive>",
              file=sys.stderr)
        sys.exit(1)
    affirmation = sys.argv[1].strip()
    directive = sys.argv[2].strip()
    if not affirmation or not directive:
        print("empty argument; refusing", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(DB):
        print(f"store missing: {DB}; refusing", file=sys.stderr)
        sys.exit(1)
    print(add_to_store(affirmation, directive))


if __name__ == "__main__":
    main()