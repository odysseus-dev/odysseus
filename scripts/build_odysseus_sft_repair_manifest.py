#!/usr/bin/env python3
"""Build a reproducible model-only repair pool from conversation QA runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SFT_WEBUI_POLICY_DISABLED_TOOLS = frozenset({
    "python", "read_file", "write_file", "edit_file", "apply_patch",
})


def source_seed_id(row: dict[str, Any]) -> str:
    return str(row.get("source_seed_id") or row.get("id") or "").strip()


def behavior_category(value: str) -> str:
    text = str(value or "").casefold()
    rules = (
        ("response_constraint_adherence", (
            "limit", "constraint", "instruction_noncompliance", "instruction_following",
            "counting_error",
        )),
        ("required_tool_execution", (
            "missing_tool", "missing_required_tool", "missing_required_action",
            "false_refusal", "refusal",
        )),
        ("tool_action_selection", (
            "wrong_action", "wrong_tool", "incorrect_tool", "malformed_tool",
            "command_selection",
        )),
        ("required_argument_grounding", ("argument", "identifier", "filter")),
        ("tool_error_recovery", (
            "no_retry", "error_recovery", "false_empty", "empty_result",
            "unrecovered", "missing_fallback", "stale_id_loop",
        )),
        ("result_rendering", (
            "render", "empty_answer", "missing_requested_content", "missing_note_titles",
            "missing_progress_link", "non_answer", "uninformative_answer",
        )),
        ("followup_evidence_use", ("followup", "follow_up", "continuity", "unanswered", "incomplete")),
        ("evidence_grounding", (
            "hallucin", "wrong_answer", "unsupported", "grounding", "false_success",
            "unfaithful", "content_mismatch",
        )),
    )
    for category, needles in rules:
        if any(needle in text for needle in needles):
            return category
    return "other_model_behavior"


def has_transport_failure(row: dict[str, Any]) -> bool:
    needles = (
        "connection refused", "connecterror", "remoteprotocolerror",
        "replay_transport_unavailable", "session_start_failed", "readtimeout",
    )
    return any(needle in json.dumps(row, ensure_ascii=False).casefold() for needle in needles)


def eligible_failed_turns(row: dict[str, Any]) -> tuple[list[int], list[int]]:
    observed = row.get("observed") or []
    failed = [value for value in (row.get("judge") or {}).get("failed_turns") or []
              if isinstance(value, int) and 1 <= value <= len(observed)]
    if not failed:
        failed = list(range(1, len(observed) + 1))
    eligible, absent_surface = [], []
    for number in failed:
        turn = observed[number - 1]
        contract = turn.get("contract") or {}
        if not (contract.get("offered") or []) and not (turn.get("tool_calls") or []):
            absent_surface.append(number)
        else:
            eligible.append(number)
    return eligible, absent_surface


def requires_native_workspace_tool(row: dict[str, Any]) -> bool:
    expected = "\n".join(
        str(turn.get("expect") or "")
        for turn in (row.get("turns") or [])
        if isinstance(turn, dict)
    )
    return any(
        re.search(rf"(?<!\w){re.escape(tool)}(?!\w)", expected, re.I)
        for tool in SFT_WEBUI_POLICY_DISABLED_TOOLS
    ) or bool(re.search(
        r"\b(?:run|use|execute)\s+(?:a\s+)?(?:local\s+)?(?:shell|bash)\b|"
        r"\b(?:shell|bash)\s+(?:version\s+)?check\b",
        expected,
        re.I,
    ))


def build_manifest(paths: list[Path], excluded_seeds: set[str],
                   routing_experiment: str | None = None,
                   resolved_seeds: set[str] | None = None) -> dict[str, Any]:
    """Retain each seed's latest confirmed model-owned failure.

    A later stochastic pass does not prove a repair and must not silently erase
    a useful failure example. Operators can explicitly resolve or exclude a
    seed after a verified fix or after discovering a defective expectation.
    """
    resolved_seeds = resolved_seeds or set()
    latest_failure: dict[str, tuple[int, dict[str, Any], Path]] = {}
    inputs = []
    ignored_nonbehavioral_rows = 0
    ignored_runtime_inputs = 0
    for order, path in enumerate(paths):
        raw = path.read_bytes()
        payload = json.loads(raw)
        runtime = payload.get("routing_experiment", "baseline")
        inputs.append({
            "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "routing_experiment": runtime,
        })
        if routing_experiment is not None and runtime != routing_experiment:
            ignored_runtime_inputs += 1
            continue
        for row in payload.get("results") or []:
            seed = source_seed_id(row)
            judge = row.get("judge") or {}
            # An unavailable judge or broken replay does not supersede older
            # valid behavioral evidence for the same seed.
            if not seed or judge.get("verdict") not in {"pass", "fail"} or has_transport_failure(row):
                ignored_nonbehavioral_rows += 1
                continue
            if judge.get("verdict") == "fail" and judge.get("owner") == "model_sft":
                latest_failure[seed] = (order, row, path)

    candidates, exclusions = [], []
    for seed, (_, row, path) in sorted(latest_failure.items()):
        judge = row.get("judge") or {}
        reason = None
        if seed in excluded_seeds:
            reason = "explicit_ambiguous_or_defective_seed"
        elif seed in resolved_seeds:
            reason = "explicitly_resolved_after_verified_fix"
        elif requires_native_workspace_tool(row):
            reason = "requires_native_workspace_tool_on_webui_surface"
        elif has_transport_failure(row):
            reason = "transport_contaminated"
        eligible, absent_surface = eligible_failed_turns(row)
        if reason is None and not eligible:
            reason = "no_failed_turn_with_executable_tool_surface"
        if reason:
            exclusions.append({"source_seed_id": seed, "reason": reason})
            continue
        candidates.append({
            "source_seed_id": seed,
            "family": row.get("family"),
            "purpose": row.get("purpose"),
            "behavior_category": behavior_category(judge.get("failure_category", "")),
            "eligible_failed_turns": eligible,
            "excluded_absent_surface_turns": absent_surface,
            "judge": judge,
            "turns": row.get("turns") or [],
            "observed": row.get("observed") or [],
            "session_id": row.get("session_id"),
            "url": row.get("url"),
            "latest_run": str(path),
        })
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "precedence": "latest confirmed model_sft failure wins per source_seed_id; later stochastic passes do not erase it",
            "include": "latest model_sft fail verdict with executable tool surface",
            "exclude": [
                "pass/uncertain", "non-model owners", "transport contamination",
                "failed turns with absent tool surface", "explicit ambiguous/defective seeds",
                "native-workspace-only expectations on the WebUI surface", "explicitly resolved seeds",
            ],
        },
        "routing_experiment": routing_experiment,
        "inputs": inputs,
        "ignored_runtime_inputs": ignored_runtime_inputs,
        "ignored_nonbehavioral_rows": ignored_nonbehavioral_rows,
        "candidate_count": len(candidates),
        "counts_by_family": dict(sorted(Counter(row["family"] for row in candidates).items())),
        "counts_by_behavior": dict(sorted(Counter(row["behavior_category"] for row in candidates).items())),
        "candidates": candidates,
        "exclusion_count": len(exclusions),
        "exclusions": exclusions,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True,
                        help="QA run in chronological order; repeat for later replays")
    parser.add_argument("--exclude-seed", action="append", default=[],
                        help="Explicitly exclude an ambiguous or defective generated seed")
    parser.add_argument("--resolved-seed", action="append", default=[],
                        help="Drop a model failure only after a verified repair replay")
    parser.add_argument(
        "--routing-experiment", default="recent_model_choice",
        help="Include only runs from this exact routing runtime",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_manifest(
        args.run, set(args.exclude_seed), args.routing_experiment,
        set(args.resolved_seed),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "candidates": manifest["candidate_count"],
        "by_family": manifest["counts_by_family"],
        "by_behavior": manifest["counts_by_behavior"],
        "excluded": manifest["exclusion_count"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
