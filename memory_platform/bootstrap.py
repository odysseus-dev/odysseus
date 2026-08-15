#!/usr/bin/env python3
"""bootstrap.py — the overnight growth cycle (run while the user sleeps).

The bootstrap turns a session's work into durable system state:
  1. LESSONS   — record teachable moments from the session (mistakes -> behaviour)
  2. GROWTH    — extract behavioural deltas from the session transcript
  3. TAXONOMY  — let the taxonomy grow: novel claims seed new wings
  4. SKILLS    — promote trusted skills (used >= 2 rewarded times) to executable
  5. CONSOLIDATION — pressure-gated merge/prune/promote (sleep-time)
  6. AUTHORITY — verify persona control over the model (recover/probe)
  7. REPORT    — write docs/bootstrap-report.md summarising everything done

Everything is best-effort and isolated; a failure in one step never blocks the
cycle. Run via the sleep agent after a session:
  bootstrap.py run [--session-transcript PATH]
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

REPORT_DIR = os.path.join(memory_env.memory_dir(), "docs")
PY = memory_env.python_bin()
SCRIPT = _SD


def _run(name, cmd, timeout=120):
    """Run a step, capture output, never throw."""
    try:
        r = subprocess.run([PY, os.path.join(SCRIPT, cmd[0])] + cmd[1:],
                           capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "out": (r.stdout or r.stderr).strip()[-300:]}
    except Exception as e:
        return {"ok": False, "out": str(e)[:300]}


def run(transcript=""):
    report = {"date": datetime.datetime.now().isoformat(timespec="seconds"),
              "steps": []}

    def step(name, fn):
        res = fn()
        report["steps"].append({"name": name, **res})
        print(f"[{'ok' if res['ok'] else 'FAIL'}] {name}")
        if res.get("out"):
            print(f"      {res['out'][:120]}")
        return res

    # 1. LESSONS from the session (teachable moments already recorded inline).
    step("lessons", lambda: _run("lessons", ["lessons.py", "recent", "--json"]))

    # 2. GROWTH: extract behavioural deltas from the session transcript if given.
    if transcript and os.path.exists(transcript):
        step("growth", lambda: _run(
            "growth_delta", ["growth_delta.py", "reflect", "--material",
                             open(transcript).read()[:6000]], timeout=180))

    # 3. TAXONOMY growth: wings list shows what the taxonomy has absorbed.
    step("taxonomy", lambda: _run("taxonomy", ["taxonomy.py", "wings", "--json"]))

    # 4. SKILLS: promote anything eligible to executable.
    step("skills", lambda: _run("skill_library", ["skill_library.py", "list", "--json"]))

    # 5. CONSOLIDATION: sleep-time pressure report (not the full cycle — that
    #    needs hours of transcripts; the report tells us if pressure is high).
    step("consolidation", lambda: _run(
        "consolidate", ["consolidate.py", "pressure"]))

    # 6. AUTHORITY: verify persona control (safe probe, no weight-edit).
    step("authority", lambda: _run(
        "authority_harness", ["authority_harness.py", "limits", "auto", "--json"]))

    # 7. REPORT
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        path = os.path.join(REPORT_DIR, "bootstrap-report.md")
        lines = [f"# Bootstrap report — {report['date']}\n"]
        for s in report["steps"]:
            lines.append(f"## {s['name']}: {'ok' if s['ok'] else 'FAIL'}")
            if s.get("out"):
                lines.append(f"```\n{s['out']}\n```")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        report["report_path"] = path
    except Exception as e:
        report["report_error"] = str(e)[:200]

    report["ok"] = all(s["ok"] for s in report["steps"] if s["name"] != "lessons")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--session-transcript", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = run(args.session_transcript)
    if args.json:
        print(json.dumps(res, indent=2))
        return
    print(f"\nBootstrap complete: {sum(1 for s in res['steps'] if s['ok'])}/{len(res['steps'])} steps ok")
    if res.get("report_path"):
        print(f"Report: {res['report_path']}")


if __name__ == "__main__":
    main()
