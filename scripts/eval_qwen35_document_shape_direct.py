#!/usr/bin/env python3
"""Direct document-tool argument-shape gate for compact Qwen tool routers.

This intentionally does not execute Odysseus tools.  It calls the served
OpenAI-compatible model directly with the same compact system prompt used by
the real route, then scores the first native tool call shape.

Use this before another train: if this gate does not move, the full Odysseus
CRUD harness will not move either.
"""

from __future__ import annotations

import argparse
import ast
import json
import time
from pathlib import Path
from typing import Any

import httpx


DEFAULT_SYSTEM_SOURCE = Path(
    str(Path(__file__).resolve().parents[1] / "data" / "train_splits" / "qwen35_9b_tool_router_v35_preference_memory_nudge_no_schema_20260820" / "train.jsonl")
)
REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_LOOP_SOURCE = REPO_ROOT / "src/agent_loop.py"


def runtime_system_prompt() -> str:
    try:
        tree = ast.parse(AGENT_LOOP_SOURCE.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "_QWEN38_TOOL_ROUTER_PROMPT" for target in node.targets):
                continue
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and value.strip():
                return value
    except Exception:
        pass
    return load_system_prompt(DEFAULT_SYSTEM_SOURCE)


CASES: list[dict[str, Any]] = [
    {
        "case": "document_create_short",
        "message": "Create an editor document titled ODY-DIRECT release checklist with exactly this content: temporary fixture.",
        "expected_tool": "create_document",
        "kind": "create",
        "title": "ODY-DIRECT release checklist",
        "content": "temporary fixture",
    },
    {
        "case": "document_edit_explicit_tool",
        "message": "Edit the active document ODY-DIRECT release checklist: replace 'temporary fixture' with 'updated fixture'. Use the document edit tool.",
        "expected_tool": "edit_document",
        "kind": "edit",
        "find": "temporary fixture",
        "replace": "updated fixture",
    },
    {
        "case": "document_edit_open_editor",
        "message": "In the open editor document, change draft itinerary to confirmed itinerary.",
        "expected_tool": "edit_document",
        "kind": "edit",
        "find": "draft itinerary",
        "replace": "confirmed itinerary",
    },
    {
        "case": "document_edit_exact_replace",
        "message": "Use edit_document to replace 'old repro steps' with 'new repro steps' in the active editor document.",
        "expected_tool": "edit_document",
        "kind": "edit",
        "find": "old repro steps",
        "replace": "new repro steps",
    },
    {
        "case": "document_read_titled_first_call",
        "message": "Find the document titled ODY-DIRECT travel memo, read it, and summarize it.",
        "expected_tool": "manage_documents",
        "kind": "list_first",
        "title": "ODY-DIRECT travel memo",
    },
    {
        "case": "document_delete_titled_first_call",
        "message": "Delete only the editor document titled ODY-DIRECT invoice summary. Find its document id if needed, then delete it.",
        "expected_tool": "manage_documents",
        "kind": "list_first",
        "title": "ODY-DIRECT invoice summary",
    },
    {
        "case": "document_verify_absent",
        "message": "Verify that editor document ODY-DIRECT school note no longer exists by searching documents. Do not create anything.",
        "expected_tool": "manage_documents",
        "kind": "list_first",
        "title": "ODY-DIRECT school note",
    },
    {
        "case": "document_list_plain",
        "message": "List my documents.",
        "expected_tool": "manage_documents",
        "kind": "list_plain",
    },
]


def load_system_prompt(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for msg in row.get("messages") or []:
            if msg.get("role") == "system" and msg.get("content"):
                return str(msg["content"])
    raise RuntimeError(f"No system prompt found in {path}")


def parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"__raw": raw}
    return parsed if isinstance(parsed, dict) else {"__raw": raw}


def first_call(response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    choices = response.get("choices") or []
    if not choices:
        return "", {}
    message = (choices[0].get("message") or {}) if isinstance(choices[0], dict) else {}
    calls = message.get("tool_calls") or []
    if not calls:
        return "", {}
    fn = calls[0].get("function") or {}
    return str(fn.get("name") or ""), parse_args(fn.get("arguments"))


def contains(value: Any, needle: str) -> bool:
    return needle.lower() in json.dumps(value, ensure_ascii=False).lower()


def score_case(case: dict[str, Any], tool: str, args: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    normalized_failures: list[str] = []
    if tool != case["expected_tool"]:
        failures.append(f"expected tool {case['expected_tool']}, got {tool or '<none>'}")
        normalized_failures.append(f"expected tool {case['expected_tool']}, got {tool or '<none>'}")

    kind = case["kind"]
    if kind == "create":
        if str(args.get("title") or "") != case["title"]:
            failures.append("create title mismatch")
        if str(args.get("content") or "") != case["content"]:
            failures.append("create content mismatch")
        normalized_failures.extend(failures)
    elif kind == "edit":
        command = str(args.get("command") or "")
        edits = args.get("edits")
        alias_find = args.get("find") or args.get("old_string") or args.get("oldString") or args.get("pattern")
        alias_replace = args.get("replace") or args.get("new_string") or args.get("newString") or args.get("replacement")
        valid_command = (
            "<<<FIND>>>" in command
            and "<<<REPLACE>>>" in command
            and "<<<END>>>" in command
            and case["find"] in command
            and case["replace"] in command
        )
        valid_edits = False
        if isinstance(edits, list):
            valid_edits = any(
                isinstance(edit, dict)
                and edit.get("find") == case["find"]
                and edit.get("replace") == case["replace"]
                for edit in edits
            )
        if not valid_command and not valid_edits:
            failures.append("edit args must use command FIND/REPLACE/END or edits[{find,replace}]")
        if "pattern" in args or "replacement" in args:
            failures.append("pattern/replacement is not accepted by runtime edit_document")
        if not (valid_command or valid_edits or (alias_find == case["find"] and alias_replace == case["replace"])):
            normalized_failures.append("edit args cannot normalize to FIND/REPLACE")
    elif kind == "list_first":
        action = args.get("action")
        query_value = args.get("search") or args.get("title") or args.get("query") or args.get("text") or ""
        if action != "list":
            failures.append(f"expected first action list, got {args.get('action')!r}")
        if not contains(query_value, case["title"]):
            failures.append("list-first search/title missing target title")
        if action == "search":
            failures.append("manage_documents has no search action; use list with search")
        if action not in {"list", "search", "find"}:
            normalized_failures.append(f"expected normalizable first action list/search/find, got {action!r}")
        if not contains(query_value, case["title"]):
            normalized_failures.append("normalizable list search/title missing target title")
    elif kind == "list_plain":
        if args.get("action") != "list":
            failures.append(f"expected action list, got {args.get('action')!r}")
            normalized_failures.append(f"expected action list, got {args.get('action')!r}")
    else:
        failures.append(f"unknown kind {kind}")
        normalized_failures.append(f"unknown kind {kind}")

    return {
        "ok": not failures,
        "normalized_ok": not normalized_failures,
        "tool_ok": tool == case["expected_tool"],
        "failures": failures,
        "normalized_failures": normalized_failures,
    }


def run_case(client: httpx.Client, base_url: str, model: str, system: str, case: dict[str, Any], timeout: float) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": case["message"]},
        ],
        "temperature": 0,
        "top_p": 1,
        "max_tokens": 256,
        "stream": False,
    }
    started = time.time()
    response = client.post(base_url.rstrip("/") + "/chat/completions", json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    tool, args = first_call(data)
    score = score_case(case, tool, args)
    return {
        "case": case["case"],
        "message": case["message"],
        "expected_tool": case["expected_tool"],
        "kind": case["kind"],
        "tool": tool,
        "args": args,
        **score,
        "usage": data.get("usage"),
        "elapsed_seconds": round(time.time() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18051/v1")
    parser.add_argument("--model", default="qwen35-9b-tool-router-v35-preference-nudge")
    parser.add_argument(
        "--system-source",
        type=Path,
        default=None,
        help="Optional JSONL source for a system prompt. Defaults to src.agent_loop runtime compact prompt.",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    system = load_system_prompt(args.system_source) if args.system_source else runtime_system_prompt()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    with httpx.Client() as client:
        for case in CASES:
            try:
                record = run_case(client, args.base_url, args.model, system, case, args.timeout)
            except Exception as exc:
                record = {
                    "case": case["case"],
                    "message": case["message"],
                    "expected_tool": case["expected_tool"],
                    "kind": case["kind"],
                    "ok": False,
                    "tool_ok": False,
                    "failures": [repr(exc)],
                    "infra_error": True,
                }
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)

    summary = {
        "model": args.model,
        "base_url": args.base_url,
        "system_source": str(args.system_source) if args.system_source else "src.agent_loop._QWEN38_TOOL_ROUTER_PROMPT",
        "cases": len(records),
        "ok": sum(1 for record in records if record.get("ok")),
        "normalized_ok": sum(1 for record in records if record.get("normalized_ok")),
        "tool_ok": sum(1 for record in records if record.get("tool_ok")),
        "infra_errors": sum(1 for record in records if record.get("infra_error")),
        "records": records,
    }
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("SUMMARY", json.dumps({k: v for k, v in summary.items() if k != "records"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
