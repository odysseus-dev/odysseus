#!/usr/bin/env python3
"""migrate_blocks.py — one-time migration from old markdown blocks to the store.

Reads the five old markdown block files and writes their content into the
store as atomic entries with the NEW schema:
  - constitution rules -> always_on, topic=constitution, priority 0
  - persona identity values -> always_on, topic=identity, priority 1
  - persona safety constraints -> always_on, topic=safety, priority 0
  - operating rules -> always_on, topic=operating, priority 2
  - human/project facts -> always_on, topic=human/project, priority 3

After migration, the store is the source of truth. The old .md files are
renamed to .md.pasture (retired, not deleted) so nothing is lost.

Usage:
  migrate_blocks.py                # run the migration (idempotent)
  migrate_blocks.py --dry-run      # show what would be migrated
"""

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

STORE_PY = memory_env.python_bin()
STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_store.py")
BLOCKS_DIR = os.path.join(memory_env.memory_dir(), "blocks")
DB = memory_env.store_db()

# topic -> (priority, description)
TOPIC_PRIORITY = {
    "constitution": 0,
    "safety": 0,
    "identity": 1,
    "operating": 2,
    "human": 3,
    "project": 3,
}


def read_block(label):
    path = os.path.join(BLOCKS_DIR, f"{label}.md")
    if not os.path.exists(path):
        return None
    raw = open(path).read()
    body = raw.split("---", 2)[2] if raw.startswith("---") else raw
    return body


def extract_entries(label, body):
    """Parse a block body into atomic entries.

    - constitution: numbered inviolables (1. **...**) + growth register lines.
    - persona: identity section bullets, safety constraints, persona contract.
    - operating: bullet rules.
    - human/project: bullets.
    Returns list of (text, topic, priority, always_on).
    """
    entries = []
    if label == "constitution":
        # Numbered inviolables "1. **...**" and growth-register "- ..." lines.
        for m in re.finditer(r"^\s*(\d+)\.\s+\*\*(.+?)\*\*", body, re.M):
            entries.append((m.group(2).strip(), "constitution", 0, 1))
        # Growth register entries (explicit directives).
        in_register = False
        for line in body.splitlines():
            if "## Growth register" in line:
                in_register = True
                continue
            if in_register and line.strip().startswith("- "):
                entries.append((line.strip()[2:].strip(), "constitution", 0, 1))
    elif label == "persona":
        # Safety constraints -> topic safety (non-negotiable, always_on).
        safety = []
        if "## SAFETY CONSTRAINTS" in body:
            sec = body.split("## SAFETY CONSTRAINTS", 1)[1]
            sec = sec.split("## ", 1)[0]
            safety.append(sec.strip())
        if safety:
            entries.append((safety[0], "safety", 0, 1))
        # Identity values -> topic identity.
        if "## Identity" in body:
            sec = body.split("## Identity", 1)[1]
            for line in sec.splitlines():
                l = line.strip()
                if l.startswith("- ") and len(l) > 15:
                    entries.append((l[2:].strip(), "identity", 1, 1))
    elif label == "operating":
        for line in body.splitlines():
            l = line.strip()
            if l.startswith("- ") and len(l) > 15:
                entries.append((l[2:].strip(), "operating", 2, 1))
    elif label in ("human", "project"):
        for line in body.splitlines():
            l = line.strip()
            if l.startswith("- ") and len(l) > 15:
                entries.append((l[2:].strip(), label, 3, 1))
    return entries


def migrate(dry_run=False):
    results = {}
    for label in ("constitution", "persona", "human", "operating", "project"):
        body = read_block(label)
        if body is None:
            results[label] = {"entries": 0, "status": "no block file"}
            continue
        entries = extract_entries(label, body)
        results[label] = {"entries": len(entries)}
        if dry_run:
            results[label]["status"] = "would migrate"
            continue
        # Write to the store as always-on entries.
        db = sqlite3.connect(DB)
        for text, topic, prio, aon in entries:
            db.execute(
                "INSERT OR IGNORE INTO entries (text, importance, created_at, "
                "last_accessed, topic, source, method, status, valid_from, "
                "confidence, temperature, always_on, priority) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (text, 0.9, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 topic, "migration", "migrate_blocks", "active",
                 datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 0.9, 1.0, aon, prio))
        db.commit()
        db.close()
        results[label]["status"] = "migrated"
    return results


def retire_blocks():
    """Rename old .md block files to .md.pasture (retired, not deleted)."""
    retired = []
    for label in ("constitution", "persona", "human", "operating", "project"):
        src = os.path.join(BLOCKS_DIR, f"{label}.md")
        dst = os.path.join(BLOCKS_DIR, f"{label}.md.pasture")
        if os.path.exists(src) and not os.path.exists(dst):
            os.rename(src, dst)
            retired.append(label)
    return retired


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    results = migrate(dry_run=args.dry_run)
    for label, r in results.items():
        print(f"  {label:12s} {r['entries']} entries -> {r.get('status', '?')}")
    if not args.dry_run:
        retired = retire_blocks()
        print(f"\nretired old block files: {retired or 'none (already retired)'}")
        print("store is now the source of truth; memory_compiler.py compiles the core")


if __name__ == "__main__":
    main()