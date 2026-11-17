#!/usr/bin/env python3
"""Build family-safe train/validation/test JSONL files from approved seeds and expansions."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approved-trace", type=Path, required=True)
    parser.add_argument("--review", type=Path, action="append", default=[])
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    split_by_family = {
        str(seed["seed_family_id"]): str(seed["split"])
        for seed in manifest["seeds"]
    }
    retained_sessions: set[str] = set()
    for review_path in args.review:
        report = json.loads(review_path.read_text(encoding="utf-8"))
        retained_sessions.update(
            str(item["session_id"])
            for item in report.get("results", [])
            if item.get("retained") is True
        )

    corpus = rows(args.approved_trace)
    if retained_sessions:
        owners = sorted({
            str(item.get("owner") or "")
            for review_path in args.review
            for item in json.loads(review_path.read_text(encoding="utf-8")).get("results", [])
            if item.get("retained") is True
        })
        for owner in owners:
            path = ROOT / "data" / "sft_traces" / f"{owner}.jsonl"
            if not path.exists():
                continue
            corpus.extend(
                row for row in rows(path)
                if str(row.get("session_id") or "") in retained_sessions
            )

    seen_messages: set[str] = set()
    split_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    family_splits: dict[str, set[str]] = defaultdict(set)
    for row in corpus:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        family = str(
            metadata.get("seed_family_id")
            or row.get("seed_family_id")
            or f"seed:{row.get('session_id')}"
        )
        split = str(
            metadata.get("dataset_split")
            or row.get("dataset_split")
            or split_by_family.get(family)
            or "train"
        )
        if split not in {"train", "validation", "test"}:
            raise ValueError(f"invalid split {split!r} for family {family}")
        signature = json.dumps(
            [row.get("user"), row.get("assistant"), row.get("tool_events")],
            sort_keys=True,
            ensure_ascii=False,
        )
        if signature in seen_messages:
            continue
        seen_messages.add(signature)
        family_splits[family].add(split)
        split_rows[split].append(row)
    leaked = {family: values for family, values in family_splits.items() if len(values) > 1}
    if leaked:
        raise ValueError(f"seed-family split leakage: {leaked}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for split in ("train", "validation", "test"):
        path = args.out_dir / f"{split}.jsonl"
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in split_rows[split])
            + ("\n" if split_rows[split] else ""),
            encoding="utf-8",
        )
    summary = {
        "turns": {split: len(split_rows[split]) for split in ("train", "validation", "test")},
        "sessions": len({str(row.get("session_id")) for row in corpus}),
        "families": len(family_splits),
        "retained_expansion_sessions": len(retained_sessions),
        "family_leaks": 0,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary["turns"], indent=2))


if __name__ == "__main__":
    main()
