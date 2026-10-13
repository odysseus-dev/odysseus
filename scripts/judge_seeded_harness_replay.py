#!/usr/bin/env python3
"""Independently classify seeded live-replay failures with a full tool catalog."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import uuid
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_sft_environment_expansion import compact_tool_catalog  # noqa: E402
from scripts.repair_sft_corpus_with_kimi import endpoint, parse_json  # noqa: E402


def compact_evidence(result: dict[str, Any]) -> dict[str, Any]:
    turns = []
    for turn in result.get("turns") or []:
        contract = next(
            (event for event in turn.get("evidence") or [] if event.get("type") == "turn_contract"),
            {},
        )
        outputs = [
            str(event.get("output") or "")[:1200]
            for event in turn.get("evidence") or []
            if event.get("type") == "tool_output"
        ]
        errors = [
            event for event in turn.get("evidence") or []
            if event.get("type") in {"error", "parse_error"}
        ]
        turns.append({
            "id": turn.get("id"),
            "prompt": turn.get("prompt"),
            "expected_tools": turn.get("expected_tools"),
            "observed_tools": turn.get("observed_tools"),
            "answer": str(turn.get("answer") or "")[:1800],
            "deterministic_failures": turn.get("failures"),
            "upstream_failed": bool(turn.get("upstream_failed", False)),
            "contract": {
                "capabilities": contract.get("capabilities") or [],
                "required": contract.get("required") or [],
                "offered": contract.get("offered") or [],
                "unavailable": contract.get("unavailable") or [],
                "selection_mode": contract.get("selection_mode"),
                "schema_mode": contract.get("schema_mode"),
            },
            "tool_outputs": outputs,
            "stream_errors": errors,
        })
    return {
        "case_id": result.get("case_id"),
        "seed_family_id": result.get("seed_family_id"),
        "owner": result.get("owner"),
        "deterministic_pass": result.get("pass"),
        "deterministic_failures": result.get("failures"),
        "turns": turns,
    }


def judge_once(ep: dict[str, str], case: dict[str, Any], result: dict[str, Any], timeout: float) -> dict[str, Any]:
    system = """You audit a real tool-agent replay. Return strict JSON only:
{"case_id":"...","case_valid":true,"overall_class":"pass|bad_generated_case|harness_routing|harness_execution|model_sft|tool_backend|mixed","confidence":0.0,"summary":"...","turns":[{"id":"...","valid_expectation":true,"best_tools":["..."],"classification":"pass|bad_generated_case|harness_routing|harness_execution|model_sft|tool_backend","reason":"...","generic_repair":"..."}]}

Use the COMPLETE tool catalog, the generated conversation, and the observed immutable turn contract.
- First decide whether the prompt and supplied environment actually support the expected tool. Reject ambiguous or invented expectations.
- harness_routing: the correct family/tool was absent, the wrong family was required, or the contract offered zero/wrong tools.
- harness_execution: the contract selected the correct deterministic operation but failed to execute/render it independently of model choice.
- model_sft: the correct tools were offered and executable, but the model chose the wrong tool/action, malformed arguments, leaked reasoning, or falsely answered.
- tool_backend: a correct call failed in the underlying service.
- Do not propose phrase-specific rules. Generic repairs must describe a semantic boundary or contract invariant.
- A prior turn's successful result can establish references for a follow-up. An active document fixture means deictic editing prompts may validly target document tools.
- Judge the complete 3-4 turn trajectory. If an earlier failed operation removed the object or evidence needed later, mark later failures as causal fallout in the reason instead of inventing another root cause.
- Recommend a harness patch only for a semantic category that should generalize across varied wording and entities. Never recommend a literal prompt/entity/domain-name rule. A single case can justify only a clear contract, authorization, or security invariant; otherwise request more variants.
- Do not reveal or reconstruct hidden benchmark answers. Judge only the supplied synthetic replay.
"""
    payload = {
        "model": ep["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({
                "tool_catalog": compact_tool_catalog(),
                "generated_case": case,
                "live_result": compact_evidence(result),
            }, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": 5000,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        ep["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {ep['api_key']}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode())
    message = body["choices"][0]["message"]
    verdict = parse_json(str(message.get("content") or message.get("reasoning_content") or ""))
    if str(verdict.get("case_id") or "") != str(result.get("case_id") or ""):
        raise ValueError("judge returned the wrong case_id")
    return verdict


def judge(
    ep: dict[str, str],
    case: dict[str, Any],
    result: dict[str, Any],
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    """Retry provider/JSON failures without changing the case being judged."""
    last_error: Exception | None = None
    for _attempt in range(max(0, retries) + 1):
        try:
            return judge_once(ep, case, result, timeout)
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--endpoint-id", default="e17d4b33")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--case-id", action="append", help="Judge only the named case; repeatable")
    args = parser.parse_args()

    cases = {row["case_id"]: row for row in json.loads(args.cases.read_text(encoding="utf-8"))["cases"]}
    results = json.loads(args.results.read_text(encoding="utf-8"))["results"]
    if args.case_id:
        wanted = set(args.case_id)
        results = [row for row in results if row["case_id"] in wanted]
    ep = endpoint(args.endpoint_id, args.model)
    verdicts: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                judge,
                ep,
                cases[result["case_id"]],
                result,
                args.timeout,
                args.retries,
            ): result
            for result in results
        }
        for future in concurrent.futures.as_completed(futures):
            result = futures[future]
            case_id = str(result["case_id"])
            try:
                verdicts[case_id] = future.result()
                print(f"judged {case_id}: {verdicts[case_id].get('overall_class')}", flush=True)
            except Exception as exc:
                errors.append({"case_id": case_id, "error": repr(exc)})
                print(f"failed {case_id}: {exc!r}", flush=True)
            atomic_write(args.out, {"verdicts": list(verdicts.values()), "errors": errors})
    ordered = [verdicts[row["case_id"]] for row in results if row["case_id"] in verdicts]
    atomic_write(args.out, {"verdicts": ordered, "errors": errors})
    counts: dict[str, int] = {}
    for row in ordered:
        key = str(row.get("overall_class") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    print(json.dumps({"judged": len(ordered), "errors": len(errors), "classes": counts}, indent=2))


if __name__ == "__main__":
    main()
