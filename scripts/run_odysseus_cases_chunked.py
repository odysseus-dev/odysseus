#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"cases": payload}
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise SystemExit(f"cases file must contain a cases array: {path}")
    return payload


def write_subset(payload: dict[str, Any], cases: list[dict[str, Any]], path: Path) -> None:
    out = dict(payload)
    out["cases"] = cases
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge(payload: dict[str, Any], chunk_paths: list[Path], out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    for path in chunk_paths:
        if not path.exists():
            raise SystemExit(f"missing chunk result: {path}")
        chunk = json.loads(path.read_text(encoding="utf-8"))
        results.extend(chunk.get("results") or [])
        cases.extend(chunk.get("cases") or [])
    summary = {
        "total": len(results),
        "passed": sum(1 for result in results if result.get("pass") is True),
    }
    summary["failed"] = summary["total"] - summary["passed"]
    merged = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base_url,
        "endpoint": args.endpoint,
        "endpoint_id": args.endpoint_id,
        "model": args.model,
        "summary": summary,
        "cases": cases,
        "results": results,
        "source_cases_metadata": {k: v for k, v in payload.items() if k != "cases"},
        "chunk_result_files": [str(path) for path in chunk_paths],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "actual_results.json").write_text(json.dumps(merged, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return merged


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--cases-file", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--email-fixture", action="store_true")
    args = parser.parse_args()

    payload = load_payload(args.cases_file)
    all_cases = payload["cases"]
    chunk_paths: list[Path] = []
    python = ROOT / ".venv/bin/python"
    for start in range(0, len(all_cases), args.chunk_size):
        chunk = all_cases[start:start + args.chunk_size]
        index = start // args.chunk_size
        chunk_dir = args.out_dir / "chunks" / f"chunk_{index:03d}_{start:03d}_{start + len(chunk) - 1:03d}"
        chunk_cases = chunk_dir / "cases.json"
        chunk_result = chunk_dir / "actual_results.json"
        chunk_paths.append(chunk_result)
        if chunk_result.exists() and not args.force:
            print(json.dumps({"chunk": index, "status": "skip", "path": str(chunk_result)}), flush=True)
            continue
        write_subset(payload, chunk, chunk_cases)
        cmd = [
            str(python if python.exists() else sys.executable),
            "scripts/eval_odysseus_app_route_smoke.py",
            "--base-url", args.base_url,
            "--endpoint", args.endpoint,
            "--endpoint-id", args.endpoint_id,
            "--model", args.model,
            "--cases-file", str(chunk_cases),
            "--out-dir", str(chunk_dir),
            "--timeout", str(args.timeout),
            "--write-md",
        ]
        if args.email_fixture:
            cmd.append("--email-fixture")
        print(json.dumps({"chunk": index, "status": "start", "cases": len(chunk), "path": str(chunk_cases)}), flush=True)
        completed = subprocess.run(cmd, cwd=ROOT, check=False)
        if not chunk_result.exists():
            raise SystemExit(f"chunk {index} exited {completed.returncode} without {chunk_result}")
        print(json.dumps({"chunk": index, "status": "done", "returncode": completed.returncode, "path": str(chunk_result)}), flush=True)
    merged = merge(payload, chunk_paths, args.out_dir, args)
    print(json.dumps({"summary": merged["summary"], "json": str(args.out_dir / "actual_results.json")}, indent=2), flush=True)
    return 0 if merged["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
