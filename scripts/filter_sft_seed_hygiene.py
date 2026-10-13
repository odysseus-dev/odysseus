#!/usr/bin/env python3
"""Create a non-destructive, style-clean SFT seed corpus and hygiene report."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


META_RE = re.compile(
    r"\b(?:sft|fixture|harness|synthetic|training trace|domain audit|audit fixture|smoke test)\b",
    re.I,
)
MARKER_RE = re.compile(
    r"(?:audit-fixture|EXP-|\{marker\}|202608\d{2}[_-]\d{6}-[0-9a-f]{6,})",
    re.I,
)


def reasons_for_session(rows: list[dict[str, Any]]) -> list[str]:
    reasons: set[str] = set()
    for row in rows:
        user = str(row.get("user") or "")
        assistant = str(row.get("assistant") or "")
        tool_events = row.get("tool_events") or []
        if META_RE.search(user):
            reasons.add("meta_user")
        if META_RE.search(assistant):
            reasons.add("meta_assistant")
        if MARKER_RE.search(" ".join((user, assistant, json.dumps(tool_events, ensure_ascii=False)))):
            reasons.add("marker_or_run_id")
        if not tool_events and len(assistant) > 500:
            reasons.add("long_answer_without_tool")
    return sorted(reasons)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--out-trace", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in args.trace.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            by_session[str(row.get("session_id") or "")].append(row)

    rejected: list[dict[str, Any]] = []
    kept_rows: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for session_id, rows in sorted(by_session.items()):
        reasons = reasons_for_session(rows)
        if reasons:
            rejected.append({
                "session_id": session_id,
                "session_name": rows[0].get("session_name"),
                "turns": len(rows),
                "reasons": reasons,
            })
            reason_counts.update(reasons)
        else:
            kept_rows.extend(rows)

    args.out_trace.parent.mkdir(parents=True, exist_ok=True)
    args.out_trace.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in kept_rows) + ("\n" if kept_rows else ""),
        encoding="utf-8",
    )
    report = {
        "source": str(args.trace),
        "sessions": len(by_session),
        "kept_sessions": len(by_session) - len(rejected),
        "rejected_sessions": len(rejected),
        "kept_turns": len(kept_rows),
        "reason_counts": dict(reason_counts),
        "rejected": rejected,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("sessions", "kept_sessions", "rejected_sessions", "kept_turns", "reason_counts")}, indent=2))


if __name__ == "__main__":
    main()
