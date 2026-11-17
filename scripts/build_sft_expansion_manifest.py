#!/usr/bin/env python3
"""Freeze approved Alex traces into seed families for environment expansion."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT = ROOT / "data/audits/sft_corpus_deepseek_audit_live_complete_20260830/deepseek_verdicts.jsonl"
DEFAULT_REPAIRS = ROOT / "data/audits/sft_corpus_kimi_repairs_live_20260830/apply_manifest.json"
DEFAULT_LATER_AUDITS = [
    ROOT / "data/audits/sft_corpus_deepseek_audit_20260830_104907/deepseek_verdicts.jsonl",
    ROOT / "data/audits/sft_corpus_deepseek_audit_20260830_105208/deepseek_verdicts.jsonl",
    ROOT / "data/audits/sft_corpus_deepseek_audit_20260830_105713/deepseek_verdicts.jsonl",
    ROOT / "data/audits/sft_corpus_deepseek_audit_20260830_110443/deepseek_verdicts.jsonl",
    ROOT / "data/audits/sft_corpus_deepseek_audit_20260830_121408/deepseek_verdicts.jsonl",
]

OWNER_BOUND_MARKERS = (
    "email", "calendar", "note", "memory", "document", "task", "skill", "session",
    "contact", "research", "gallery", "image", "settings", "webhook", "token", "endpoint", "mcp",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stable_split(seed_family_id: str) -> str:
    bucket = int(hashlib.sha256(seed_family_id.encode()).hexdigest()[:8], 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "validation"
    return "test"


def turn_digest(row: dict[str, Any]) -> str:
    payload = [row.get("user"), row.get("assistant"), row.get("thinking"), row.get("tool_events")]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def approved_sessions(base_audit: Path, repairs: Path, later_audits: list[Path]) -> tuple[set[str], dict[str, str]]:
    base = read_jsonl(base_audit)
    approved = {str(row["session_id"]) for row in base if row.get("verdict") == "keep"}
    provenance = {str(row["session_id"]): "deepseek_complete_keep" for row in base if row.get("verdict") == "keep"}
    repair_manifest = json.loads(repairs.read_text(encoding="utf-8"))
    for sid in repair_manifest.get("accepted_session_ids") or []:
        approved.add(str(sid))
        provenance[str(sid)] = "kimi_repair_deepseek_keep"
    for path in later_audits:
        if not path.exists():
            continue
        for row in read_jsonl(path):
            sid = str(row.get("session_id") or "")
            if row.get("verdict") == "keep" and sid:
                approved.add(sid)
                provenance[sid] = f"later_deepseek_keep:{path.parent.name}"
    return approved, provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, default=ROOT / "data/sft_traces/sft_alex_creator.jsonl")
    parser.add_argument("--base-audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--repair-manifest", type=Path, default=DEFAULT_REPAIRS)
    parser.add_argument("--later-audit", type=Path, action="append", default=[])
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    later = args.later_audit or DEFAULT_LATER_AUDITS
    approved, provenance = approved_sessions(args.base_audit, args.repair_manifest, later)
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(args.trace):
        sid = str(row.get("session_id") or "")
        if sid in approved:
            by_session[sid].append(row)

    manifest_rows = []
    frozen_rows = []
    duplicate_turns = 0
    tools = Counter()
    split_counts = Counter()
    for sid in sorted(by_session):
        unique = []
        seen = set()
        for row in by_session[sid]:
            digest = turn_digest(row)
            if digest in seen:
                duplicate_turns += 1
                continue
            seen.add(digest)
            unique.append(row)
        if not unique:
            continue
        actual_tools = sorted({
            str(event.get("tool"))
            for row in unique for event in (row.get("tool_events") or []) if event.get("tool")
        })
        for tool in actual_tools:
            tools[tool] += 1
        owner_bound = any(any(marker in tool.lower() for marker in OWNER_BOUND_MARKERS) for tool in actual_tools)
        family_id = f"alex:{sid}"
        split = stable_split(family_id)
        split_counts[split] += 1
        manifest_rows.append({
            "seed_family_id": family_id,
            "source_owner": "sft_alex_creator",
            "source_session_id": sid,
            "session_name": unique[0].get("session_name"),
            "approval_provenance": provenance.get(sid),
            "split": split,
            "owner_bound": owner_bound,
            "tools": actual_tools,
            "turn_count": len(unique),
            "turns": [
                {
                    "message_id": row.get("message_id"),
                    "user": row.get("user"),
                    "assistant": row.get("assistant"),
                    "thinking": row.get("thinking"),
                    "tool_events": row.get("tool_events") or [],
                }
                for row in unique
            ],
        })
        for row in unique:
            copied = dict(row)
            metadata = dict(copied.get("metadata") or {})
            metadata.update({"seed_family_id": family_id, "dataset_split": split, "approval_provenance": provenance.get(sid)})
            copied["metadata"] = metadata
            frozen_rows.append(copied)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "seed_manifest.json").write_text(json.dumps({"seeds": manifest_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out_dir / "approved_trace.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in frozen_rows),
        encoding="utf-8",
    )
    summary = {
        "approved_ids": len(approved),
        "approved_sessions_present": len(manifest_rows),
        "approved_turns": len(frozen_rows),
        "missing_approved_sessions": len(approved - set(by_session)),
        "duplicate_turns_removed": duplicate_turns,
        "owner_bound_sessions": sum(bool(row["owner_bound"]) for row in manifest_rows),
        "global_sessions": sum(not bool(row["owner_bound"]) for row in manifest_rows),
        "splits": dict(split_counts),
        "tool_session_counts": dict(tools.most_common()),
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
