#!/usr/bin/env python3
"""Rank next Odysseus tool-router improvement targets from eval artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _metric(record: dict[str, Any], key: str, default: Any = None) -> Any:
    metrics = record.get("metrics") or {}
    return metrics.get(key, default)


def _tool_rounds(record: dict[str, Any]) -> int:
    metrics = record.get("metrics") or {}
    usage = metrics.get("usage_buckets") or []
    round_models = metrics.get("round_models") or []
    if usage:
        return len(usage)
    if round_models:
        return len(round_models)
    snapshots = record.get("model_request_snapshots") or []
    if snapshots:
        return len(snapshots)
    return 0


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


def _record_status(record: dict[str, Any]) -> str:
    if _record_has_infra_error(record):
        return "infra"
    if not record.get("native_call_ok"):
        return "routing"
    if not record.get("command_contract_ok"):
        return "contract"
    if not record.get("tool_invocation_ok"):
        return "invocation"
    if not record.get("command_outcome_ok"):
        return "outcome"
    if not record.get("response_quality_ok"):
        return "response"
    if record.get("duplicate_textual_call"):
        return "duplicate_text"
    if record.get("repetitive_tool_call"):
        return "repeat"
    return "pass"


def _first_output(record: dict[str, Any]) -> dict[str, Any]:
    outputs = record.get("tool_outputs") or []
    return outputs[0] if outputs else {}


def _print_row(record: dict[str, Any]) -> None:
    case = record.get("case")
    status = _record_status(record)
    first_tool = record.get("first_tool")
    expected = record.get("expected_tool")
    output = _first_output(record)
    input_tokens = _metric(record, "input_tokens")
    response_time = _metric(record, "response_time")
    elapsed = record.get("elapsed_seconds")
    rounds = _tool_rounds(record)
    exit_code = output.get("exit_code")
    print(
        f"- {case}: status={status}, expected={expected}, first={first_tool}, "
        f"rounds={rounds}, input={input_tokens}, response={response_time}s, "
        f"elapsed={elapsed}s, exit={exit_code}"
    )


def _top(records: list[dict[str, Any]], key, limit: int) -> list[dict[str, Any]]:
    return sorted(records, key=key, reverse=True)[:limit]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()

    artifact = _load(args.artifact)
    records = list(artifact.get("records") or [])
    infra = [record for record in records if _record_has_infra_error(record)]
    evaluable = [record for record in records if not _record_has_infra_error(record)]
    failed = [record for record in evaluable if _record_status(record) != "pass"]
    slow = _top(
        [record for record in evaluable if _metric(record, "response_time") is not None],
        lambda record: float(_metric(record, "response_time", 0) or 0),
        args.limit,
    )
    token_heavy = _top(
        [record for record in evaluable if _metric(record, "input_tokens") is not None],
        lambda record: int(_metric(record, "input_tokens", 0) or 0),
        args.limit,
    )
    multi_round = _top(
        [record for record in evaluable if _tool_rounds(record) > 1],
        lambda record: (_tool_rounds(record), float(_metric(record, "response_time", 0) or 0)),
        args.limit,
    )

    print(f"artifact: {args.artifact}")
    print(f"model: {artifact.get('model')}")
    print(f"cases: {artifact.get('cases', len(records))}")
    print(f"infra: {len(infra)}")
    print(f"evaluable: {len(evaluable)}")
    print(f"failures: {len(failed)}")
    print()

    print("failures:")
    if failed:
        for record in failed:
            _print_row(record)
    else:
        print("- none")
    print()

    print(f"slowest_{len(slow)}:")
    for record in slow:
        _print_row(record)
    print()

    print(f"token_heaviest_{len(token_heavy)}:")
    for record in token_heavy:
        _print_row(record)
    print()

    print(f"multi_round_{len(multi_round)}:")
    if multi_round:
        for record in multi_round:
            _print_row(record)
    else:
        print("- none")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
