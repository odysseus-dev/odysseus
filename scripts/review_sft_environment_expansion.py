#!/usr/bin/env python3
"""Semantically review expansion runs and retain only independently approved sessions."""

from __future__ import annotations
import os

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
TRACE_DIR = ROOT / "data" / "sft_traces"


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp = Path(handle.name)
    temp.replace(path)


def trace_rows(owner: str, session_ids: set[str]) -> list[dict[str, Any]]:
    path = TRACE_DIR / f"{owner}.jsonl"
    if not path.exists():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            row = json.loads(raw)
            if str(row.get("session_id") or "") in session_ids:
                rows.append(row)
    return rows


def remove_trace_sessions(owner: str, session_ids: set[str]) -> int:
    path = TRACE_DIR / f"{owner}.jsonl"
    if not path.exists() or not session_ids:
        return 0
    kept: list[str] = []
    removed = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        if str(row.get("session_id") or "") in session_ids:
            removed += 1
        else:
            kept.append(json.dumps(row, ensure_ascii=False))
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write("\n".join(kept) + ("\n" if kept else ""))
        temp = Path(handle.name)
    temp.replace(path)
    return removed


def delete_live_sessions(base_url: str, password: str, by_owner: dict[str, set[str]]) -> None:
    for owner, session_ids in by_owner.items():
        with httpx.Client() as client:
            response = client.post(
                base_url.rstrip("/") + "/api/auth/login",
                json={"username": owner, "password": password, "remember": True},
                timeout=30,
            )
            response.raise_for_status()
            for session_id in session_ids:
                response = client.delete(
                    base_url.rstrip("/") + f"/api/session/{session_id}", timeout=30
                )
                response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:7011")
    parser.add_argument("--password", default=os.environ.get("ODYSSEUS_QA_PASSWORD"), required=os.environ.get("ODYSSEUS_QA_PASSWORD") is None)
    parser.add_argument("--min-score", type=int, default=80)
    parser.add_argument("--endpoint-id")
    args = parser.parse_args()

    results = json.loads(args.run.read_text(encoding="utf-8")).get("results", [])
    candidates = [row for row in results if row.get("pass") is True and row.get("session_id")]
    owner_by_session = {str(row["session_id"]): str(row["owner"]) for row in candidates}
    review_rows: list[dict[str, Any]] = []
    for owner in sorted(set(owner_by_session.values())):
        ids = {sid for sid, candidate_owner in owner_by_session.items() if candidate_owner == owner}
        review_rows.extend(trace_rows(owner, ids))
    if not review_rows:
        atomic_json(args.out, {"reviewed": 0, "kept": 0, "rejected": 0, "results": []})
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    review_trace = args.out.with_suffix(".review.jsonl")
    review_trace.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in review_rows) + "\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(ROOT / "scripts" / "audit_sft_corpus_with_deepseek.py"),
        "--trace", str(review_trace),
        "--all-sessions", "--workers", "1", "--batch-size", "1",
    ]
    if args.endpoint_id:
        command.extend(["--endpoint-id", args.endpoint_id])
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    output_line = next(
        line for line in reversed(completed.stdout.splitlines()) if line.startswith("output=")
    )
    audit_dir = Path(output_line.split("=", 1)[1])
    verdicts = [
        json.loads(line)
        for line in (audit_dir / "deepseek_verdicts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    verdict_by_session = {str(row["session_id"]): row for row in verdicts}
    rejected = {
        sid for sid in owner_by_session
        if sid not in verdict_by_session
        or verdict_by_session[sid].get("verdict") != "keep"
        or int(verdict_by_session[sid].get("score") or 0) < args.min_score
    }
    rejected_by_owner: dict[str, set[str]] = {}
    for sid in rejected:
        rejected_by_owner.setdefault(owner_by_session[sid], set()).add(sid)
    if rejected_by_owner:
        delete_live_sessions(args.base_url, args.password, rejected_by_owner)
        for owner, session_ids in rejected_by_owner.items():
            remove_trace_sessions(owner, session_ids)
        for result in results:
            if str(result.get("session_id") or "") in rejected:
                result["pass"] = False
                result.setdefault("failures", []).append("semantic_review_rejected")
        atomic_json(args.run, {"results": results})

    report = {
        "reviewed": len(owner_by_session),
        "kept": len(owner_by_session) - len(rejected),
        "rejected": len(rejected),
        "audit_dir": str(audit_dir),
        "results": [
            {
                **row,
                "owner": owner_by_session.get(str(row.get("session_id") or "")),
                "retained": str(row.get("session_id") or "") not in rejected,
            }
            for row in verdicts
        ],
    }
    atomic_json(args.out, report)
    print(json.dumps({key: report[key] for key in ("reviewed", "kept", "rejected")}, indent=2))


if __name__ == "__main__":
    main()
