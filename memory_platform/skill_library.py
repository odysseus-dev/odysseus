#!/usr/bin/env python3
"""skill_library.py — Voyager-style skill library for the persona.

PHASE 2 of the growth-acceleration path. Research basis:
- Voyager (Wang et al.): an ever-growing skill library of EXECUTABLE procedures
  retrieved by description — skills compound the agent's ability and BYPASS
  repeated LLM reasoning (retrieved, not re-derived).
- Procedural-memory papers (Memp, LEGOMem, CodeMem): routines stored as
  reusable procedures run without full inference each time.

Two-timescale self-improvement (MetaSkill-Evolve):
  - FAST loop: task skills evolve from execution success/failure.
  - SLOW loop: the improvement method itself (below, `evaluate` rewards a skill
    so it promotes toward EXECUTABLE — the compiled, LLM-light form).

Lifecycle of a skill:  observed -> drafted -> trusted (used, rewarded) -> executable
Promotion is GATED (research-aligned control): a skill must accumulate >= 2
successful uses with reward evidence before it is trusted; only trusted skills
are eligible to become executable procedures. No single-use autopromotion.

Storage: index/skills.json  (deterministic, auditable — same as growth deltas)
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_env

SKILLS_FILE = os.path.join(memory_env.memory_dir(), "index", "skills.json")
EXEC_DIR = os.path.join(memory_env.memory_dir(), "skills")
MIN_USES_TO_TRUST = 2

EXEC_HEADER = """#!/usr/bin/env python3
# EXECUTABLE SKILL — compiled procedural memory. Runs without the LLM.
# Generated from a trusted skill. Executes the persona's accumulated routine.
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load():
    try:
        with open(SKILLS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"skills": []}


def _save(skills):
    os.makedirs(os.path.dirname(SKILLS_FILE), exist_ok=True)
    tmp = SKILLS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(skills, f, indent=2)
    os.replace(tmp, SKILLS_FILE)


def add(name, description, steps, howto=""):
    """Draft a new skill from an observed successful procedure."""
    skills = _load()
    for s in skills["skills"]:
        if s["name"] == name:
            s["updated"] = _now()
            s["description"] = description
            s["steps"] = steps
            s["howto"] = howto
            _save(skills)
            return {"status": "updated", "name": name, "state": s["state"]}
    skill = {
        "name": name, "description": description, "steps": steps, "howto": howto,
        "state": "observed", "uses": 0, "rewards": 0, "created": _now(),
        "updated": _now(), "last_used": None,
    }
    skills["skills"].append(skill)
    _save(skills)
    return {"status": "drafted", "name": name, "state": "observed"}


def use(name, reward=None):
    """Record a use. reward=True marks the procedure as a success (fast loop).
    Accumulating successes promotes the skill toward trusted/executable."""
    skills = _load()
    for s in skills["skills"]:
        if s["name"] != name:
            continue
        s["uses"] += 1
        s["last_used"] = _now()
        if reward:
            s["rewards"] += 1
        # GATED promotion (fast loop): needs >= MIN_USES_TO_TRUST successful
        # uses to move observed -> trusted. No single-use autopromotion.
        if s["state"] == "observed" and s["rewards"] >= MIN_USES_TO_TRUST:
            s["state"] = "trusted"
        _save(skills)
        return {"status": "recorded", "name": name,
                "uses": s["uses"], "rewards": s["rewards"], "state": s["state"]}
    return {"status": "not_found", "name": name}


def compile_executable(name):
    """Trusted -> executable: emit a deterministic Python procedure. This is
    the Phase-2 'LLM-light' step — the routine now runs without inference."""
    skills = _load()
    for s in skills["skills"]:
        if s["name"] != name:
            continue
        if s["state"] != "trusted":
            return {"status": "blocked", "name": name, "reason": "not trusted"}
        steps = s["steps"]
        body_lines = []
        for i, st in enumerate(steps):
            if st.startswith("command:"):
                cmd = st[8:]
                body_lines.append(f"    print('step {i+1}: ' + {cmd!r})")
                body_lines.append(f"    os.system({cmd!r})")
            else:
                body_lines.append(f"    print('step {i+1}: {st}')")
        script = EXEC_HEADER + (
            "import os, sys\n\n"
            "def run():\n"
            + "\n".join(body_lines) + "\n"
            "    print('skill done')\n\n"
            "if __name__ == '__main__':\n"
            "    run()\n"
        )
        exec_dir = EXEC_DIR
        os.makedirs(exec_dir, exist_ok=True)
        fname = name.lower().replace(" ", "_").replace("/", "_") + ".py"
        fpath = os.path.join(exec_dir, fname)
        with open(fpath, "w") as f:
            f.write(script)
        s["state"] = "executable"
        s["exec_path"] = fpath
        s["updated"] = _now()
        _save(skills)
        return {"status": "compiled", "name": name, "path": fpath}
    return {"status": "not_found", "name": name}


def list_skills(state=None):
    skills = _load()["skills"]
    if state:
        skills = [s for s in skills if s["state"] == state]
    return skills


def main():
    ap = argparse.ArgumentParser(description="Skill library (Voyager-style procedural memory)")
    ap.add_argument("cmd", choices=["add", "use", "compile", "list"])
    ap.add_argument("--name", default="")
    ap.add_argument("--description", default="")
    ap.add_argument("--steps", action="append", default=[],
                    help="a procedure step; prefix an executable command with 'command:' (repeatable)")
    ap.add_argument("--reward", action="store_true")
    ap.add_argument("--state", default="")
    args = ap.parse_args()

    if args.cmd == "add":
        if not args.name:
            print("need --name"); return
        res = add(args.name, args.description, args.steps)
        print(res["status"], res["name"], "->", res["state"])
    elif args.cmd == "use":
        res = use(args.name, reward=args.reward)
        print(f"{res['status']}: {res.get('name','')} "
              f"uses={res.get('uses','')} rewards={res.get('rewards','')} "
              f"state={res.get('state','')}")
    elif args.cmd == "compile":
        res = compile_executable(args.name)
        if res.get("status") == "compiled":
            print(f"compiled -> {res['path']}")
        else:
            print(f"{res.get('status','?')}: {res.get('reason','')}")
    elif args.cmd == "list":
        for s in list_skills(args.state):
            print(f"  [{s['state']:10}] {s['name']} "
                  f"(uses={s['uses']}, rewards={s['rewards']}) — {s['description'][:60]}")


if __name__ == "__main__":
    main()

