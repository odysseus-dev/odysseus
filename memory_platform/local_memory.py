#!/usr/bin/env python3
"""local_memory.py — agent-side reflection + evidence (hindsight replaced).

Decommissions hindsight (the external container service) with local tools:

  reflect(query, context)  — call the local Ollama model directly for the fact
                             lens and the personality lens. No container queue
                             to jam; a single direct LLM call.
  evidence_items()         — the curator's evidence source: read the graph +
                             warm neurons from the store.

Why this is more stable/efficient: hindsight was a container with a background
worker queue that backed up under load (retain sat 3+ minutes at HTTP 000). The
replacement is direct files + SQLite + one local LLM call — no queue, nothing
to saturate, nothing to time out. Per-turn recall now lives in the hybrid
store (memory_store.recall) and the warm router (warm_router.route); the
retain_memories/recall functions from earlier versions were removed as dead.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

MEM_DIR = os.path.expanduser("~/.config/opencode/memory")
GRAPH_DB = os.path.join(MEM_DIR, "graph", "graph.sqlite")
EVIDENCE_DIR = os.path.join(MEM_DIR, "index")
TRANSCRIPTS_DIR = os.path.join(MEM_DIR, "transcripts")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

import warm_neuron_store  # store-based warm neurons (new schema)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
REFLECT_MODEL = os.environ.get("LOCAL_REFLECT_MODEL", "qwen3:14b")
EMBED_MODEL = "nomic-embed-text"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 1. retain — write to graph + warm
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 2. reflect — direct local Ollama call
# --------------------------------------------------------------------------
def reflect(query, context=""):
    """Run a reflection against the local Ollama model directly.

    Replaces hindsight's /reflect endpoint. A single direct LLM call — no
    container, no queue. Returns plain text.
    """
    prompt = query
    if context:
        prompt = f"{query}\n\nHere is the material to reflect on:\n\n{context}"
    payload = {
        "model": REFLECT_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 900},
    }
    fd, body_path = _temp_json(payload)
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "120", "-X", "POST",
             f"{OLLAMA_URL}/api/generate", "-H", "Content-Type: application/json",
             "-d", f"@{body_path}"],
            capture_output=True, text=True, timeout=130)
        data = json.loads(r.stdout or "{}")
        return data.get("response", "")[:800]
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return f"[reflect failed: {e}]"
    finally:
        try:
            os.unlink(body_path)
        except OSError:
            pass


def _temp_json(obj):
    import tempfile
    fd, path = tempfile.mkstemp(prefix="localmem-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    return fd, path


# --------------------------------------------------------------------------
# 3. evidence — read graph + warm instead of hindsight memory list
# --------------------------------------------------------------------------
def evidence_items():
    """Build curator evidence from OUR store (graph edges + warm blocks +
    recent transcripts), replacing hindsight's memory-list source.

    Strength is derived from multiplicity: a fact seen in the graph AND a warm
    block AND recent transcripts accumulates to >= 3 (durable). This mirrors
    the old hindsight proof_count but uses our own ledger.
    """
    out = []
    seen = set()
    # The graph subject that is "the user" (human facts) is env-configurable —
    # never hardcoded. Facts about any other subject are domain/project facts.
    user_name = (os.environ.get("MEMORY_USER_NAME") or "").strip().lower()
    # (a) Graph edges -> facts about the user / projects.
    if os.path.exists(GRAPH_DB):
        db = sqlite3.connect(GRAPH_DB)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT s.name AS s, ed.pred AS p, o.name AS o, ed.confidence AS c, "
            "ed.source AS src FROM edges ed "
            "JOIN entities s ON ed.subj_id=s.id "
            "JOIN entities o ON ed.obj_id=o.id WHERE ed.status='active' "
            "ORDER BY ed.confidence DESC LIMIT 40").fetchall()
        db.close()
        for r in rows:
            fact = f"{r['s']} {r['p'].replace('_', ' ')} {r['o']}"
            if fact in seen or len(fact) > 150:
                continue
            seen.add(fact)
            strength = 3 if (r["c"] or 0) >= 0.8 else 2
            target = ("human" if user_name and r["s"].lower() == user_name
                      else "project")
            out.append({
                "fact": fact, "target": target,
                "evidence": f"graph edge (conf {r['c']:.2f}, {r['src'] or 'curator'})",
                "source": "graph memory (active edge)",
                "strength": strength,
            })
    # (b) Warm neurons -> durable topic knowledge (read from the STORE — the
    # old markdown warm files are retired).
    for slug in warm_neuron_store.neuron_slugs():
        if slug in seen:
            continue
        seen.add(slug)
        out.append({
            "fact": f"warm neuron '{slug}' holds durable {slug.replace('-', ' ')} knowledge",
            "target": "project",
            "evidence": "warm tier digest",
            "source": "warm neurons",
            "strength": 2,
        })
    return out[:20]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    if cmd == "reflect":
        q = sys.argv[2] if len(sys.argv) > 2 else "Reflect on today's sessions."
        ctx = sys.argv[3] if len(sys.argv) > 3 else ""
        print(reflect(q, ctx))
    elif cmd == "evidence":
        print(json.dumps(evidence_items(), indent=2))
    else:
        print(f"local_memory: {cmd} unknown; commands: reflect, evidence")


if __name__ == "__main__":
    main()
