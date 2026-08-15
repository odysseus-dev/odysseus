#!/usr/bin/env python3
"""source_registry.py — the ADOPTION CONTRACT for every absorbed source.

The principle (user-approved): **explicit adoption, automatic usefulness.**

Every absorbed source gets a record with three orthogonal channels:

  know  (AUTOMATIC, always)  — retrievable, referenceable, recitable, usable.
                               Any absorption immediately makes the material
                               useful. Default and floor for everything.
  speak (EXPLICIT only)      — shapes the delivery/voice. Locked behind an
                               explicit directive ("absorb <person>'s manner").
  be    (EXPLICIT + gated)   — shapes identity/worldview. Locked behind an
                               explicit directive ("absorb <person>'s
                               worldview") AND the worthiness gate.

The rule that fixes Delta Green: mining/game-rule material is `know` only — it
is retrievable and referenceable but can NEVER enter speech or identity unless
the user explicitly adopts it as an influence. A book is a resource, not a
self.

  - fiction is eligible for speak/be at the VALUES level (stories shape people,
    it is human), but never at the FACT level — the epistemology gate still
    blocks any claimed fact from a fictional world (Cthulhu is not real).
  - usefulness is automatic: `know` triggers a competence pass so the material
    is recitable/applicable, not just stored.

Storage: a `sources` table in the store (id, name, provenance, adoption json).
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_env

STORE_DB = memory_env.store_db()


def connect():
    db = sqlite3.connect(STORE_DB)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            provenance TEXT DEFAULT '',      -- where it came from (book/person/game)
            kind TEXT DEFAULT 'resource',     -- influence | resource
            adoption TEXT DEFAULT '{"know":true,"speak":false,"be":false}',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    db.commit()
    return db


def register(name, provenance="", kind="resource", speak=False, be=False):
    """Register an absorbed source with its adoption contract.

    Default (kind=resource): know=true, speak=false, be=false.
    kind=influence (explicit directive): may enable speak/be.
    """
    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    adoption = {"know": True, "speak": bool(speak), "be": bool(be)}
    db = connect()
    row = db.execute("SELECT id FROM sources WHERE name=?", (name,)).fetchone()
    if row:
        db.execute(
            "UPDATE sources SET provenance=?, kind=?, adoption=?, updated_at=? "
            "WHERE id=?",
            (provenance, kind, json.dumps(adoption), ts, row["id"]))
        sid = row["id"]
    else:
        cur = db.execute(
            "INSERT INTO sources (name, provenance, kind, adoption, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (name, provenance, kind, json.dumps(adoption), ts, ts))
        sid = cur.lastrowid
    db.commit()
    db.close()
    return {"id": sid, "name": name, "kind": kind, "adoption": adoption}


def adopt(name, speak=None, be=None, provenance=None, kind=None):
    """Explicitly adopt a source as an influence (the user directive).

    Only this function enables speak/be. Nothing else promotes a source into
    the voice or identity.
    """
    db = connect()
    row = db.execute("SELECT * FROM sources WHERE name=?", (name,)).fetchone()
    if not row:
        db.close()
        return {"ok": False, "reason": f"no source '{name}' registered"}
    adoption = json.loads(row["adoption"])
    if speak is not None:
        adoption["speak"] = bool(speak)
    if be is not None:
        adoption["be"] = bool(be)
    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if kind and kind in ("influence", "resource"):
        db.execute("UPDATE sources SET kind=?, adoption=?, updated_at=? WHERE id=?",
                   (kind, json.dumps(adoption), ts, row["id"]))
    else:
        db.execute("UPDATE sources SET adoption=?, updated_at=? WHERE id=?",
                   (json.dumps(adoption), ts, row["id"]))
    db.commit()
    db.close()
    return {"ok": True, "name": name, "adoption": adoption}


def get(name):
    db = connect()
    row = db.execute("SELECT * FROM sources WHERE name=?", (name,)).fetchone()
    db.close()
    if not row:
        return None
    return {"id": row["id"], "name": row["name"],
            "provenance": row["provenance"], "kind": row["kind"],
            "adoption": json.loads(row["adoption"])}


def can_speak(name):
    """Can this source influence the delivery/voice? (explicit speak only)"""
    s = get(name)
    return bool(s and s["adoption"].get("speak"))


def can_be(name):
    """Can this source influence identity/worldview? (explicit be + gated)"""
    s = get(name)
    return bool(s and s["adoption"].get("be"))


def speaks():
    """All sources with speak enabled (what may shape the voice)."""
    db = connect()
    rows = db.execute("SELECT * FROM sources").fetchall()
    db.close()
    return [{"name": r["name"], "adoption": json.loads(r["adoption"])}
            for r in rows if json.loads(r["adoption"]).get("speak")]


def all_sources():
    db = connect()
    rows = db.execute("SELECT * FROM sources ORDER BY name").fetchall()
    db.close()
    return [{"id": r["id"], "name": r["name"], "provenance": r["provenance"],
             "kind": r["kind"], "adoption": json.loads(r["adoption"])}
            for r in rows]


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["register", "adopt", "list", "speak"])
    ap.add_argument("name", nargs="?", default="")
    ap.add_argument("--provenance", default="")
    ap.add_argument("--kind", default="resource",
                    choices=["resource", "influence"])
    ap.add_argument("--speak", action="store_true")
    ap.add_argument("--be", action="store_true")
    args = ap.parse_args()
    if args.cmd == "register":
        out = register(args.name, args.provenance, args.kind,
                       speak=args.speak, be=args.be)
        print(json.dumps(out, indent=2))
    elif args.cmd == "adopt":
        out = adopt(args.name, speak=args.speak, be=args.be, kind=args.kind)
        print(json.dumps(out, indent=2))
    elif args.cmd == "speak":
        print(json.dumps(speaks(), indent=2))
    else:
        print(json.dumps(all_sources(), indent=2))


if __name__ == "__main__":
    main()
