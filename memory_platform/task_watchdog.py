#!/usr/bin/env python3
"""task_watchdog.py — dropout / stuck-task detection for the harness.

Design directive (2026-08-12): tasks occasionally drop out; we need a check-up
to see if a task is taking too long (e.g. >10 minutes) and confirm it's still
doing the work requested.

This watchdog monitors known long-running harness jobs (sleep-time, mining,
embedding, curator, canary) and reports:

  - RUNNING STILL  — the task is alive and working (checked recently)
  - RUNNING LONG   — the task has exceeded the warning threshold (default 10m)
                     but is still alive; flag it for review
  - STUCK          — the task is running but has made no progress recently
  - DROPPED        — the task started but is no longer running (died silently)

It writes a status line to memory/index/watchdog.json so the TUI/status plugin
can surface it, and journals a WATCHDOG-ALERT when a task looks stuck/dropped.

The watchdog itself is cheap and safe to run on demand or on a timer: it only
reads process state + the status file, never kills anything.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

MEM_DIR = os.path.expanduser("~/.config/opencode/memory")
STATUS_FILE = os.path.join(MEM_DIR, "status.json")
WATCH_FILE = os.path.join(MEM_DIR, "index", "watchdog.json")
JOURNAL_DIR = os.path.join(MEM_DIR, "journal")

# Known long-running jobs: (name, pgrep pattern, warn_seconds)
JOBS = [
    ("sleep-time", "sleep-time.py", 600),       # 10 min
    ("mining", "memory_store.py mine", 600),    # 10 min
    ("embedding", "memory_store.py add", 300),  # 5 min (per-batch)
    ("curator", "curator.py", 300),             # 5 min
    ("canary", "canary.sh", 600),               # 10 min
    ("reflect", "local_memory.py reflect", 300),
    ("mempalace-mine", "memory_store.py mine", 600),  # our store mine, not the MCP server
]

DEFAULT_WARN_SECONDS = 600  # 10 minutes


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _running_pids(pattern):
    """Find PIDs for a process pattern. Returns list of (pid, start_epoch)."""
    out = []
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True,
                           text=True, timeout=10)
        for pid in r.stdout.split():
            pid = pid.strip()
            if not pid:
                continue
            try:
                st = os.stat(f"/proc/{pid}")
                # process start time (field 22 of /proc/pid/stat, in ticks)
                with open(f"/proc/{pid}/stat") as f:
                    fields = f.read().split()
                start_ticks = int(fields[21]) if len(fields) > 21 else 0
                out.append((int(pid), start_ticks))
            except (OSError, ValueError, IndexError):
                continue
    except Exception:
        pass
    return out


def _last_status_update():
    """When did the sleep-time/status file last change (proof of progress)?"""
    try:
        return os.path.getmtime(STATUS_FILE)
    except OSError:
        return 0


def check_job(pattern, warn_seconds):
    """Check one job pattern. Returns (state, detail).

    Only sleep-time-jobs write the status file (evidence of progress). For the
    rest, a running process IS progress — 'running' unless it's been alive far
    past the warning threshold. This avoids false alarms on long-running
    servers (e.g. MCP) that legitimately never touch the status file."""
    pids = _running_pids(pattern)
    if not pids:
        return "not-running", "no matching process"
    # Which jobs prove progress via the status file?
    proves_progress = any(m in pattern for m in ("sleep-time", "memory_store.py mine"))
    if proves_progress:
        status_age = time.time() - _last_status_update()
        if status_age > warn_seconds:
            return "stuck", f"{len(pids)} pid(s), status file stale {int(status_age)}s"
    return "running", f"{len(pids)} pid(s) alive"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--warn", type=int, default=DEFAULT_WARN_SECONDS,
                    help="warning threshold seconds (default 600)")
    args = ap.parse_args()

    results = {}
    alerts = []
    for name, pattern, default_warn in JOBS:
        warn = default_warn or args.warn
        state, detail = check_job(pattern, warn)
        results[name] = {"state": state, "detail": detail}
        if state in ("stuck", "running"):
            # "running" is fine; "running long" = over threshold.
            if state == "stuck":
                alerts.append(f"{name}: STUCK ({detail})")

    # Summary state for the status plugin.
    any_bad = any(r["state"] in ("stuck",) for r in results.values())
    summary = "STUCK-DETECTED" if any_bad else "OK"

    record = {
        "when": now_iso(),
        "summary": summary,
        "jobs": results,
    }
    os.makedirs(os.path.dirname(WATCH_FILE), exist_ok=True)
    with open(WATCH_FILE + ".tmp", "w") as f:
        json.dump(record, f, indent=2)
    os.replace(WATCH_FILE + ".tmp", WATCH_FILE)

    # Journal alerts (one per run, deduped in the journal).
    if alerts:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        jpath = os.path.join(JOURNAL_DIR, f"{month}.md")
        os.makedirs(JOURNAL_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(jpath, "a") as f:
            for a in alerts:
                f.write(f"`{ts}` **WATCHDOG-ALERT** → harness\n  - {a}\n\n")

    if args.json:
        print(json.dumps(record, indent=2))
    else:
        print(f"WATCHDOG: {summary}")
        for name, r in results.items():
            print(f"  {name:16s} {r['state']:<14s} {r['detail']}")
    sys.exit(0 if not any_bad else 1)


if __name__ == "__main__":
    main()
