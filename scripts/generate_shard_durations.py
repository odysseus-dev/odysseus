#!/usr/bin/env python3
"""Generate or update the committed shard duration manifest from pytest execution reports.

This utility parses one or more JUnit XML or JSON test reports, aggregates test
execution durations by repository-relative test file, validates the data, and writes
the deterministic manifest to `tests/.shard_durations.json`.

Usage:
    python scripts/generate_shard_durations.py --input report1.xml report2.xml
    python scripts/generate_shard_durations.py --input report.xml --output tests/.shard_durations.json --merge
    python scripts/generate_shard_durations.py --input shard*/pytest-report.xml --authoritative
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Mapping


MANIFEST_VERSION = 1
DEFAULT_OUTPUT_RELATIVE = "tests/.shard_durations.json"


def normalize_file_key(raw_path: str, repo_root: Path | None = None) -> str | None:
    """Normalize a reported file path into a stable posix repository-relative key.

    Returns None if the path does not represent a valid test file under `tests/`.
    If repo_root is provided, also verifies that the file exists on disk.
    """
    if not raw_path or not isinstance(raw_path, str):
        return None
    p = Path(raw_path.strip())
    if repo_root is not None:
        try:
            if p.is_absolute():
                p = p.resolve().relative_to(repo_root.resolve())
            else:
                p = (repo_root / p).resolve().relative_to(repo_root.resolve())
        except (ValueError, OSError):
            pass

    posix = p.as_posix()
    if posix.startswith("./"):
        posix = posix[2:]

    # Must be a python test file inside tests/
    if posix.startswith("tests/") and posix.endswith(".py"):
        if repo_root is not None and not (repo_root / posix).is_file():
            # If path does not exist directly (e.g. from classname nesting like tests/test_mod/TestClass.py),
            # check parent path components for the actual test file.
            parts = posix.split("/")
            for idx in range(len(parts) - 1, 1, -1):
                candidate = "/".join(parts[:idx]) + ".py"
                if (repo_root / candidate).is_file():
                    return candidate
            if (repo_root / "tests").is_dir():
                return None
        return posix
    return None


def normalize_inventory_key(item: str) -> str | None:
    """Validate and normalize a test file path from a collected inventory."""
    if not isinstance(item, str):
        return None
    norm = item.strip().replace("\\", "/")
    if norm.startswith("./"):
        norm = norm[2:]
    parts = norm.split("/")
    if ".." in parts or "." in parts:
        return None
    if norm.startswith("tests/") and norm.endswith(".py"):
        return norm
    return None


def discover_repo_test_files(repo_root: Path) -> set[str]:
    """Find all Python test files in the repository that pytest collects."""
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        return set()
    test_files: set[str] = set()
    for p in tests_dir.rglob("*.py"):
        name = p.name
        if (name.startswith("test_") or name.endswith("_test.py")) and not name.startswith("_"):
            test_files.add(p.relative_to(repo_root).as_posix())
    return test_files


def find_shard_metadata_path(report_path: Path) -> Path | None:
    """Locate the metadata sidecar for a given report file."""
    candidates = [
        report_path.parent / "pytest-meta.json",
        report_path.with_suffix(".meta.json"),
        report_path.with_name(report_path.stem + "-meta.json"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def load_shard_metadata(meta_path: Path) -> dict[str, object]:
    """Parse and return shard execution metadata from a sidecar file."""
    try:
        text = meta_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"Failed to read metadata sidecar {meta_path}: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Metadata sidecar root must be a dict: {meta_path}")
    return data


def parse_junit_xml(
    xml_path: str | Path,
    repo_root: Path | None = None,
    strict: bool = False,
) -> dict[str, float]:
    """Parse a JUnit XML report and aggregate execution durations by file.

    When strict is True, rejects missing, non-numeric, non-finite, or negative
    times, and unresolvable test files.
    """
    path = Path(xml_path)
    if not path.is_file():
        raise FileNotFoundError(f"Report file not found: {path}")

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except (ET.ParseError, OSError) as e:
        raise ValueError(f"Failed to parse JUnit XML {path}: {e}") from e

    file_durations: dict[str, float] = {}

    for tc in root.iter("testcase"):
        raw_file = tc.get("file")
        if not raw_file:
            classname = tc.get("classname", "")
            if classname:
                raw_file = classname.replace(".", "/") + ".py"

        file_key = normalize_file_key(raw_file, repo_root) if raw_file else None
        if not file_key:
            if strict:
                tc_name = tc.get("name", "unnamed")
                raise ValueError(
                    f"Malformed testcase in {path}: cannot resolve test file for {tc_name} "
                    f"(file={raw_file!r}, classname={tc.get('classname')!r})"
                )
            continue

        raw_time = tc.get("time")
        if raw_time is None or str(raw_time).strip() == "":
            if strict:
                raise ValueError(
                    f"Malformed testcase in {path}: missing required 'time' attribute for {tc.get('name', 'unnamed')}"
                )
            duration = 0.0
        else:
            try:
                duration = float(raw_time)
            except (ValueError, TypeError) as e:
                if strict:
                    raise ValueError(
                        f"Malformed testcase in {path}: invalid time value {raw_time!r} for {tc.get('name', 'unnamed')}"
                    ) from e
                duration = 0.0

        if not math.isfinite(duration) or duration < 0:
            if strict:
                raise ValueError(
                    f"Malformed testcase in {path}: non-finite or negative time {duration} for {tc.get('name', 'unnamed')}"
                )
            duration = 0.0

        file_durations[file_key] = file_durations.get(file_key, 0.0) + duration

    return file_durations


def parse_durations_json(
    json_path: str | Path,
    repo_root: Path | None = None,
    strict: bool = False,
) -> dict[str, float]:
    """Parse a durations JSON file (manifest format or key-value)."""
    path = Path(json_path)
    if not path.is_file():
        raise FileNotFoundError(f"Report file not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"Failed to parse JSON {path}: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")

    raw_durations = data.get("durations", data)
    if not isinstance(raw_durations, dict):
        raise ValueError(f"Missing durations object in {path}")

    durations: dict[str, float] = {}
    for raw_k, v in raw_durations.items():
        k = normalize_file_key(raw_k, repo_root)
        if not k:
            if strict:
                raise ValueError(f"Invalid or unresolvable test file key in {path}: {raw_k!r}")
            continue

        if isinstance(v, bool) or not isinstance(v, (int, float)):
            if strict:
                raise ValueError(f"Invalid duration type for {raw_k} in {path}: {v!r}")
            continue

        val = float(v)
        if not math.isfinite(val) or val < 0:
            if strict:
                raise ValueError(f"Non-finite or negative duration for {raw_k} in {path}: {val}")
            continue

        durations[k] = val

    return durations


def aggregate_reports(
    report_paths: list[str | Path],
    repo_root: Path | None = None,
    strict: bool = False,
) -> dict[str, float]:
    """Aggregate per-file durations across multiple report files."""
    combined: dict[str, float] = {}
    file_to_report: dict[str, Path] = {}
    for r in report_paths:
        p = Path(r)
        if p.suffix.lower() == ".xml":
            parsed = parse_junit_xml(p, repo_root=repo_root, strict=strict)
        elif p.suffix.lower() == ".json":
            parsed = parse_durations_json(p, repo_root=repo_root, strict=strict)
        else:
            raise ValueError(f"Unsupported report format: {p} (expected .xml or .json)")

        for k, dur in parsed.items():
            if strict and k in combined:
                prior = file_to_report[k]
                raise ValueError(
                    f"Overlapping test file detected across shards: {k} appears in both {prior} and {p}"
                )
            combined[k] = combined.get(k, 0.0) + dur
            file_to_report[k] = p

    return combined


def merge_with_existing(
    existing: Mapping[str, float] | None,
    updates: Mapping[str, float],
) -> dict[str, float]:
    """Merge newly measured durations into an existing duration mapping."""
    result = dict(existing or {})
    for k, v in updates.items():
        result[k] = round(float(v), 3)
    return result


def validate_manifest_data(data: Mapping[str, float]) -> bool:
    """Validate that manifest data contains valid keys and non-negative durations."""
    if not isinstance(data, Mapping) or not data:
        return False
    for k, v in data.items():
        if not isinstance(k, str) or not k.startswith("tests/") or not k.endswith(".py"):
            return False
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            return False
    return True


def write_manifest(
    durations: Mapping[str, float],
    output_path: str | Path,
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Write validated per-file durations to a deterministic JSON manifest."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    sorted_durations = {k: round(float(durations[k]), 3) for k in sorted(durations)}
    payload: dict[str, object] = {
        "version": MANIFEST_VERSION,
    }
    if metadata:
        for k, v in metadata.items():
            if v is not None:
                payload[k] = v
    payload["durations"] = sorted_durations

    tmp_path = out.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        "-i",
        nargs="+",
        required=True,
        help="One or more test report files (.xml or .json)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_OUTPUT_RELATIVE,
        help=f"Path to output manifest (default: {DEFAULT_OUTPUT_RELATIVE})",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root directory (default: parent directory of scripts/)",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Merge with existing manifest instead of replacing it",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite even if report contains significantly fewer files than existing manifest",
    )
    parser.add_argument(
        "--min-files",
        type=int,
        default=1,
        help="Minimum number of test files required to consider report complete (default: 1)",
    )
    parser.add_argument(
        "--authoritative",
        action="store_true",
        help="Enforce authoritative regeneration (verifies shard sidecars, git revision, and complete suite coverage)",
    )
    parser.add_argument(
        "--expected-shards",
        type=int,
        default=4,
        help="Expected number of shard reports in authoritative mode (default: 4)",
    )
    parser.add_argument(
        "--revision",
        default=None,
        help="Assert reports match this git revision SHA",
    )
    parser.add_argument(
        "--collected-inventory",
        default=None,
        help="Path to JSON file containing expected collected test file list",
    )
    args = parser.parse_args()

    repo_root = (
        Path(args.repo_root).resolve()
        if args.repo_root
        else Path(__file__).resolve().parent.parent
    )

    provenance_sha = args.revision
    provenance_run_id: str | None = None
    provenance_run_attempt: str | None = None
    authoritative_inventory: set[str] | None = None

    if args.authoritative:
        if len(args.input) < args.expected_shards:
            print(
                f"Error: authoritative regeneration requires at least {args.expected_shards} "
                f"shard reports, but received {len(args.input)}",
                file=sys.stderr,
            )
            return 2

        shard_indices: set[int] = set()
        commit_shas: set[str] = set()
        run_ids: set[str] = set()
        run_attempts: set[str] = set()
        all_sidecar_inventories: list[set[str]] = []

        for r in args.input:
            p = Path(r)
            meta_path = find_shard_metadata_path(p)
            if not meta_path:
                print(
                    f"Error: missing metadata sidecar for shard report: {p}",
                    file=sys.stderr,
                )
                return 2

            try:
                meta = load_shard_metadata(meta_path)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 2

            if meta.get("schema_version") != 1:
                print(
                    f"Error: unsupported metadata schema version in {meta_path}: {meta.get('schema_version')}",
                    file=sys.stderr,
                )
                return 2

            outcome = meta.get("pytest_outcome")
            if outcome != "success":
                print(
                    f"Error: report {p} represents a non-successful shard execution (pytest_outcome={outcome!r})",
                    file=sys.stderr,
                )
                return 2

            shard_str = str(meta.get("shard", "")).strip()
            m = re.match(r"^(\d+)/(\d+)$", shard_str)
            if not m:
                print(
                    f"Error: invalid shard selector in {meta_path}: {shard_str!r}",
                    file=sys.stderr,
                )
                return 2

            idx, count = int(m.group(1)), int(m.group(2))
            if count != args.expected_shards:
                print(
                    f"Error: shard {shard_str} in {meta_path} does not match expected shard count {args.expected_shards}",
                    file=sys.stderr,
                )
                return 2

            if idx in shard_indices:
                print(
                    f"Error: duplicate report detected for shard {idx}/{count}: {p}",
                    file=sys.stderr,
                )
                return 2
            shard_indices.add(idx)

            c_sha = str(meta.get("commit_sha", "")).strip()
            if not c_sha:
                print(f"Error: missing commit_sha in {meta_path}", file=sys.stderr)
                return 2
            commit_shas.add(c_sha)

            r_id = str(meta.get("run_id", "")).strip()
            if not r_id:
                print(f"Error: missing run_id in {meta_path}", file=sys.stderr)
                return 2
            run_ids.add(r_id)

            r_attempt = str(meta.get("run_attempt", "")).strip()
            if not r_attempt:
                print(f"Error: missing or empty run_attempt in {meta_path}", file=sys.stderr)
                return 2
            run_attempts.add(r_attempt)

            if "collected_files" not in meta:
                print(
                    f"Error: missing collected_files inventory in {meta_path}",
                    file=sys.stderr,
                )
                return 2

            collected = meta.get("collected_files")
            if not isinstance(collected, list):
                print(
                    f"Error: collected_files in {meta_path} must be a list, got {type(collected).__name__}",
                    file=sys.stderr,
                )
                return 2

            if not collected:
                print(
                    f"Error: collected_files in {meta_path} is empty",
                    file=sys.stderr,
                )
                return 2

            inventory_set: set[str] = set()
            for item in collected:
                norm_item = normalize_inventory_key(item) if isinstance(item, str) else None
                if not norm_item:
                    print(
                        f"Error: unresolvable or invalid test file path {item!r} in {meta_path}",
                        file=sys.stderr,
                    )
                    return 2
                inventory_set.add(norm_item)

            if not inventory_set:
                print(
                    f"Error: no valid test files found in collected_files in {meta_path}",
                    file=sys.stderr,
                )
                return 2

            all_sidecar_inventories.append(inventory_set)

        if len(commit_shas) > 1:
            print(
                f"Error: reports represent mixed source revisions: {', '.join(sorted(commit_shas))}",
                file=sys.stderr,
            )
            return 5

        if len(run_ids) > 1:
            print(
                f"Error: reports come from different CI runs: {', '.join(sorted(run_ids))}",
                file=sys.stderr,
            )
            return 2

        if len(run_attempts) > 1:
            print(
                f"Error: reports represent mixed CI run attempts: {', '.join(sorted(run_attempts))}",
                file=sys.stderr,
            )
            return 2

        expected_indices = set(range(1, args.expected_shards + 1))
        if shard_indices != expected_indices:
            missing = sorted(expected_indices - shard_indices)
            print(
                f"Error: missing shard reports for shards: {', '.join(f'{i}/{args.expected_shards}' for i in missing)}",
                file=sys.stderr,
            )
            return 2

        detected_sha = next(iter(commit_shas))
        if args.revision and detected_sha != args.revision:
            print(
                f"Error: revision mismatch: reports have {detected_sha} but expected {args.revision}",
                file=sys.stderr,
            )
            return 5

        if len(all_sidecar_inventories) != len(args.input):
            print(
                "Error: not all shard sidecars provided collected_files inventories",
                file=sys.stderr,
            )
            return 2

        first_inv = all_sidecar_inventories[0]
        for idx, other_inv in enumerate(all_sidecar_inventories[1:], 2):
            if other_inv != first_inv:
                diff = first_inv ^ other_inv
                sample = ", ".join(sorted(diff)[:5])
                print(
                    f"Error: sidecar 1 and sidecar {idx} report inconsistent pytest collection inventories: {sample}...",
                    file=sys.stderr,
                )
                return 2

        authoritative_inventory = first_inv

        if args.collected_inventory:
            inv_p = Path(args.collected_inventory)
            if not inv_p.is_file():
                print(f"Error: collected inventory file not found: {inv_p}", file=sys.stderr)
                return 2
            try:
                inv_data = json.loads(inv_p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"Error reading collected inventory from {inv_p}: {e}", file=sys.stderr)
                return 2

            if not isinstance(inv_data, list) or not inv_data:
                print(
                    f"Error: collected inventory file {inv_p} must contain a non-empty list of paths",
                    file=sys.stderr,
                )
                return 2

            cli_inventory = set()
            for item in inv_data:
                norm = normalize_inventory_key(item) if isinstance(item, str) else None
                if not norm:
                    print(f"Error: invalid file key {item!r} in {inv_p}", file=sys.stderr)
                    return 2
                cli_inventory.add(norm)

            if cli_inventory != authoritative_inventory:
                diff = cli_inventory ^ authoritative_inventory
                sample = ", ".join(sorted(diff)[:5])
                print(
                    f"Error: --collected-inventory does not match sidecar collection inventories: {sample}...",
                    file=sys.stderr,
                )
                return 2

        provenance_sha = detected_sha
        provenance_run_id = next(iter(run_ids))
        provenance_run_attempt = next(iter(run_attempts))

    try:
        new_durations = aggregate_reports(
            args.input,
            repo_root=repo_root,
            strict=args.authoritative,
        )
    except Exception as e:
        print(f"Error parsing reports: {e}", file=sys.stderr)
        return 1

    if len(new_durations) < args.min_files:
        print(
            f"Error: report contained only {len(new_durations)} files, below minimum {args.min_files}",
            file=sys.stderr,
        )
        return 2

    if args.authoritative:
        expected_test_files = authoritative_inventory
        if not expected_test_files:
            print("Error: authoritative collection inventory is empty", file=sys.stderr)
            return 2
        missing_files = expected_test_files - set(new_durations.keys())
        if missing_files:
            sample = ", ".join(sorted(missing_files)[:5])
            print(
                f"Error: authoritative report set is missing {len(missing_files)} "
                f"pytest test files out of {len(expected_test_files)} total: {sample}...",
                file=sys.stderr,
            )
            return 2
        unexpected_files = set(new_durations.keys()) - expected_test_files
        if unexpected_files:
            sample = ", ".join(sorted(unexpected_files)[:5])
            print(
                f"Error: authoritative report set contains {len(unexpected_files)} "
                f"unexpected test files not in collected inventory: {sample}...",
                file=sys.stderr,
            )
            return 2

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = repo_root / out_path

    if out_path.is_file() and not args.merge and not args.force:
        try:
            existing = parse_durations_json(out_path, repo_root=repo_root)
            if len(new_durations) < len(existing) * 0.5:
                print(
                    f"Error: new report contains {len(new_durations)} files but existing manifest has "
                    f"{len(existing)} files. Use --merge to update or --force to overwrite.",
                    file=sys.stderr,
                )
                return 4
        except Exception:
            pass

    final_durations = new_durations
    if args.merge and out_path.is_file():
        try:
            existing = parse_durations_json(out_path, repo_root=repo_root)
            final_durations = merge_with_existing(existing, new_durations)
        except Exception as e:
            print(f"Warning: could not read existing manifest ({e}), writing fresh", file=sys.stderr)

    if not validate_manifest_data(final_durations):
        print("Error: validated duration data is empty or invalid", file=sys.stderr)
        return 3

    metadata = {
        "revision": provenance_sha,
        "run_id": provenance_run_id,
        "run_attempt": provenance_run_attempt,
        "shard_count": len(args.input),
        "test_files_count": len(final_durations),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_manifest(final_durations, out_path, metadata=metadata)
    print(f"Wrote {len(final_durations)} file durations to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
