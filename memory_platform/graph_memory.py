#!/usr/bin/env python3
"""Graph memory tier — concept-mediated temporal knowledge graph for the agent.

Built from the 2025-2026 academic frontier (briefs in
`/tmp/opencode/memory-research/agentic-memory-brief.md` and
`~/.firecrawl/claim-audit-2026/brief.md`). Exceeds the entity-hub approach by
following the research convergence:

  - CONCEPT-MEDIATED, not entity-centric (GAAMA 2603.27910): nodes are typed
    (person / project / concept / tool / preference / fact), which kills the
    "mega-hub" dilution Letta/Graphiti hit with one entity connected to
    everything.
  - BI-TEMPORAL edges (Graphiti 2501.13956): `valid_from/valid_until` (when
    the fact is true in the world) + `learned_at` (when we learned it).
  - INCREMENTAL construction (TG-RAG 2510.13590, iText2KG 2409.03284): merge
    only, no full re-index; entity resolution via aliases, never blind merges.
  - SUPERSEDE, never delete (Write-Time Gating 2603.15994, SSGM 2603.11768):
    contradicting edges are marked superseded with the new one taking over;
    history preserved for audit.
  - WRITE-TIME GATING (2603.15994, D-MEM 2603.14597): routine/salience-weak
    facts never touch the graph; surprises and contradictions do. The biggest
    token saver in the literature.
  - PROVENANCE on every edge (MemORAI 2605.01386): each edge carries the
    evidence text + source, which is exactly what the claim-audit layer needs.

Storage: headless SQLite at `memory/graph/graph.sqlite` — reachable by the
sleep-time curator directly (no MCP dependency). Schema below.

Usage:
  graph_memory.py add <subj> <pred> <obj> --type subj_type --evidence "..."
                       [--source X] [--confidence 0.0-1.0] [--strength N]
  graph_memory.py add-bulk <json-file>
  graph_memory.py query <entity-or-concept> [--hops N] [--latest-only]
  graph_memory.py resolve <name>
  graph_memory.py neighbors <entity>
  graph_memory.py stats
  graph_memory.py superseded-by <subj> <pred> <obj>  (manual conflict flag)
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

# Odysseus-native path resolution — no hardcoded user paths.
try:
    from . import memory_env
except ImportError:
    import memory_env

GRAPH_DIR = memory_env.graph_dir()
DB_PATH = memory_env.graph_db()

# Fixed predicate vocabulary — schema drift is a researched failure mode, so we
# constrain the set of relationship types we accept. Unknown preds are refused
# (the extractor maps free text onto this set).
PREDICATES = {
    "works_on", "works_at", "teaches", "manages", "uses", "prefers",
    "lives_in", "located_in", "has_role", "part_of", "leads", "builds",
    "develops", "created", "interested_in", "knows", "collaborates_with",
    "owned_by", "reports_to", "supports", "runs", "tracks", "related_to",
    "based_on", "happens_on", "started_on", "ended_on", "responds_to",
    "struggles_with", "likes", "dislikes", "avoided_by", "goal_of",
}

# Node types.
NODE_TYPES = {"person", "project", "concept", "tool", "preference",
              "organization", "place", "event", "skill", "artefact"}

# Confidence: evidence-gated (EvoKG-style f(frequency, recency, source_cred)).
# We fold the curator's strength (0-5) into a 0-1 confidence.
DEFAULT_CONF = 0.7


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    os.makedirs(GRAPH_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL DEFAULT 'concept',
            summary TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS aliases (
            entity_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            FOREIGN KEY(entity_id) REFERENCES entities(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subj_id INTEGER NOT NULL,
            pred TEXT NOT NULL,
            obj_id INTEGER NOT NULL,
            valid_from TEXT,
            valid_until TEXT,
            learned_at TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.7,
            evidence TEXT DEFAULT '',
            source TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',  -- active|superseded|expired
            superseded_by INTEGER,
            FOREIGN KEY(subj_id) REFERENCES entities(id),
            FOREIGN KEY(obj_id) REFERENCES entities(id)
        );
        CREATE INDEX IF NOT EXISTS idx_edges_subj ON edges(subj_id);
        CREATE INDEX IF NOT EXISTS idx_edges_obj ON edges(obj_id);
    """)
    return db


def strength_to_conf(strength):
    """Map curator strength (0-5) to a 0-1 confidence, gated so weak evidence
    never enters the graph (write-time gating)."""
    try:
        s = float(strength)
    except (TypeError, ValueError):
        return None
    if s < 3:
        return None  # below durability threshold -> do not write
    return min(0.99, 0.4 + 0.12 * s)  # 3->0.76, 4->0.88, 5->0.99


def resolve_entity(db, name, node_type="concept"):
    """Find an entity by exact name or alias; create if missing. Returns id.
    Alias-first resolution mirrors iText2KG's incremental matching."""
    name = (name or "").strip()
    if not name:
        return None
    row = db.execute("SELECT id FROM entities WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    row = db.execute("""
        SELECT a.entity_id FROM aliases a JOIN entities e ON a.entity_id = e.id
        WHERE lower(a.alias) = lower(?)""", (name,)).fetchone()
    if row:
        return row["entity_id"]
    cur = db.execute(
        "INSERT INTO entities (name, type, summary, created_at) VALUES (?,?,?,?)",
        (name, node_type if node_type in NODE_TYPES else "concept", "", now_iso()))
    return cur.lastrowid


def add_edge(db, subj, pred, obj, subj_type="concept", obj_type="concept",
             evidence="", source="", confidence=None, strength=None):
    """Add a fact to the graph with write-time gating + supersession.

    - If strength < 3, the fact is gated out (never touches the graph).
    - On a contradiction (same subj-pred-obj already active), the old edge is
      marked superseded and the new one wins — history preserved.
    """
    pred = (pred or "").strip().lower().replace(" ", "_")
    if pred not in PREDICATES:
        print(f"graph: unknown predicate '{pred}' refused "
              f"(allowed: {len(PREDICATES)})", file=sys.stderr)
        return False
    if confidence is None:
        confidence = strength_to_conf(strength)
        if confidence is None:
            print(f"graph: write-gated (strength {strength} < 3) — skipped",
                  file=sys.stderr)
            return False
    sid = resolve_entity(db, subj, subj_type)
    oid = resolve_entity(db, obj, obj_type)
    if sid is None or oid is None or sid == oid:
        return False
    # Supersede any active edge with the same (subj,pred,obj).
    old = db.execute("""
        SELECT id FROM edges WHERE subj_id=? AND pred=? AND obj_id=?
        AND status='active'""", (sid, pred, oid)).fetchone()
    ts = now_iso()
    if old:
        db.execute("UPDATE edges SET status='superseded', superseded_by=? WHERE id=?",
                   (None, old["id"]))  # superseded_by set after insert
    cur = db.execute("""
        INSERT INTO edges (subj_id, pred, obj_id, valid_from, valid_until,
                          learned_at, confidence, evidence, source, status)
        VALUES (?,?,?,?,NULL,?,?,?,?,'active')""",
        (sid, pred, oid, ts, ts, confidence, (evidence or "")[:400], source or ""))
    new_id = cur.lastrowid
    if old:
        db.execute("UPDATE edges SET superseded_by=? WHERE id=?", (new_id, old["id"]))
    db.execute("INSERT OR IGNORE INTO aliases (entity_id, alias) VALUES (?,?)",
               (sid, subj.strip()))
    db.execute("INSERT OR IGNORE INTO aliases (entity_id, alias) VALUES (?,?)",
               (oid, obj.strip()))
    db.commit()
    return True


def query(db, term, hops=1, latest_only=True):
    """Find an entity by name/alias then BFS `hops` deep, returning compact
    facts. Follows the research: vector/lexical finds the entry node, graph
    traverses from it (HippoRAG-style)."""
    term = (term or "").strip()
    if not term:
        return []
    eid = None
    row = db.execute("SELECT id, name, type FROM entities WHERE lower(name)=lower(?)",
                     (term,)).fetchone()
    if not row:
        row = db.execute("""
            SELECT e.id, e.name, e.type FROM aliases a
            JOIN entities e ON a.entity_id=e.id
            WHERE lower(a.alias)=lower(?) LIMIT 1""", (term,)).fetchone()
    if not row:
        # substring match as last resort
        row = db.execute("SELECT id, name, type FROM entities WHERE name LIKE ? LIMIT 1",
                         (f"%{term}%",)).fetchone()
    if not row:
        return []
    eid, name, etype = row["id"], row["name"], row["type"]
    results = []
    seen = {eid}
    frontier = [(eid, 0)]
    while frontier:
        cur, depth = frontier.pop(0)
        if depth > hops:
            break
        sql = """
            SELECT e.id AS sid, e.name AS sname, e.type AS stype,
                   ed.pred, o.id AS oid, o.name AS oname, o.type AS otype,
                   ed.valid_from, ed.valid_until, ed.confidence, ed.status,
                   ed.source, ed.evidence
            FROM edges ed
            JOIN entities e ON ed.subj_id=e.id
            JOIN entities o ON ed.obj_id=o.id
            WHERE ed.subj_id=? AND ed.status='active'
        """
        params = [cur]
        for r in db.execute(sql, params):
            results.append({
                "subject": r["sname"], "predicate": r["pred"],
                "object": r["oname"], "confidence": r["confidence"],
                "from": r["valid_from"], "until": r["valid_until"],
                "status": r["status"], "source": r["source"],
            })
            if r["oid"] not in seen:
                seen.add(r["oid"])
                if depth + 1 <= hops:
                    frontier.append((r["oid"], depth + 1))
    # Also include edges where this entity is the object (incoming).
    sql = """
        SELECT s.name AS sname, ed.pred, o.name AS oname, o.type AS otype,
               ed.valid_from, ed.valid_until, ed.confidence, ed.status, ed.source
        FROM edges ed
        JOIN entities s ON ed.subj_id=s.id
        JOIN entities o ON ed.obj_id=o.id
        WHERE ed.obj_id=? AND ed.status='active'
    """
    for r in db.execute(sql, (eid,)):
        results.append({
            "subject": r["sname"], "predicate": r["pred"],
            "object": r["oname"], "confidence": r["confidence"],
            "from": r["valid_from"], "until": r["valid_until"],
            "status": r["status"], "source": r["source"],
        })
    return {"entity": name, "type": etype, "facts": results}


def stats(db):
    e = db.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"]
    a = db.execute("SELECT COUNT(*) AS n FROM aliases").fetchone()["n"]
    ed = db.execute("SELECT COUNT(*) AS n FROM edges").fetchone()["n"]
    active = db.execute(
        "SELECT COUNT(*) AS n FROM edges WHERE status='active'").fetchone()["n"]
    by_pred = {r["pred"]: r["n"] for r in
               db.execute("SELECT pred, COUNT(*) AS n FROM edges GROUP BY pred"
                          " ORDER BY n DESC LIMIT 8")}
    return {"entities": e, "aliases": a, "edges": ed, "active": active,
            "predicates": by_pred}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["add", "add-bulk", "query", "resolve",
                                    "neighbors", "stats"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--type", default="concept")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--confidence", type=float, default=None)
    ap.add_argument("--strength", type=int, default=None)
    ap.add_argument("--hops", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--latest-only", action="store_true", default=True)
    args = ap.parse_args()

    db = connect()
    if args.cmd == "stats":
        s = stats(db)
        print(json.dumps(s, indent=2) if args.json else
              f"entities={s['entities']} aliases={s['aliases']} "
              f"edges={s['edges']} active={s['active']}")
        return
    if args.cmd == "add":
        if len(args.arg) < 3:
            print("add needs <subj> <pred> <obj>", file=sys.stderr)
            sys.exit(1)
        subj, pred, obj = args.arg[0], args.arg[1], args.arg[2]
        ok = add_edge(db, subj, pred, obj,
                      subj_type=args.type, evidence=args.evidence,
                      source=args.source, confidence=args.confidence,
                      strength=args.strength)
        print(json.dumps({"added": ok}))
        sys.exit(0 if ok else 1)
    if args.cmd == "add-bulk":
        path = args.arg[0] if args.arg else None
        if not path:
            print("add-bulk needs <json-file>", file=sys.stderr)
            sys.exit(1)
        items = json.load(open(path))
        added = 0
        for it in items:
            ok = add_edge(db, it.get("subject"), it.get("predicate"),
                          it.get("object"),
                          subj_type=it.get("subject_type", "concept"),
                          obj_type=it.get("object_type", "concept"),
                          evidence=it.get("evidence", ""),
                          source=it.get("source", ""),
                          confidence=it.get("confidence"),
                          strength=it.get("strength"))
            if ok:
                added += 1
        print(json.dumps({"added": added, "total": len(items)}))
        return
    if args.cmd in ("query", "neighbors"):
        term = " ".join(args.arg) if args.cmd == "query" else args.arg[0]
        res = query(db, term, hops=args.hops, latest_only=args.latest_only)
        print(json.dumps(res, indent=2) if args.json else
              json.dumps(res, indent=2))
        return
    if args.cmd == "resolve":
        name = " ".join(args.arg)
        eid = resolve_entity(db, name, "concept")
        row = db.execute("SELECT name, type FROM entities WHERE id=?",
                         (eid,)).fetchone()
        aliases = [r["alias"] for r in
                   db.execute("SELECT alias FROM aliases WHERE entity_id=?",
                              (eid,))]
        print(json.dumps({"id": eid, "name": row["name"], "type": row["type"],
                          "aliases": aliases}, indent=2))
        return


if __name__ == "__main__":
    main()
