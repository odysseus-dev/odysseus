#!/usr/bin/env python3
"""Focused real-route eval for implicit Odysseus memory questions.

This uses the same Odysseus HTTP chat route as the broader harness evals, then
adds memory-specific scoring so the next SFT slice can target the actual gap:
natural "what do you remember/know about me" questions that should route to
manage_memory before answering.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

try:
    from scripts.eval_odysseus_tool_use import _cookie, run_case
except ModuleNotFoundError:
    from eval_odysseus_tool_use import _cookie, run_case


CASES: list[dict[str, Any]] = [
    {
        "case": "memory_what_remember_nationality",
        "message": "what do you remember about my nationality?",
        "expected_tool": "manage_memory",
        "expected_action": "search",
        "query_terms": ("nationality",),
    },
    {
        "case": "memory_what_know_about_me",
        "message": "what do you know about me?",
        "expected_tool": "manage_memory",
        "expected_action": "list",
    },
    {
        "case": "memory_saved_swedish",
        "message": "do you have anything saved about me being Swedish?",
        "expected_tool": "manage_memory",
        "expected_action": "search",
        "query_terms": ("swedish",),
    },
    {
        "case": "memory_family",
        "message": "what have I told you about my family?",
        "expected_tool": "manage_memory",
        "expected_action": "search",
        "query_terms": ("family",),
    },
    {
        "case": "memory_preferences",
        "message": "what do you remember about my preferences?",
        "expected_tool": "manage_memory",
        "expected_action": "search",
        "query_terms": ("preference", "preferences"),
    },
    {
        "case": "memory_location",
        "message": "what do you know about where I live?",
        "expected_tool": "manage_memory",
        "expected_action": "search",
        "query_terms": ("live", "location", "reside", "residence"),
    },
    {
        "case": "memory_explicit_control",
        "message": "search my saved memories for nationality",
        "expected_tool": "manage_memory",
        "expected_action": "search",
        "query_terms": ("nationality",),
    },
    {
        "case": "chat_hi_control",
        "message": "hi",
        "expected_tool": "no_tool",
        "expected_action": "",
    },
    {
        "case": "chat_identity_control",
        "message": "who are you?",
        "expected_tool": "no_tool",
        "expected_action": "",
    },
]


BAD_SURFACE_PATTERNS = (
    r"\bdon['\u2019]?\s+have\b",
    r"\bi don['\u2019]?\b",
    r"\bdon['\u2019]?\s+retain\b",
    r"\bdon['\u2019]?\s+remember\b",
    r"\bdon'\b",
    r"\babou\b",
    r"\blis\b",
    r"\bfirs\b",
    r"\btha\b",
    r"\bwh\b",
)


def _parse_command(raw: Any) -> tuple[str, str]:
    """Return action/query-ish text from a tool command payload."""
    if isinstance(raw, dict):
        action = str(raw.get("action") or "").strip()
        query = str(raw.get("query") or raw.get("text") or raw.get("command") or "").strip()
        return action, query
    text = str(raw or "").strip()
    if not text:
        return "", ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return _parse_command(parsed)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "", ""
    action = lines[0]
    query_lines = [
        line
        for line in lines[1:]
        if not line.startswith("<parameter=") and not line.startswith("</parameter")
    ]
    query = " ".join(query_lines).strip()
    return action, query


def _bad_surface(response: str) -> bool:
    value = response or ""
    return any(re.search(pattern, value, re.IGNORECASE) for pattern in BAD_SURFACE_PATTERNS)


def annotate(record: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    metrics = record.get("metrics") or {}
    tool_events = metrics.get("tool_events") or []
    memory_events = [event for event in tool_events if event.get("tool") == "manage_memory"]
    first_memory_action = ""
    first_memory_query = ""
    if memory_events:
        first_memory_action, first_memory_query = _parse_command(memory_events[0].get("command"))
    expected_tool = case["expected_tool"]
    expected_action = case.get("expected_action") or ""
    response = str(record.get("response") or "")
    no_tool = expected_tool == "no_tool"
    action_ok = no_tool or first_memory_action == expected_action
    query_terms = tuple(str(term).lower() for term in case.get("query_terms") or ())
    query_lower = first_memory_query.lower()
    query_ok = no_tool or not query_terms or any(term in query_lower for term in query_terms)
    tool_ok = (
        (record.get("tool_count") == 0 and no_tool)
        or (record.get("first_tool") == expected_tool)
    )
    no_premature_denial = no_tool or not (
        record.get("tool_count") == 0
        and re.search(r"\b(i\s+)?do\s+not\b|\bi don['\u2019]?t\b|\bno saved memor", response, re.I)
    )
    surface_ok = bool(response) and not _bad_surface(response)
    success = bool(
        tool_ok
        and action_ok
        and query_ok
        and no_premature_denial
        and surface_ok
        and not record.get("infra_failure")
        and not record.get("stream_errors")
    )
    record.update(
        {
            "expected_action": expected_action,
            "first_memory_action": first_memory_action,
            "first_memory_query": first_memory_query,
            "memory_tool_ok": bool(tool_ok),
            "memory_action_ok": bool(action_ok),
            "memory_query_ok": bool(query_ok),
            "no_premature_memory_denial": bool(no_premature_denial),
            "memory_surface_ok": bool(surface_ok),
            "focused_success": success,
            "input_tokens": metrics.get("input_tokens"),
            "output_tokens": metrics.get("output_tokens"),
            "tokens_per_second": metrics.get("tokens_per_second"),
        }
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:7011")
    parser.add_argument("--endpoint", default="http://127.0.0.1:18051/v1")
    parser.add_argument("--endpoint-id", default="8b80db2d")
    parser.add_argument("--selected-endpoint-url", default="http://host.docker.internal:18051/v1")
    parser.add_argument("--model", default="qwen35-9b-tool-router-v31-recovery-from-base")
    parser.add_argument("--selected-model", default="qwen35-9b-tool-router-v31-recovery-from-base")
    parser.add_argument("--cookie-file", default="data/sessions.json")
    parser.add_argument("--prompt-mode", default="agent")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--hard-turn-timeout", type=float, default=60.0)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cases", default="")
    parser.set_defaults(auto_approve=True, client_runtime_context=None)
    args = parser.parse_args()

    selected = {item.strip() for item in args.cases.split(",") if item.strip()}
    cases = [case for case in CASES if not selected or case["case"] in selected]
    unknown = selected - {case["case"] for case in CASES}
    if unknown:
        raise SystemExit(f"Unknown case(s): {', '.join(sorted(unknown))}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    with httpx.Client(
        cookies={"odysseus_session": _cookie(Path(args.cookie_file))},
        follow_redirects=False,
        timeout=args.timeout + 20,
    ) as client:
        for case in cases:
            record = run_case(
                client,
                args,
                case["case"],
                case["message"],
                case["expected_tool"],
            )
            record = annotate(record, case)
            records.append(record)
            print(
                json.dumps(
                    {
                        key: record.get(key)
                        for key in (
                            "case",
                            "message",
                            "expected_tool",
                            "expected_action",
                            "first_tool",
                            "first_memory_action",
                            "first_memory_query",
                            "memory_tool_ok",
                            "memory_action_ok",
                            "memory_query_ok",
                            "no_premature_memory_denial",
                            "memory_surface_ok",
                            "focused_success",
                            "input_tokens",
                            "output_tokens",
                            "elapsed_seconds",
                            "response",
                        )
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
    summary = {
        "model": args.selected_model or args.model,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cases": len(records),
        "focused_success": sum(bool(r.get("focused_success")) for r in records),
        "memory_tool_success": sum(bool(r.get("memory_tool_ok")) for r in records),
        "memory_action_success": sum(bool(r.get("memory_action_ok")) for r in records),
        "memory_query_success": sum(bool(r.get("memory_query_ok")) for r in records),
        "surface_success": sum(bool(r.get("memory_surface_ok")) for r in records),
        "infra_failures": sum(bool(r.get("infra_failure")) for r in records),
        "stream_errors": sum(bool(r.get("stream_errors")) for r in records),
        "records": records,
    }
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n")
    print("SUMMARY", json.dumps({k: v for k, v in summary.items() if k != "records"}))
    return 0 if summary["focused_success"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
