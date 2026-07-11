"""Manual/scheduled entry point for disabled-by-default Misumi Phase A pilots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.misumi_pilots import load_pilot_config, run_pilot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pilot", choices=("morning-status", "skill-audit", "task-triage", "household-qa", "memory-digest"))
    parser.add_argument("--question", default="")
    parser.add_argument("--manual", action="store_true", help="Run a disabled pilot manually for evaluation")
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()
    config = load_pilot_config()
    pilot = (config.get("pilots") or {}).get(args.pilot) or {}
    if not args.manual and not (config.get("enabled") and pilot.get("enabled")):
        print(json.dumps({"status": "disabled", "pilot": args.pilot}))
        return 2
    result = run_pilot(
        args.pilot,
        question=args.question,
        persist=not args.no_persist,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("household_unchanged") else 1


if __name__ == "__main__":
    sys.exit(main())
