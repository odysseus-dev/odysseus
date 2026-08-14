#!/usr/bin/env python3
"""system_status.py — one-command health report for the whole memory system.

Consolidates the six health signals that previously required separate commands
into a single fast report:

  tier  graph      — entities/edges/active triples + write-gating health
  tier  warm       — neurons, fires recorded, token-budget efficiency
  tier  identity   — persona Identity section size + candidate accumulation
  guard drift      — drift-ledger check (0 = clean, 1 = drift flagged)
  guard claim      — claim-audit journal tallies (PASS/DEGRADE counts)
  verify canary    — quick spot-checks that don't need the full suite

Usage:
  python3 system_status.py           # full report
  python3 system_status.py --quick   # just the status line (for scripts/TUI)
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

MEM_DIR = memory_env.memory_dir()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GRAPH_DB = os.path.join(MEM_DIR, "graph", "graph.sqlite")
LEDGER = os.path.join(MEM_DIR, "index", "drift_ledger.json")
CLAIM_JOURNAL = os.path.join(MEM_DIR, "index", "claim_audit.jsonl")
STORE_PY = memory_env.python_bin()
STORE_DB = memory_env.store_db()


def graph_stats():
    if not os.path.exists(GRAPH_DB):
        return {"ok": False, "error": "no graph DB"}
    db = sqlite3.connect(GRAPH_DB)
    try:
        e = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        edges = db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        active = db.execute(
            "SELECT COUNT(*) FROM edges WHERE status='active'").fetchone()[0]
        return {"ok": True, "entities": e, "edges": edges, "active": active}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    finally:
        db.close()


def warm_stats():
    # Warm neurons live in the STORE (kind='neuron'). NEW SCHEMA: size at rest
    # is free; the optimization is TOKEN COST on fire. Report neuron count,
    # recorded fires, and flag any neuron whose body would consume the whole
    # firing budget when it fires (an efficiency note, not a fault).
    try:
        import warm_neuron_store
        neurons = warm_neuron_store.list_neurons()
    except Exception:
        neurons = []
    TOKEN_BUDGET = 300
    heavy = [n["slug"] for n in neurons if len(n["text"]) // 4 > TOKEN_BUDGET]
    state = {}
    try:
        state = json.load(open(os.path.join(MEM_DIR, "index", "warm_state.json")))
    except Exception:
        pass
    fires = sum(b.get("fires", 0)
                for b in state.get("neurons", state.get("blocks", {})).values())
    return {"neurons": len(neurons), "fires": fires, "heavy_tokens": heavy}


def identity_stats():
    # Identity lives in the STORE (topic='identity' always-on entries); the old
    # persona.md block file is retired.
    try:
        import sqlite3
        db = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT text FROM entries WHERE topic='identity' "
            "AND status='active' ORDER BY priority, id").fetchall()
        db.close()
        texts = [r[0] for r in rows]
        return {"ok": True, "identity_entries": len(texts),
                "identity_chars": sum(len(t) for t in texts)}
    except Exception:
        return {"ok": False}


def drift_status():
    r = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, "drift-ledger.py"),
                        "check"], capture_output=True, text=True, timeout=20)
    out = (r.stdout or "").strip().splitlines()
    return {"clean": r.returncode == 0, "detail": out[-1] if out else "?"}


def claim_status():
    counts = {"PASS": 0, "DEGRADE": 0, "BLOCK": 0}
    total = 0
    if os.path.exists(CLAIM_JOURNAL):
        for line in open(CLAIM_JOURNAL):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                counts[e.get("verdict", "?")] = counts.get(e.get("verdict", "?"), 0) + 1
                total += 1
            except Exception:
                pass
    return {"journaled": total, "by_verdict": counts}


def store_status():
    """Hybrid memory store health (atomic entries + audit chain)."""
    try:
        r = subprocess.run(
            [STORE_PY,
             os.path.join(SCRIPT_DIR, "memory_store.py"), "stats", "--json"],
            capture_output=True, text=True, timeout=15)
        s = json.loads(r.stdout or "{}")
        rv = subprocess.run(
            [STORE_PY,
             os.path.join(SCRIPT_DIR, "memory_store.py"), "verify", "--json"],
            capture_output=True, text=True, timeout=15)
        v = json.loads(rv.stdout or "{}")
        return {"ok": True, "entries": s.get("active_entries", 0),
                "working": s.get("working", 0),
                "audit": s.get("audit", 0),
                "chain_ok": v.get("ok", False),
                "topics": s.get("top_topics", {})}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def report():
    g, w, i, d, c = (graph_stats(), warm_stats(), identity_stats(),
                     drift_status(), claim_status())
    s = store_status()
    print("SYSTEM STATUS")
    print("-" * 50)
    print(f"graph    : {'OK' if g['ok'] else 'DOWN'}  "
          f"{g.get('entities', 0)} entities, {g.get('edges', 0)} edges, "
          f"{g.get('active', 0)} active")
    print(f"warm     : {w['neurons']} neurons, {w['fires']} fires"
          + (f", heavy (>budget): {w['heavy_tokens']}" if w["heavy_tokens"] else ""))
    if i.get("ok"):
        print(f"identity : {i['identity_entries']} entries, {i['identity_chars']} chars")
    if s.get("ok"):
        print(f"store    : {'OK' if s.get('chain_ok') else 'CHAIN BROKEN'}  "
              f"{s['entries']} entries, {s['working']} working, "
              f"{s['audit']} audit")
    else:
        print(f"store    : DOWN ({s.get('error', '?')})")
    print(f"drift    : {'CLEAN' if d['clean'] else 'DRIFT FLAGGED'}  ({d['detail']})")
    print(f"claim    : {c['journaled']} journaled "
          f"(PASS={c['by_verdict'].get('PASS', 0)}, "
          f"DEGRADE={c['by_verdict'].get('DEGRADE', 0)})")
    all_ok = g.get("ok", False) and d["clean"] and s.get("chain_ok", False)
    print("-" * 50)
    print("OVERALL  : " + ("HEALTHY" if all_ok else "ISSUES — see above"))
    return all_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="print one status line only (for scripts/TUI)")
    args = ap.parse_args()
    ok = report()
    if args.quick:
        print("HEALTHY" if ok else "ISSUES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()