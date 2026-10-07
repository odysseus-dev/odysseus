#!/usr/bin/env python3
"""Evaluate Qwen tool-routing rows through Odysseus streaming + parser code.

This is intentionally below the full chat HTTP route: it does not execute tools
or mutate user data. It uses the same Odysseus LLM request path and production
text parser that the agent loop uses after a local model streams text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm_core import stream_llm
from src.tool_parsing import parse_tool_blocks
from src.tool_schemas import function_call_to_tool_block


def _sse_payloads(chunk: str) -> list[dict[str, Any]]:
    payloads = []
    for line in str(chunk or "").splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            continue
        try:
            payloads.append(json.loads(data))
        except json.JSONDecodeError:
            payloads.append({"type": "raw", "data": data})
    return payloads


def _expected_block(row: dict[str, Any]):
    call = row["messages"][-1]["tool_calls"][0]["function"]
    return function_call_to_tool_block(call["name"], json.dumps(call.get("arguments") or {}))


def _expected_name(row: dict[str, Any]) -> str:
    return row["messages"][-1]["tool_calls"][0]["function"]["name"]


def _same_tool_content(actual: str | None, expected: str | None) -> bool:
    if actual == expected:
        return True
    if actual is None or expected is None:
        return False
    try:
        actual_value = json.loads(actual)
        expected_value = json.loads(expected)
    except (TypeError, json.JSONDecodeError):
        return False
    return actual_value == expected_value


def _row_messages(row: dict[str, Any], mode: str) -> list[dict[str, str]]:
    messages = row["messages"]
    user = messages[1]["content"]
    if mode == "row_system":
        return [
            {"role": "system", "content": messages[0]["content"]},
            {"role": "user", "content": user},
        ]
    if mode == "zero":
        return [{"role": "user", "content": user}]
    if mode == "tiny":
        return [
            {
                "role": "system",
                "content": (
                    "Use Odysseus native tool-call tags for explicit tool requests. "
                    "Make exactly one call, then stop."
                ),
            },
            {"role": "user", "content": user},
        ]
    if mode == "compact_map":
        return [
            {
                "role": "system",
                "content": (
                    "You are Odysseus. For explicit requests, emit exactly one "
                    "native tool call, then stop. Use this map: "
                    "manage_notes=notes/checklists; "
                    "manage_documents=document library; "
                    "manage_calendar=calendar events; "
                    "manage_tasks=scheduled/recurring tasks; "
                    "manage_memory=saved memories; "
                    "search_chats=past chats; "
                    "read_file=explicit workspace paths; "
                    "mcp__email__list_emails=inbox/latest email; "
                    "mcp__email__search_emails=email subject/sender/topic search; "
                    "mcp__email__read_email=known email id."
                ),
            },
            {"role": "user", "content": user},
        ]
    if mode == "compact_map_v2":
        return [
            {
                "role": "system",
                "content": (
                    "You are Odysseus. For explicit requests, emit exactly one "
                    "native tool call, then stop. Use: manage_notes for notes "
                    "and checklists; manage_documents for the document library; "
                    "manage_calendar for calendar events; manage_tasks for "
                    "scheduled or recurring tasks; manage_memory for saved "
                    "memories; search_chats for past chats; read_file for "
                    "explicit workspace paths. Email: use mcp__email__list_emails "
                    "with folder INBOX and max_results 20 when asked to find/read "
                    "an email by subject; use mcp__email__search_emails with "
                    "max_results 10 for mail search by sender/topic; use "
                    "mcp__email__read_email only with a known email id."
                ),
            },
            {"role": "user", "content": user},
        ]
    if mode == "compact_map_v3":
        return [
            {
                "role": "system",
                "content": (
                    "Odysseus tools. Emit one native tool call, then stop. "
                    "manage_notes: notes/checklists. manage_documents: document "
                    "library. manage_calendar: calendar events. manage_tasks: "
                    "scheduled/recurring tasks. manage_memory: saved memories. "
                    "search_chats: past chats. read_file: workspace path. Email: "
                    "subject find+read -> mcp__email__list_emails {folder:INBOX,max_results:20}; "
                    "sender/topic search -> mcp__email__search_emails {max_results:10}; "
                    "known id -> mcp__email__read_email."
                ),
            },
            {"role": "user", "content": user},
        ]
    raise ValueError(f"Unknown mode: {mode}")


def _select_rows(path: Path, per_group: int) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open() as f:
        for line in f:
            row = json.loads(line)
            groups[_expected_name(row)].append(row)
    selected = []
    for name in sorted(groups):
        selected.extend(groups[name][:per_group])
    return selected


async def _run_one(args, row: dict[str, Any]) -> dict[str, Any]:
    expected = _expected_block(row)
    messages = _row_messages(row, args.mode)
    started = time.monotonic()
    text_parts: list[str] = []
    stream_events: list[dict[str, Any]] = []
    error = None
    try:
        async for chunk in stream_llm(
            args.base_url,
            args.model,
            messages,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            tools=None,
            session_id="tool-groups-" + uuid.uuid4().hex,
        ):
            for payload in _sse_payloads(chunk):
                stream_events.append(payload)
                if isinstance(payload.get("delta"), str):
                    text_parts.append(payload["delta"])
                elif payload.get("type") == "error":
                    error = payload
    except Exception as exc:  # noqa: BLE001 - eval should record failures
        error = {"error": repr(exc)}
    text = "".join(text_parts)
    blocks = parse_tool_blocks(text, skip_fenced=True)
    actual = blocks[0] if blocks else None
    exact = bool(
        expected
        and actual
        and actual.tool_type == expected.tool_type
        and _same_tool_content(actual.content, expected.content)
    )
    tool_ok = bool(expected and actual and actual.tool_type == expected.tool_type)
    return {
        "group": expected.tool_type if expected else _expected_name(row),
        "user": row["messages"][1]["content"],
        "expected": {
            "tool_type": expected.tool_type if expected else None,
            "content": expected.content if expected else None,
        },
        "actual": {
            "tool_type": actual.tool_type if actual else None,
            "content": actual.content if actual else None,
        },
        "tool_ok": tool_ok,
        "exact_ok": exact,
        "parsed_tool_count": len(blocks),
        "error": error,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "response": text[:1200],
    }


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18046/v1")
    parser.add_argument("--model", default="qwen35-9b-tool-router-v4-q4")
    parser.add_argument("--mode", choices=["row_system", "compact_map", "compact_map_v2", "compact_map_v3", "tiny", "zero"], default="row_system")
    parser.add_argument("--per-group", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=96)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rows = _select_rows(Path(args.rows), args.per_group)
    records = []
    for i, row in enumerate(rows, 1):
        record = await _run_one(args, row)
        records.append(record)
        print(
            json.dumps(
                {
                    "i": i,
                    "group": record["group"],
                    "tool_ok": record["tool_ok"],
                    "exact_ok": record["exact_ok"],
                    "elapsed_seconds": record["elapsed_seconds"],
                    "actual": record["actual"],
                },
                ensure_ascii=True,
            ),
            flush=True,
        )

    by_group = {}
    for record in records:
        group = record["group"]
        bucket = by_group.setdefault(group, {"n": 0, "tool_ok": 0, "exact_ok": 0, "errors": 0})
        bucket["n"] += 1
        bucket["tool_ok"] += int(record["tool_ok"])
        bucket["exact_ok"] += int(record["exact_ok"])
        bucket["errors"] += int(bool(record["error"]))

    summary = {
        "model": args.model,
        "base_url": args.base_url,
        "mode": args.mode,
        "rows": str(Path(args.rows).resolve()),
        "n": len(records),
        "tool_ok": sum(int(r["tool_ok"]) for r in records),
        "exact_ok": sum(int(r["exact_ok"]) for r in records),
        "errors": sum(int(bool(r["error"])) for r in records),
        "by_group": by_group,
        "records": records,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n")
    print("SUMMARY", json.dumps({k: v for k, v in summary.items() if k != "records"}, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(_main())
