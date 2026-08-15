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
        return {"applied": [], "pending": [], "last_read_back": None}


def _save_deltas(d):
    os.makedirs(os.path.dirname(DELTAS_FILE), exist_ok=True)
    tmp = DELTAS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, DELTAS_FILE)


# ACTIVE GROWTH PROFILE (token-efficient, session-independent, nothing lost):
# The full delta history ALWAYS persists in the store (source of truth). The
# profile is a maintained VIEW of the persona's CURRENT operating guidance —
# consolidated by target (delivery/operating/persona merge near-duplicates),
# so it shows the current HOW, not every historical delta. It is NOT
# session-anchored: sessions can be endless and it doesn't matter. A fixed
# token budget keeps the per-turn cost stable forever; applying a delta
# updates the profile on the same write (active, not per-session passive).

PROFILE_BUDGET_TOKENS = 180   # fixed per-turn cap (like the always-on digest)
PROFILE_ENTRY_CHARS = 120     # one-line compaction of each profile entry
_NEAR_DUP_OVERLAP = 0.25   # same-subject deltas merge (measured: same-guidance
                           # pairs ~0.30, distinct ~0.00 — 0.25 splits them)

# Topic display order (delivery first — the HOW is the most behaviourally
# relevant axis; the profile leads with it).
_PROFILE_ORDER = {"delivery": 0, "operating": 1, "persona": 2}


def _profile_key(delta):
    """Distinctive words of a delta for near-duplicate consolidation."""
    return {w.strip(".,;:!?()[]{}\"'").lower() for w in
            (delta.get("change") or "").split() if len(w) > 4}


def _near_dup(a, b):
    """Are two deltas the same guidance (shared distinctive words)?"""
    ka, kb = _profile_key(a), _profile_key(b)
    if not ka or not kb:
        return False
    return len(ka & kb) / max(len(ka), len(kb)) >= _NEAR_DUP_OVERLAP


def _apply_profile(delta):
    """Merge a new delta into the profile: near-duplicates consolidate (keep
    the most recent), distinct guidance appends. Runs on every apply — the
    profile stays active, not per-session."""
    state = _load_deltas()
    profile = state.get("profile", [])
    for i, existing in enumerate(profile):
        if _near_dup(existing, delta):
            profile[i] = delta  # same guidance, newer wins
            state["profile"] = profile
            _save_deltas(state)
            return "merged"
    profile.append(delta)
    state["profile"] = profile
    _save_deltas(state)
    return "added"


def growth_profile(max_tokens=PROFILE_BUDGET_TOKENS):
    """The active growth profile: the persona's CURRENT behavioural guidance,
    consolidated by target, capped at a fixed token budget. Session-independent
    and token-stable — the per-turn cost never grows with session length."""
    state = _load_deltas()
    profile = state.get("profile") or state.get("applied", [])[-5:]
    if not profile:
        return []
    # Group by target; within a target keep the most recent entries; order
    # targets by behavioural importance (delivery first).
    by_target = {}
    for d in profile:
        t = d.get("target") or "delivery"
        by_target.setdefault(t, []).append(d)
    ordered = []
    for t in sorted(by_target, key=lambda t: _PROFILE_ORDER.get(t, 9)):
        for d in by_target[t][-2:]:  # newest per target (profile stays tight)
            ordered.append(d)
    out = []
    used = 0
    for d in ordered:
        change = (d.get("change") or "").strip().replace("\n", " ")
        if len(change) > PROFILE_ENTRY_CHARS:
            change = change[: PROFILE_ENTRY_CHARS - 1].rstrip() + "…"
        tok = max(1, len(change) // 4)
        if used + tok > max_tokens:
            break
        out.append({"change": change, "target": d.get("target", "delivery")})
        used += tok
    return out


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
    """Apply a single behavioural delta: journal it, write it to the store,
    and merge it into the ACTIVE growth profile. The single high-confidence
    gate is enforced by the caller. The profile updates on the same write —
    growth is active, not a per-session read-back."""
    rec = {"change": change, "evidence": (evidence or "")[:200],
           "confidence": conf, "target": target,
           "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    state = _load_deltas()
    state["applied"].append(rec)
    _save_deltas(state)
    _write_store(change, target)
    try:
        _apply_profile(rec)  # active profile: near-dups merge, distinct adds
    except Exception:
        pass
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
    ap.add_argument("cmd", choices=["reflect", "apply", "list", "recent", "profile"])
    ap.add_argument("--material", default="")
    ap.add_argument("--delta", default="", help="delta JSON for `apply`")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
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
    elif args.cmd == "profile":
        prof = growth_profile()
        if args.json:
            print(json.dumps(prof, indent=2))
            return
        for d in prof:
            print(f"  [{d['target']}] {d['change']}")


if __name__ == "__main__":
    main()
