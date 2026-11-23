#!/usr/bin/env python3
"""Authoritative verification of sharded Chromium test coverage.

Discovers expected Playwright test inventory for the current repository
and configuration, and compares it against the union of executed test
identities from all shard reports.

Rejects:
- Missing shards
- Missing reports
- Empty reports
- Malformed reports
- Missing test identities
- Duplicate test identities across or within shards
- Unexpected test identities
- Failed, cancelled, or errored tests
- Unexpected skipped tests
- Shard reports that do not match the expected suite
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def discover_playwright_test_identities(
    repo_root: Path = ROOT,
    config_path: Path | str = "tests/e2e/playwright.config.js",
) -> set[tuple[str, str]]:
    """Discover expected Playwright test identities as (spec_filename, title).

    Uses `npx playwright test --config <config> --list --reporter=json`.
    """
    cmd = [
        "npx",
        "playwright",
        "test",
        "--config",
        str(config_path),
        "--list",
        "--reporter=json",
    ]
    try:
        res = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
    except Exception as exc:
        raise RuntimeError(f"Failed to discover Playwright tests via '{' '.join(cmd)}': {exc}") from exc

    identities: set[tuple[str, str]] = set()

    def extract_suites(suites: list[dict]):
        for suite in suites:
            for spec in suite.get("specs", []):
                file_rel = spec.get("file", "")
                spec_file = Path(file_rel).name
                title = spec.get("title", "")
                for test in spec.get("tests", []):
                    # Each test in spec inherits title
                    identities.add((spec_file, title))
            extract_suites(suite.get("suites", []))

    extract_suites(data.get("suites", []))

    if not identities:
        raise RuntimeError(f"Playwright discovery returned 0 tests from {config_path}")

    return identities


def verify_chromium_shards(
    shard_paths: list[Path],
    expected_identities: set[tuple[str, str]],
    expected_shard_count: int = 4,
) -> tuple[bool, str, dict]:
    """Verify that shard reports match expected identities exactly and disjointly."""
    metadata = {
        "expected_shards": expected_shard_count,
        "provided_shards": len(shard_paths),
        "expected_tests": len(expected_identities),
        "executed_tests": 0,
        "shard_counts": {},
        "missing_identities": [],
        "duplicate_identities": [],
        "unexpected_identities": [],
    }

    if len(shard_paths) != expected_shard_count:
        msg = f"Expected {expected_shard_count} shard reports, found {len(shard_paths)}"
        return False, msg, metadata

    seen_identities: dict[tuple[str, str], Path] = {}
    duplicates: list[tuple[str, str, str, str]] = []  # (spec, title, shard1, shard2)
    executed_identities: set[tuple[str, str]] = set()

    for shard_path in shard_paths:
        shard_name = str(shard_path.relative_to(ROOT)) if shard_path.is_relative_to(ROOT) else str(shard_path)
        if not shard_path.exists():
            return False, f"Shard report file does not exist: {shard_path}", metadata

        try:
            tree = ET.parse(shard_path)
            root = tree.getroot()
        except Exception as exc:
            return False, f"Malformed JUnit XML in {shard_path}: {exc}", metadata

        cases = list(root.iter("testcase"))
        if not cases:
            return False, f"Shard report contains zero test cases: {shard_path}", metadata

        metadata["shard_counts"][shard_name] = len(cases)

        for case in cases:
            classname = case.get("classname") or ""
            name = case.get("name") or ""
            spec_file = Path(classname).name
            identity = (spec_file, name)

            # Check for failed, skipped, or errored
            if case.find("failure") is not None:
                return False, f"Test failure in {shard_name}: {spec_file}::{name}", metadata
            if case.find("error") is not None:
                return False, f"Test error in {shard_name}: {spec_file}::{name}", metadata
            if case.find("skipped") is not None:
                return False, f"Unexpected skipped test in {shard_name}: {spec_file}::{name}", metadata

            # Check duplicate
            if identity in seen_identities:
                prev_shard = seen_identities[identity].name
                duplicates.append((spec_file, name, prev_shard, shard_name))
            else:
                seen_identities[identity] = shard_path
                executed_identities.add(identity)

    metadata["executed_tests"] = len(executed_identities)

    if duplicates:
        metadata["duplicate_identities"] = [
            f"{s}::{t} in {sh1} and {sh2}" for s, t, sh1, sh2 in duplicates
        ]
        return False, f"Found {len(duplicates)} duplicate test execution(s) across shards", metadata

    # Check against expected identities
    missing = expected_identities - executed_identities
    unexpected = executed_identities - expected_identities

    if missing:
        metadata["missing_identities"] = [f"{s}::{t}" for s, t in sorted(missing)]
        return False, f"Missing {len(missing)} required test(s) from shard execution", metadata

    if unexpected:
        metadata["unexpected_identities"] = [f"{s}::{t}" for s, t in sorted(unexpected)]
        return False, f"Found {len(unexpected)} unexpected test(s) in shard execution", metadata

    return (
        True,
        f"Verified all {len(executed_identities)} Chromium tests executed exactly once across {len(shard_paths)} shards.",
        metadata,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "reports",
        nargs="*",
        type=Path,
        help="Paths to shard JUnit XML reports or directories",
    )
    parser.add_argument(
        "--expected-shards",
        type=int,
        default=4,
        help="Expected total number of shards (default: 4)",
    )
    parser.add_argument(
        "--report-name",
        default="browser-junit.xml",
        help="Expected shard JUnit report filename (default: browser-junit.xml)",
    )
    parser.add_argument(
        "--config",
        default="tests/e2e/playwright.config.js",
        help="Path to playwright config file",
    )
    args = parser.parse_args(argv)

    # Collect xml report files
    report_files: list[Path] = []
    for r in args.reports:
        if r.is_dir():
            # Check for standard shard report naming in subdirectories or directory itself
            named_reports = list(r.glob(f"*/{args.report_name}"))
            if not named_reports and (r / args.report_name).is_file():
                named_reports = [r / args.report_name]
            if named_reports:
                report_files.extend(named_reports)
            else:
                direct_xmls = list(r.glob("*.xml"))
                if direct_xmls:
                    report_files.extend(direct_xmls)
                else:
                    report_files.extend(r.glob("**/*.xml"))
        elif r.is_file():
            report_files.append(r)

    # Sort report files for deterministic processing
    report_files = sorted(set(report_files))

    try:
        expected = discover_playwright_test_identities(ROOT, args.config)
    except Exception as exc:
        print(f"::error::Failed test discovery: {exc}", file=sys.stderr)
        return 1

    ok, msg, meta = verify_chromium_shards(
        report_files,
        expected,
        expected_shard_count=args.expected_shards,
    )

    if not ok:
        print(f"::error::{msg}", file=sys.stderr)
        if meta.get("missing_identities"):
            print("Missing identities (first 5):", meta["missing_identities"][:5], file=sys.stderr)
        if meta.get("duplicate_identities"):
            print("Duplicate identities (first 5):", meta["duplicate_identities"][:5], file=sys.stderr)
        if meta.get("unexpected_identities"):
            print("Unexpected identities (first 5):", meta["unexpected_identities"][:5], file=sys.stderr)
        return 1

    print(msg)
    print(f"Shard test distribution: {meta['shard_counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
