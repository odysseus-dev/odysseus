#!/usr/bin/env python3
"""Build the oversized test-file split plan for issue #3983.

The output is a planning document only. It does not move tests, rewrite
assertions, extract helpers, or change CI.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = ROOT / "tests"
OUTPUT = TESTS_DIR / "OVERSIZED_TEST_SPLIT_PLAN.md"
RAW_OUTPUT = Path("/tmp/oversized-test-file-metrics.json")

LARGE_LINE_THRESHOLD = 300
LARGE_NODE_THRESHOLD = 20
TOP_LIMIT = 30

HIGH_RISK_SIGNALS = {"route/api", "db/session", "import-state", "security"}


@dataclass(frozen=True)
class FileMetric:
    path: str
    lines: int
    nonblank: int
    test_defs: int
    test_classes: int
    collected: int
    area: str
    sub_area: str
    signals: tuple[str, ...]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def count_ast_tests(text: str) -> tuple[int, int]:
    tree = ast.parse(text)
    test_defs = 0
    test_classes = 0

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                test_defs += 1
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("Test"):
                test_classes += 1

    return test_defs, test_classes


def load_taxonomy_classifier():
    sys.path.insert(0, str(ROOT))
    from tests._taxonomy import classify_test_path

    return classify_test_path


def classify(path: Path, classify_test_path) -> tuple[str, str]:
    rel_path = Path(path.relative_to(ROOT).as_posix())

    try:
        result = classify_test_path(rel_path)
    except Exception:
        return "unknown", "unknown"

    return getattr(result, "area", "unknown"), getattr(result, "sub_area", "unknown")


def collect_node_counts() -> Counter[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "tests",
    ]
    env = dict(os.environ)
    env["PY_COLORS"] = "0"

    result = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)

    counts: Counter[str] = Counter()
    for line in result.stdout.splitlines():
        line = line.strip()
        if "::" not in line:
            continue
        if not line.startswith("tests/"):
            continue
        file_path = line.split("::", 1)[0]
        counts[file_path] += 1

    return counts


def detect_signals(text: str, path: str) -> tuple[str, ...]:
    signal_patterns = {
        "route/api": [
            r"\bTestClient\b",
            r"\bapp\.",
            r"\broutes\.",
            r"\bfrom routes\b",
            r"\bimport routes\b",
        ],
        "db/session": [
            r"\bSessionLocal\b",
            r"\bsqlite\b",
            r"\bDATABASE_URL\b",
            r"\bcore\.database\b",
            r"\bdb\.query\b",
            r"\bcommit\(",
        ],
        "import-state": [
            r"\bsys\.modules\b",
            r"\bimportlib\b",
            r"\bclear_module\b",
            r"\bpreserve_import_state\b",
            r"\bmonkeypatch\.setitem\b",
        ],
        "security": [
            r"\bsecurity\b",
            r"\bssrf\b",
            r"\bpath traversal\b",
            r"\bcsrf\b",
            r"\bpermission\b",
        ],
        "filesystem": [
            r"\btmp_path\b",
            r"\bTemporaryDirectory\b",
            r"\bPath\(",
            r"\bmkdir\b",
            r"\bwrite_text\b",
            r"\bread_text\b",
        ],
        "subprocess/script": [
            r"\bsubprocess\b",
            r"\brunpy\b",
            r"\bload_script\b",
            r"\bsys\.argv\b",
        ],
        "async/threading": [
            r"\basyncio\b",
            r"\bthreading\b",
            r"\bconcurrent\.futures\b",
            r"\bThreadPoolExecutor\b",
        ],
        "ui/static": [
            r"\bstatic/",
            r"\bjsdom\b",
            r"\bnode\b",
            r"\.js\b",
        ],
    }

    signals = []
    for name, patterns in signal_patterns.items():
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            signals.append(name)

    if path.startswith("tests/cli/"):
        signals.append("cli-directory")

    return tuple(signals)


def metric_for(path: Path, node_counts: Counter[str], classify_test_path) -> FileMetric:
    rel = path.relative_to(ROOT).as_posix()
    text = read_text(path)
    lines = len(text.splitlines())
    nonblank = sum(1 for line in text.splitlines() if line.strip())
    test_defs, test_classes = count_ast_tests(text)
    area, sub_area = classify(path, classify_test_path)

    return FileMetric(
        path=rel,
        lines=lines,
        nonblank=nonblank,
        test_defs=test_defs,
        test_classes=test_classes,
        collected=node_counts.get(rel, 0),
        area=area,
        sub_area=sub_area,
        signals=detect_signals(text, rel),
    )


def test_files() -> list[Path]:
    return sorted(TESTS_DIR.rglob("test_*.py"))


def as_metric_row(metric: FileMetric) -> str:
    signals = ", ".join(metric.signals) if metric.signals else "-"
    return (
        f"| `{metric.path}` | {metric.lines} | {metric.collected} | "
        f"{metric.test_defs} | {metric.test_classes} | "
        f"{metric.area} | {metric.sub_area} | {signals} |"
    )


def metric_table(title: str, metrics: list[FileMetric]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| File | Lines | Collected tests | Test defs | Test classes | Area | Sub-area | Signals |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    lines.extend(as_metric_row(metric) for metric in metrics)
    lines.append("")
    return lines


def candidate_metrics(metrics: list[FileMetric]) -> list[FileMetric]:
    return [
        metric
        for metric in metrics
        if metric.lines >= LARGE_LINE_THRESHOLD
        or metric.collected >= LARGE_NODE_THRESHOLD
    ]


def include_reasons(metric: FileMetric) -> str:
    reasons = []
    if metric.lines >= LARGE_LINE_THRESHOLD:
        reasons.append(f"{metric.lines} lines")
    if metric.collected >= LARGE_NODE_THRESHOLD:
        reasons.append(f"{metric.collected} collected tests")
    return ", ".join(reasons)


def risk_notes(metric: FileMetric) -> str:
    if not metric.signals:
        return "No obvious setup signals from static scan."
    return ", ".join(metric.signals)


def suggested_handling(metric: FileMetric) -> str:
    if HIGH_RISK_SIGNALS.intersection(metric.signals):
        return "Defer mechanical split until setup/risk boundaries are mapped."
    if metric.collected >= LARGE_NODE_THRESHOLD:
        return "Good first manual-review candidate if test themes are cohesive."
    return "Plan split boundaries before editing."


def candidate_section(metrics: list[FileMetric]) -> list[str]:
    lines = [
        "## Split planning candidates",
        "",
        "This section is generated from metrics, not from manual judgement.",
        "Files are included when they meet at least one threshold:",
        "",
        f"- at least {LARGE_LINE_THRESHOLD} physical lines; or",
        f"- at least {LARGE_NODE_THRESHOLD} collected pytest items.",
        "",
        "These are planning candidates only. A later split PR still needs a focused manual review of each file before moving tests.",
        "",
        "| File | Why included | Setup/risk signals | Suggested handling |",
        "|---|---|---|---|",
    ]

    for metric in metrics:
        lines.append(
            f"| `{metric.path}` | {include_reasons(metric)} | "
            f"{risk_notes(metric)} | {suggested_handling(metric)} |"
        )

    lines.append("")
    return lines


def first_manual_review_section(metrics: list[FileMetric]) -> list[str]:
    low_risk = [
        metric
        for metric in metrics
        if metric.area != "uncategorized"
        and not HIGH_RISK_SIGNALS.intersection(metric.signals)
    ]
    low_risk = sorted(low_risk, key=lambda m: (m.collected, m.lines), reverse=True)

    lines = [
        "## Suggested first manual-review candidates",
        "",
        "These are not automatic split approvals. They are categorized candidates with enough size/collection value and no route/API, DB/session, import-state, or security signal from the static scan.",
        "",
        "Files still in the `uncategorized` taxonomy area are listed separately below so taxonomy review does not get mixed into the first split decision.",
        "",
        "| File | Lines | Collected tests | Area | Sub-area | Signals | Why this is a candidate |",
        "|---|---:|---:|---|---|---|---|",
    ]

    if not low_risk:
        lines.append("| _None_ | - | - | - | - | - | - |")

    for metric in low_risk[:10]:
        signals = ", ".join(metric.signals) if metric.signals else "-"
        lines.append(
            f"| `{metric.path}` | {metric.lines} | {metric.collected} | "
            f"{metric.area} | {metric.sub_area} | {signals} | {include_reasons(metric)} |"
        )

    lines.append("")
    return lines


def taxonomy_gap_section(metrics: list[FileMetric]) -> list[str]:
    uncategorized = [
        metric
        for metric in metrics
        if metric.area == "uncategorized"
    ]
    uncategorized = sorted(
        uncategorized,
        key=lambda m: (m.collected, m.lines),
        reverse=True,
    )

    lines = [
        "## Taxonomy coverage gaps among split candidates",
        "",
        "`uncategorized` is a current taxonomy area, not a builder failure.",
        "This plan does not reclassify tests because taxonomy changes should be reviewed separately from oversized-file split planning.",
        "",
        "Before using any of these files as a split target, first decide whether the taxonomy should be refined in a separate focused issue/PR.",
        "",
        "| File | Lines | Collected tests | Sub-area | Signals | Suggested follow-up |",
        "|---|---:|---:|---|---|---|",
    ]

    if not uncategorized:
        lines.append("| _None_ | - | - | - | - | - |")

    for metric in uncategorized:
        signals = ", ".join(metric.signals) if metric.signals else "-"
        follow_up = "Review taxonomy mapping before using as a split target."
        if HIGH_RISK_SIGNALS.intersection(metric.signals):
            follow_up = "Review taxonomy and setup/risk boundaries before any split."
        lines.append(
            f"| `{metric.path}` | {metric.lines} | {metric.collected} | "
            f"{metric.sub_area} | {signals} | {follow_up} |"
        )

    lines.append("")
    return lines


def deferred_section(metrics: list[FileMetric]) -> list[str]:
    deferred = [
        metric
        for metric in metrics
        if HIGH_RISK_SIGNALS.intersection(metric.signals)
    ]
    deferred = sorted(deferred, key=lambda m: (m.collected, m.lines), reverse=True)

    lines = [
        "## High-risk candidates to defer first",
        "",
        "These files may still be split later, but not as the first implementation slice without a separate manual boundary review.",
        "",
        "| File | Lines | Collected tests | High-risk signals |",
        "|---|---:|---:|---|",
    ]

    for metric in deferred[:15]:
        signals = ", ".join(sorted(HIGH_RISK_SIGNALS.intersection(metric.signals)))
        lines.append(
            f"| `{metric.path}` | {metric.lines} | {metric.collected} | {signals} |"
        )

    lines.append("")
    return lines


def write_distribution(
    lines: list[str],
    title: str,
    values: Counter[str],
    *,
    min_count: int = 1,
) -> None:
    displayed = [
        (value, count)
        for value, count in sorted(values.items())
        if count >= min_count
    ]
    omitted_values = sum(1 for count in values.values() if count < min_count)
    omitted_files = sum(count for count in values.values() if count < min_count)

    lines.extend([
        f"{title}:",
        "",
        "| Value | Files |",
        "|---|---:|",
    ])
    for value, count in displayed:
        lines.append(f"| {value} | {count} |")

    if omitted_values:
        lines.extend([
            "",
            f"Values below {min_count} files: {omitted_values} values covering {omitted_files} files.",
        ])

    lines.append("")


def write_report(metrics: list[FileMetric], node_count_total: int) -> None:
    by_lines = sorted(metrics, key=lambda m: (m.lines, m.collected), reverse=True)
    by_collected = sorted(metrics, key=lambda m: (m.collected, m.lines), reverse=True)
    candidates = sorted(
        candidate_metrics(metrics),
        key=lambda m: (m.collected, m.lines),
        reverse=True,
    )

    areas = Counter(metric.area for metric in metrics)
    sub_areas = Counter(metric.sub_area for metric in metrics)

    lines = [
        "# Oversized Test File Split Plan",
        "",
        "## Purpose",
        "",
        "This document plans future oversized test-file splits using current repo data.",
        "It does not move files, rewrite assertions, extract helpers, or change CI.",
        "",
        "## Roadmap context",
        "",
        "- Issue: #3983",
        "- Parent tracker: #2523",
        "- Follows #3973 / #3982, the report-only order-sensitivity diagnostics slice.",
        "",
        "## Methodology",
        "",
        "Metrics were generated from the current test tree using:",
        "",
        "- physical line counts for every recursive `test_*.py` file under `tests/`;",
        "- AST counts for `test_*` functions and `Test*` classes;",
        "- one `pytest --collect-only -q tests` run to count collected items per file;",
        "- current taxonomy classification from `tests._taxonomy.classify_test_path`; and",
        "- static setup-signal scans for route/API, DB/session, import-state, security, filesystem, subprocess/script, async/threading, and UI/static indicators.",
        "",
        "Static signals are not proof of risk. They are review prompts.",
        "Future split PRs must still inspect each file manually before editing.",
        "",
        "## Current summary",
        "",
        f"- test files scanned: {len(metrics)}",
        f"- collected pytest items counted: {node_count_total}",
        f"- large-file threshold: {LARGE_LINE_THRESHOLD} lines",
        f"- large-collected threshold: {LARGE_NODE_THRESHOLD} collected items",
        "",
    ]

    write_distribution(lines, "Area distribution", areas)
    write_distribution(lines, "Sub-area distribution", sub_areas, min_count=2)

    lines.extend(metric_table("Top files by collected pytest items", by_collected[:TOP_LIMIT]))
    lines.extend(metric_table("Top files by physical line count", by_lines[:TOP_LIMIT]))
    lines.extend(candidate_section(candidates))
    lines.extend(taxonomy_gap_section(candidates))
    lines.extend(first_manual_review_section(candidates))
    lines.extend(deferred_section(candidates))

    lines.extend([
        "## Rules for future split PRs",
        "",
        "- One file or one coherent file-family per PR.",
        "- No assertion rewrites mixed with file moves.",
        "- No helper extraction mixed with file moves.",
        "- No production code changes.",
        "- No CI workflow changes.",
        "- Preserve existing markers and taxonomy unless the split issue explicitly says otherwise.",
        "- Validate the original file's collected tests before and after the split.",
        "- Validate any neighboring taxonomy/focused-runner behavior if paths change.",
        "- Treat files with route/API, DB/session, import-state, or security signals as higher-risk until manually reviewed.",
        "",
        "## Suggested next step",
        "",
        "Use this plan to choose the first actual oversized-file split issue.",
        "The first split should prefer a file with high review value and low setup risk.",
        "Do not start a split PR from this planning issue alone if the file's boundaries are still ambiguous.",
        "",
        "## Reproduction command",
        "",
        "This document was generated with:",
        "",
        "```bash",
        ".venv/bin/python tests/tools/build_oversized_test_split_plan.py",
        "```",
        "",
        "## Freshness check",
        "",
        "After editing the builder or rebasing the branch, regenerate the plan and confirm no unexpected plan drift:",
        "",
        "```bash",
        ".venv/bin/python tests/tools/build_oversized_test_split_plan.py",
        "git diff --exit-code -- tests/OVERSIZED_TEST_SPLIT_PLAN.md",
        "```",
        "",
    ])

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")


def write_raw(metrics: list[FileMetric]) -> None:
    raw = [
        {
            "area": metric.area,
            "collected": metric.collected,
            "lines": metric.lines,
            "nonblank": metric.nonblank,
            "path": metric.path,
            "signals": list(metric.signals),
            "sub_area": metric.sub_area,
            "test_classes": metric.test_classes,
            "test_defs": metric.test_defs,
        }
        for metric in metrics
    ]
    RAW_OUTPUT.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")


def assert_taxonomy_worked(metrics: list[FileMetric]) -> None:
    if not metrics:
        raise SystemExit("ERROR: no test files were scanned")

    unknown = sum(1 for metric in metrics if metric.area == "unknown")
    if unknown == len(metrics):
        raise SystemExit("ERROR: taxonomy classification returned unknown for every file")


def main() -> int:
    if not TESTS_DIR.exists():
        print("ERROR: tests/ directory not found", file=sys.stderr)
        return 1

    classify_test_path = load_taxonomy_classifier()
    node_counts = collect_node_counts()
    metrics = [metric_for(path, node_counts, classify_test_path) for path in test_files()]

    assert_taxonomy_worked(metrics)
    write_report(metrics, sum(node_counts.values()))
    write_raw(metrics)

    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
    print(f"Wrote {RAW_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
