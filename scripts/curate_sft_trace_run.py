#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any


def _domain_from_session_name(name: str) -> str:
    if " email " in name:
        return "email"
    if " notes " in name:
        return "notes"
    if " calendar " in name:
        return "calendar"
    return "other"


def load_passing_report_sessions(report_path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    sessions: dict[str, dict[str, Any]] = {}
    for row in payload.get("results") or []:
        session_id = str(row.get("session_id") or "")
        if row.get("pass") is True and session_id:
            sessions[session_id] = row
    return sessions


def load_trace_rows(trace_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{trace_path}:{line_no}: invalid JSON: {exc}") from exc
        rows.append(row)
    return rows


def row_runtime_revision(row: dict[str, Any]) -> str:
    direct = str(row.get("runtime_revision") or "").strip()
    if direct:
        return direct
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if isinstance(metadata, dict):
        return str(metadata.get("runtime_revision") or "").strip()
    return ""


def curate_rows(
    rows: list[dict[str, Any]],
    passing_sessions: dict[str, dict[str, Any]],
    *,
    require_thinking: bool = False,
    require_runtime_revision: bool = False,
    expected_runtime_revision: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    curated: list[dict[str, Any]] = []
    seen_sessions: set[str] = set()
    no_thinking_sessions: set[str] = set()
    missing_runtime_revision_sessions: set[str] = set()
    mismatched_runtime_revision_sessions: set[str] = set()
    skipped_no_thinking = 0
    skipped_missing_runtime_revision = 0
    skipped_mismatched_runtime_revision = 0
    duplicate_sessions = 0
    expected_runtime_revision = str(expected_runtime_revision or "").strip()

    for row in rows:
        session_id = str(row.get("session_id") or "")
        if session_id not in passing_sessions:
            continue
        if session_id in seen_sessions:
            duplicate_sessions += 1
            continue
        if require_thinking and not str(row.get("thinking") or "").strip():
            no_thinking_sessions.add(session_id)
            skipped_no_thinking += 1
            continue
        runtime_revision = row_runtime_revision(row)
        if require_runtime_revision and not runtime_revision:
            missing_runtime_revision_sessions.add(session_id)
            skipped_missing_runtime_revision += 1
            continue
        if expected_runtime_revision and runtime_revision != expected_runtime_revision:
            mismatched_runtime_revision_sessions.add(session_id)
            skipped_mismatched_runtime_revision += 1
            continue
        seen_sessions.add(session_id)
        enriched = dict(row)
        enriched["eval_case_id"] = passing_sessions[session_id].get("id")
        enriched["eval_domain"] = passing_sessions[session_id].get("domain")
        if runtime_revision:
            enriched["runtime_revision"] = runtime_revision
        curated.append(enriched)

    missing_sessions = sorted(set(passing_sessions) - seen_sessions)
    missing_without_reason = sorted(
        set(missing_sessions)
        - no_thinking_sessions
        - missing_runtime_revision_sessions
        - mismatched_runtime_revision_sessions
    )
    domains = Counter(str(row.get("eval_domain") or _domain_from_session_name(row.get("session_name") or "")) for row in curated)
    summary = {
        "rows": len(curated),
        "report_passing_sessions": len(passing_sessions),
        "missing_sessions": len(missing_sessions),
        "missing_without_reason": len(missing_without_reason),
        "duplicate_sessions_skipped": duplicate_sessions,
        "skipped_no_thinking": skipped_no_thinking,
        "skipped_missing_runtime_revision": skipped_missing_runtime_revision,
        "skipped_mismatched_runtime_revision": skipped_mismatched_runtime_revision,
        "expected_runtime_revision": expected_runtime_revision,
        "domains": dict(sorted(domains.items())),
        "rows_with_thinking": sum(1 for row in curated if str(row.get("thinking") or "").strip()),
        "rows_with_tool_events": sum(1 for row in curated if row.get("tool_events")),
        "rows_with_runtime_revision": sum(1 for row in curated if row_runtime_revision(row)),
        "missing_session_ids": missing_sessions[:20],
        "missing_without_reason_session_ids": missing_without_reason[:20],
        "missing_runtime_revision_session_ids": sorted(missing_runtime_revision_sessions)[:20],
        "mismatched_runtime_revision_session_ids": sorted(mismatched_runtime_revision_sessions)[:20],
    }
    return curated, summary


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Curate accepted Odysseus SFT traces for one eval report.")
    parser.add_argument("--report", type=Path, required=True, help="Eval actual_results.json path.")
    parser.add_argument("--trace", type=Path, required=True, help="Owner SFT trace JSONL path.")
    parser.add_argument("--out", type=Path, required=True, help="Curated JSONL output path.")
    parser.add_argument("--summary-out", type=Path, default=None, help="Optional summary JSON path.")
    parser.add_argument("--require-thinking", action="store_true", help="Drop passing rows that lack thinking text.")
    parser.add_argument("--require-runtime-revision", action="store_true", help="Drop passing rows that lack runtime revision provenance.")
    parser.add_argument(
        "--runtime-revision",
        default=os.getenv("ODYSSEUS_RUNTIME_REVISION", ""),
        help="Require this exact runtime revision. Defaults to ODYSSEUS_RUNTIME_REVISION.",
    )
    args = parser.parse_args()

    passing_sessions = load_passing_report_sessions(args.report)
    rows = load_trace_rows(args.trace)
    expected_runtime_revision = str(args.runtime_revision or "").strip()
    curated, summary = curate_rows(
        rows,
        passing_sessions,
        require_thinking=args.require_thinking,
        require_runtime_revision=args.require_runtime_revision or bool(expected_runtime_revision),
        expected_runtime_revision=expected_runtime_revision,
    )
    write_jsonl(args.out, curated)
    if args.summary_out:
        args.summary_out.parent.mkdir(parents=True, exist_ok=True)
        args.summary_out.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0 if summary["missing_without_reason"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
