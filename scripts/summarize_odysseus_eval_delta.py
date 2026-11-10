#!/usr/bin/env python3
"""Summarize Odysseus tool-use eval artifacts and optional per-case deltas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCORE_FIELDS = (
    "native_success",
    "command_contract_success",
    "tool_invocation_success",
    "command_outcome_success",
    "execution_success",
    "response_quality_success",
)


def _is_infra_failure_error(error: dict[str, Any]) -> bool:
    if not isinstance(error, dict):
        return False
    status = error.get("status")
    text = " ".join(
        str(error.get(key) or "")
        for key in ("error", "message", "detail", "type")
    ).lower()
    if status in {502, 503, 504, 520, 521, 522, 523, 524}:
        return True
    return bool(
        "cannot reach" in text
        or "connection refused" in text
        or "connection reset" in text
        or "connect timeout" in text
        or "read timeout" in text
        or "unreachable" in text
        or "cooldown active" in text
        or "upstream protocol error" in text
        or ("upstream" in text and "failed" in text)
    )


def _record_has_infra_error(record: dict[str, Any]) -> bool:
    if record.get("infra_failure") is True:
        return True
    errors = list(record.get("stream_errors") or [])
    stream_exception = record.get("stream_exception")
    if isinstance(stream_exception, dict):
        errors.append(stream_exception)
    return any(_is_infra_failure_error(error) for error in errors)


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _records_by_case(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(record.get("case")): record
        for record in artifact.get("records", [])
        if record.get("case")
    }


def _metric(record: dict[str, Any], key: str) -> Any:
    metrics = record.get("metrics") or {}
    return metrics.get(key)


def _fmt_num(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def _print_summary(label: str, path: Path, artifact: dict[str, Any]) -> None:
    cases = artifact.get("cases")
    infra = artifact.get("infra_failures")
    evaluable = artifact.get("evaluable_cases")
    inferred_infra = sum(
        1 for record in artifact.get("records", []) if _record_has_infra_error(record)
    )
    print(f"{label}: {path}")
    print(f"  model: {artifact.get('model')}")
    print(f"  cases: {cases}")
    if infra is not None:
        print(f"  infra_failures: {infra}")
        print(f"  evaluable_cases: {evaluable}")
    elif inferred_infra:
        print(f"  inferred_infra_records: {inferred_infra}")
    for field in SCORE_FIELDS:
        value = artifact.get(field)
        if value is not None:
            print(f"  {field}: {value}/{cases}")
        ev_value = artifact.get(f"{field}_evaluable")
        if ev_value is not None:
            print(f"  {field}_evaluable: {ev_value}/{evaluable}")
    print(f"  duplicate_textual_calls: {artifact.get('duplicate_textual_calls')}")
    print(f"  repetitive_tool_calls: {artifact.get('repetitive_tool_calls')}")
    print(f"  stream_errors: {artifact.get('stream_errors')}")


def _print_delta(before: dict[str, Any], after: dict[str, Any]) -> None:
    before_records = _records_by_case(before)
    after_records = _records_by_case(after)
    shared = sorted(set(before_records) & set(after_records))
    if not shared:
        print("delta: no shared cases")
        return
    print("delta by shared case:")
    for case in shared:
        old = before_records[case]
        new = after_records[case]
        old_input = _metric(old, "input_tokens")
        new_input = _metric(new, "input_tokens")
        old_time = _metric(old, "response_time")
        new_time = _metric(new, "response_time")
        old_elapsed = old.get("elapsed_seconds")
        new_elapsed = new.get("elapsed_seconds")
        print(
            "  "
            + case
            + ": input "
            + f"{_fmt_num(old_input)} -> {_fmt_num(new_input)}; "
            + "response "
            + f"{_fmt_num(old_time, 's')} -> {_fmt_num(new_time, 's')}; "
            + "elapsed "
            + f"{_fmt_num(old_elapsed, 's')} -> {_fmt_num(new_elapsed, 's')}; "
            + "tool "
            + f"{old.get('tool_invocation_ok')} -> {new.get('tool_invocation_ok')}; "
            + "outcome "
            + f"{old.get('command_outcome_ok')} -> {new.get('command_outcome_ok')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--compare", type=Path, help="Compare artifact against this earlier baseline.")
    args = parser.parse_args()

    current = _load(args.artifact)
    _print_summary("artifact", args.artifact, current)
    if args.compare:
        baseline = _load(args.compare)
        print()
        _print_summary("baseline", args.compare, baseline)
        print()
        _print_delta(baseline, current)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
