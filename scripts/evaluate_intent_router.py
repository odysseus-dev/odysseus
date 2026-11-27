#!/usr/bin/env python3
"""Evaluate the CPU-local intent router without printing corpus prompts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.intent_router import IntentRouter  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "intent_router_cases.json",
        help="JSON evaluation corpus",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--min-intent-recall",
        type=float,
        default=0.0,
        help="Exit non-zero when micro intent recall falls below this value",
    )
    parser.add_argument(
        "--min-intent-precision",
        type=float,
        default=0.0,
        help="Exit non-zero when micro intent precision falls below this value",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    router = IntentRouter()

    # Load the model and prototype matrix before measuring warm request latency.
    router.classify("intent router warmup", top_k=args.top_k)

    expected_intents = 0
    predicted_intents = 0
    matched_intents = 0
    expected_constraints = 0
    predicted_constraint_count = 0
    matched_constraints = 0
    cases_with_hit = 0
    exact_matches = 0
    failed_cases: list[int] = []
    elapsed_ms: list[float] = []

    for index, case in enumerate(cases):
        route = router.classify(case["text"], top_k=args.top_k)
        elapsed_ms.append(route.elapsed_ms)

        expected = set(case["expected_intents"])
        constraints = set(case.get("constraints", []))
        expected_intents += len(expected)
        expected_constraints += len(constraints)
        if route.source != "semantic":
            failed_cases.append(index)
            continue

        predicted = {item.name for item in route.top_intents}
        predicted_intents += len(predicted)
        matched_intents += len(expected & predicted)
        cases_with_hit += bool(expected & predicted) if expected else not predicted
        exact_matches += expected == predicted

        predicted_constraint_set = set(route.constraints)
        predicted_constraint_count += len(predicted_constraint_set)
        matched_constraints += len(constraints & predicted_constraint_set)

    intent_recall = matched_intents / expected_intents if expected_intents else 1.0
    intent_precision = (
        matched_intents / predicted_intents if predicted_intents else 1.0
    )
    constraint_recall = (
        matched_constraints / expected_constraints if expected_constraints else 1.0
    )
    constraint_precision = (
        matched_constraints / predicted_constraint_count
        if predicted_constraint_count
        else 1.0
    )
    report = {
        "cases": len(cases),
        "top_k": args.top_k,
        "intent_recall_at_k": round(intent_recall, 4),
        "intent_precision_at_k": round(intent_precision, 4),
        "case_hit_rate": round(cases_with_hit / len(cases), 4) if cases else 1.0,
        "exact_match_rate": round(exact_matches / len(cases), 4) if cases else 1.0,
        "constraint_recall": round(constraint_recall, 4),
        "constraint_precision": round(constraint_precision, 4),
        "warm_latency_ms": {
            "p50": round(float(np.percentile(elapsed_ms, 50)), 2) if elapsed_ms else 0.0,
            "p95": round(float(np.percentile(elapsed_ms, 95)), 2) if elapsed_ms else 0.0,
            "max": round(max(elapsed_ms), 2) if elapsed_ms else 0.0,
        },
        "classification_failure_count": len(failed_cases),
        "classification_failure_indexes": failed_cases,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(
        bool(failed_cases)
        or intent_recall < args.min_intent_recall
        or intent_precision < args.min_intent_precision
    )


if __name__ == "__main__":
    raise SystemExit(main())
