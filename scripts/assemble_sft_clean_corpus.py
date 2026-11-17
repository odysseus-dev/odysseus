#!/usr/bin/env python3
"""Assemble kept and validated repaired sessions into a clean SFT corpus."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--verdicts", type=Path, required=True)
    parser.add_argument("--repairs", type=Path, action="append", default=[])
    parser.add_argument("--out-trace", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in load_jsonl(args.trace):
        source[str(row.get("session_id") or "")].append(row)
    verdicts = {str(row.get("session_id") or ""): row for row in load_jsonl(args.verdicts)}
    repaired: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in args.repairs:
        for row in load_jsonl(path):
            repaired[str(row.get("session_id") or "")].append(row)

    output: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for session_id in sorted(source):
        verdict = verdicts.get(session_id)
        decision = str((verdict or {}).get("verdict") or "missing")
        if decision == "keep":
            output.extend(source[session_id])
            counts["kept"] += 1
        elif decision == "repair" and repaired.get(session_id):
            output.extend(repaired[session_id])
            counts["repaired"] += 1
        else:
            counts["excluded"] += 1
            excluded.append({
                "session_id": session_id,
                "verdict": decision,
                "issues": (verdict or {}).get("issues") or [],
                "repair_missing": decision == "repair" and session_id not in repaired,
            })

    args.out_trace.parent.mkdir(parents=True, exist_ok=True)
    args.out_trace.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in output) + ("\n" if output else ""),
        encoding="utf-8",
    )
    report = {
        "source_sessions": len(source),
        "output_sessions": counts["kept"] + counts["repaired"],
        "output_turns": len(output),
        "decisions": dict(counts),
        "excluded": excluded,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "excluded"}, indent=2))


if __name__ == "__main__":
    main()
