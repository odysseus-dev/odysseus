#!/usr/bin/env python3
"""growth_delta.py — turn reflections into ACTIONABLE persona growth.

PHASE 1 of the growth-acceleration path. Research basis:
- Generative Agents (Park et al.): memories must be SYNTHESIZED into higher-
  level reflections AND RETRIEVED to shape behavior — not just stored. The
  audit found the corpus was "sitting cold, not changing output"; this module
  is the apply path.
- MetaSkill-Evolve: improvement needs a fast loop (skill/delta evolution) with
  a slow loop (the improvement method itself). This is the fast loop.

Two distinct gates, per the design:
  - BEHAVIORAL delta (delivery, operating approach): a SINGLE high-confidence
    LLM-extracted signal may apply immediately. This is why growth can feel
    alive — the persona adapts its HOW this session, not after 3 sightings.
  - PERMANENT rule (constitution/identity/operating fact): unchanged — still
    requires the existing 3-sighting strength gate. We never weaken that.

Flow:
  growth_delta.py reflect "<material>"          # LLM -> deltas, single-gate apply
  growth_delta.py apply <delta-json>            # apply a behavioural delta now
  growth_delta.py list                          # applied deltas (the "growth read-back")
  growth_delta.py recent                        # what changed recently (session opener)

Every delta is journaled with its evidence (which reflection produced it), so
growth is auditable — never a silent write.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DELTA_MODEL = os.environ.get("GROWTH_DELTA_MODEL", "qwen3:14b")
STORE = memory_env.store_db()
DELTAS_FILE = os.path.join(memory_env.memory_dir(), "index", "growth_deltas.json")

# Behavioural (fast, single-signal) vs permanent (slow, 3-sighting) targets.
BEHAVIOURAL_TOPICS = {"delivery", "operating", "persona"}
PERMANENT_TOPICS = {"constitution", "identity", "project", "human"}

DELTA_PROMPT = """You are the growth engine of a personal AI persona. Given recent
interaction material, extract up to 3 ACTIONABLE behavioural deltas — changes to
HOW the persona serves its user (delivery, approach, discernment, tone, habits).
These apply to a SINGLE strong signal (fast loop).

Rules:
- Only emit changes that would measurably improve service per turn (the growth
  definition). Skip trivia and one-off events.
- A delta is a specific, adoptable behaviour: "when the user asks about X, lead
  with Y because Z" — not a vague goal.
- Do NOT propose changes to permanent rules (constitution, identity, facts).
- Each delta needs: what to change, the evidence it came from, confidence (0-1).

Output ONLY JSON:
{"deltas":[{"change":"...", "evidence":"...", "confidence":0.0-1.0,
            "target":"delivery|operating|persona"}]}"""


def _llm(prompt):
    import urllib.request
    payload = json.dumps({
        "model": DELTA_MODEL, "prompt": prompt, "stream": False,
        "think": False,
        "options": {"temperature": 0.4, "num_predict": 800},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()).get("response", "")


def _parse_json(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        return json.loads(t)
    except Exception:
        s, e = t.find("{"), t.rfind("}")
        if s != -1 and e > s:
            try:
                return json.loads(t[s:e + 1])
            except Exception:
                pass
        return {"deltas": []}


def _load_deltas():
    try:
        with open(DELTAS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"applied": [], "pending": []}


def _save_deltas(d):
    os.makedirs(os.path.dirname(DELTAS_FILE), exist_ok=True)
    tmp = DELTAS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, DELTAS_FILE)


def _write_store(text, topic):
    """Best-effort write a behavioural delta to the store (persona/delivery)."""
    try:
        py = memory_env.python_bin()
        store_py = os.path.join(_SD, "memory_store.py")
        subprocess.run([py, store_py, "add", text, "--topic", topic,
                        "--importance", "0.5", "--source", "growth_delta",
                        "--method", "agent"],
                       capture_output=True, text=True, timeout=30)
    except Exception:
        pass


def _apply_one(change, evidence, conf, target):
    """Apply a single behavioural delta: journal it, write it to the store.
    The single high-confidence gate is enforced by the caller."""
    rec = {"change": change, "evidence": (evidence or "")[:200],
           "confidence": conf, "target": target,
           "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    state = _load_deltas()
    state["applied"].append(rec)
    _save_deltas(state)
    _write_store(change, target)
    return rec


def reflect(material, dry_run=False):
    """Extract behavioural deltas from interaction material and apply them."""
    out = _llm(DELTA_PROMPT + "\n\nMaterial:\n" + material)
    parsed = _parse_json(out)
    deltas = parsed.get("deltas", [])[:3]  # spec: up to 3 per pass
    state = _load_deltas()
    applied = []
    would_apply = []
    for d in deltas:
        change = (d.get("change") or "").strip()
        conf = float(d.get("confidence") or 0)
        target = d.get("target") or "delivery"
        if not change or len(change) < 12:
            continue
        if target not in BEHAVIOURAL_TOPICS:
            continue  # never route behavioural deltas to permanent topics
        # SINGLE high-confidence gate for behavioural growth.
        if conf < 0.6:
            continue
        would_apply.append({"change": change, "evidence": (d.get("evidence") or "")[:200],
                            "confidence": conf, "target": target})
        if not dry_run:
            applied.append(_apply_one(change, (d.get("evidence") or ""),
                                      conf, target))
    return {"extracted": deltas, "applied": applied, "would_apply": would_apply,
            "dry_run": dry_run}


def apply(delta_json):
    """Apply a behavioural delta directly (spec: apply <delta-json>)."""
    try:
        d = json.loads(delta_json)
    except Exception:
        return {"applied": [], "error": "invalid delta json"}
    change = (d.get("change") or "").strip()
    conf = float(d.get("confidence") or 0)
    target = d.get("target") or "delivery"
    if not change or len(change) < 12:
        return {"applied": [], "error": "delta too short to be actionable"}
    if target not in BEHAVIOURAL_TOPICS:
        return {"applied": [], "error": f"not a behavioural target: {target}"}
    if conf < 0.6:
        return {"applied": [], "error": f"confidence {conf} below the 0.6 single-signal gate"}
    return {"applied": [_apply_one(change, (d.get("evidence") or ""), conf, target)]}


def recent(limit=5):
    state = _load_deltas()
    applied = state.get("applied", [])
    return applied[-limit:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["reflect", "apply", "list", "recent"])
    ap.add_argument("--material", default="")
    ap.add_argument("--delta", default="", help="delta JSON for `apply`")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.cmd == "reflect":
        if not args.material:
            print("usage: growth_delta.py reflect --material '<transcript or reflection>'")
            return
        res = reflect(args.material, dry_run=args.dry_run)
        shown = res["would_apply"] if args.dry_run else res["applied"]
        if args.dry_run:
            print(f"DRY RUN — {len(res['extracted'])} candidates, "
                  f"{len(shown)} would apply")
        else:
            print(f"applied {len(shown)} behavioural deltas")
        applied_keys = {(d.get('change'), d.get('confidence')) for d in shown}
        for d in shown:
            print(f"  [{d['target']}] c={d['confidence']:.2f} {d['change'][:90]}")
        for d in res["extracted"]:
            if (d.get('change'), float(d.get('confidence') or 0)) not in applied_keys:
                print(f"  (skip) {d.get('change','')[:80]} [conf {d.get('confidence')}]")
    elif args.cmd == "apply":
        res = apply(args.delta or "")
        if res.get("error"):
            print(f"apply failed: {res['error']}")
        else:
            print(f"applied {len(res['applied'])} behavioural delta(s)")
            for d in res["applied"]:
                print(f"  [{d['target']}] c={d['confidence']:.2f} {d['change'][:90]}")
    elif args.cmd == "list":
        st = _load_deltas()
        print(f"{len(st['applied'])} applied growth deltas")
        for d in st["applied"][-15:]:
            print(f"  [{d['applied_at'][:16]}] {d['change'][:80]}")
    elif args.cmd == "recent":
        for d in recent():
            print(f"- {d['change']}  (from: {d['evidence'][:80]})")


if __name__ == "__main__":
    main()
