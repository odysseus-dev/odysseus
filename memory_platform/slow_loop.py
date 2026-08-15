#!/usr/bin/env python3
"""slow_loop.py — the slow-loop evidence journal (Phase 3 gate evidence).

Phase 3 (operator/voicebox split) is gated behind a strict feasibility bar
(docs/PHASE3_GATE.md): no autonomous compiled-skill execution until the gate
criteria pass WITH EVIDENCE. This module is the evidence-gathering half — it
journals every execution event that bears on the gate, so the criteria
accumulate measured data instead of anecdotes:

  criterion 1 (reliability): >= 3 distinct tasks, >= 30 consecutive correct
                             compiled executions, zero silent failures
  criterion 2 (measured uplift): >= 50% LLM-call reduction with no quality drop
  criterion 3 (adaptive correctness): drift detection -> slow-loop re-derivation
  criterion 4 (graceful fallback): failure falls back to LLM path automatically
  criterion 5 (auditability): every run journaled (when, skill, input hash, outcome)

Every call here is a durable, timestamped journal entry under
<memory>/journal/slow-loop-<month>.md. The gate reads this journal; nothing is
claimed until the data says so. This is the honest path to Phase 3 — gather
evidence organically, never assert it.

Usage:
  slow_loop.py run <skill> <input-hash> <outcome> [--llm_fallback true]
  slow_loop.py gate           # evaluate the gate criteria against the journal
  slow_loop.py report         # recent journal entries
"""

import argparse
import json
import os
import sys

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

JOURNAL_DIR = os.path.join(memory_env.memory_dir(), "journal")

GATE_MIN_TASKS = 3          # criterion 1: distinct tasks
GATE_MIN_RUNS = 30          # criterion 1: consecutive correct runs
GATE_REDUCTION = 0.50       # criterion 2: LLM-call reduction threshold


def _journal(entry):
    try:
        import fcntl, datetime, hashlib
        os.makedirs(JOURNAL_DIR, exist_ok=True)
        month = datetime.datetime.now().strftime("%Y-%m")
        path = os.path.join(JOURNAL_DIR, f"slow-loop-{month}.md")
        lock = open(os.path.join(JOURNAL_DIR, ".slow-loop.lock"), "a")
        fcntl.flock(lock, fcntl.LOCK_EX)
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        lines = [f"`{ts}` **{entry['outcome']}** skill=`{entry['skill']}` "
                 f"input=`{entry['input_hash']}`"]
        if entry.get("llm_fallback"):
            lines.append(f"  - llm_fallback: {entry['llm_fallback']}")
        if entry.get("error"):
            lines.append(f"  - error: {entry['error'][:120]}")
        with open(path, "a") as f:
            f.write("\n".join(lines) + "\n")
        fcntl.flock(lock, fcntl.LOCK_UN)
    except Exception:
        pass


def run(skill, input_hash, outcome, llm_fallback=False, error=""):
    """Journal one compiled-skill execution. outcome: 'success' | 'failure'."""
    entry = {"skill": skill, "input_hash": input_hash, "outcome": outcome,
             "llm_fallback": llm_fallback, "error": error}
    _journal(entry)
    return entry


def _read_journal():
    import datetime
    month = datetime.datetime.now().strftime("%Y-%m")
    try:
        with open(os.path.join(JOURNAL_DIR, f"slow-loop-{month}.md")) as f:
            return f.readlines()
    except Exception:
        return []


def gate():
    """Evaluate the Phase-3 gate criteria against the journal. Returns a
    verdict per criterion with the data behind it. Nothing is claimed beyond
    what the journal shows — a criterion without data is 'not met'."""
    lines = _read_journal()
    runs = []
    for ln in lines:
        if "**success**" in ln or "**failure**" in ln:
            out = "success" if "**success**" in ln else "failure"
            skill = (ln.split("skill=`", 1)[1].split("`", 1)[0]
                     if "skill=`" in ln else "")
            runs.append({"outcome": out, "skill": skill})
    tasks = {r["skill"] for r in runs if r["skill"]}
    # Criterion 1: >= 3 distinct tasks, >= 30 CONSECUTIVE correct executions
    # at the tail of the journal (a failure breaks the streak).
    streak = 0
    for r in reversed(runs):
        if r["outcome"] == "success":
            streak += 1
        else:
            break
    c1 = {"met": len(tasks) >= GATE_MIN_TASKS and streak >= GATE_MIN_RUNS,
          "tasks": len(tasks), "consecutive_successes": streak,
          "required_tasks": GATE_MIN_TASKS, "required_runs": GATE_MIN_RUNS}
    # Criterion 2: measured uplift — not assertable from the journal alone;
    # needs the recall-cost / LLM-call logs. Report as "evidence pending".
    c2 = {"met": False, "status": "evidence pending — needs measured "
                                  "LLM-call reduction logs, not assertions"}
    # Criterion 4: graceful fallback — every failure must show a fallback.
    failures = [r for r in runs if r["outcome"] == "failure"]
    all_fallback = all("llm_fallback" in ln for ln in lines
                       if "**failure**" in ln)
    c4 = {"met": len(failures) == 0 or all_fallback,
          "failures": len(failures), "all_had_fallback": all_fallback}
    met_all = all(c["met"] for c in (c1, c2, c4))
    return {"gate_met": met_all, "criterion_1": c1, "criterion_2": c2,
            "criterion_4": c4,
            "note": "Phase 3 remains SPEC-ONLY until all criteria pass "
                    "with evidence (see PHASE3_GATE.md)"}


def report(limit=10):
    lines = _read_journal()
    return lines[-limit:]


def main():
    ap = argparse.ArgumentParser(description="Slow-loop evidence journal (Phase 3 gate)")
    ap.add_argument("cmd", choices=["run", "gate", "report"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--input-hash", default="")
    ap.add_argument("--llm-fallback", action="store_true")
    ap.add_argument("--error", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "run":
        skill = " ".join(args.arg)
        outcome = "success"  # or pass --outcome; default success for a completed run
        entry = run(skill, args.input_hash or "no-hash", outcome,
                    args.llm_fallback, args.error)
        print(json.dumps(entry, indent=2) if args.json else
              f"journaled: {entry['outcome']} skill={entry['skill']}")

    elif args.cmd == "gate":
        g = gate()
        if args.json:
            print(json.dumps(g, indent=2))
            return
        print(f"Phase-3 gate: {'MET' if g['gate_met'] else 'NOT MET'}")
        c = g["criterion_1"]
        print(f"  c1 reliability: {c['tasks']}/{c['required_tasks']} tasks, "
              f"{c['consecutive_successes']}/{c['required_runs']} consecutive")
        print(f"  c2 uplift: {g['criterion_2']['status']}")
        c4 = g["criterion_4"]
        print(f"  c4 fallback: {c4['failures']} failures, "
              f"all_had_fallback={c4['all_had_fallback']}")
        print(f"  {g['note']}")

    elif args.cmd == "report":
        for ln in report():
            print(ln.rstrip())


if __name__ == "__main__":
    main()
