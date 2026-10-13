#!/usr/bin/env python3
"""Run live-style Odysseus hard examples against the current agent route.

This is eval-first by design: it calls the same stream_agent_loop path used by
the app, records actual tool calls and mutations, and writes JSON/Markdown
results. It does not train, launch a server, or call the model endpoint
directly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.database import CalendarEvent, Document, SessionLocal
from scripts.ody_eval_email_fixture import email_fixture
from src.agent_loop import stream_agent_loop
from src.user_time import (
    current_datetime_context_message,
    set_user_tz_name,
    set_user_tz_offset,
)


DEFAULT_ENDPOINT = "http://host.docker.internal:18055/v1"
DEFAULT_MODEL = "qwen35-9b-tool-router-v44-fixture-followthrough-repair"
DEFAULT_OWNER = "pewds"
DEFAULT_TZ = "Asia/Tokyo"
DEFAULT_TZ_OFFSET_MIN = 540


CASES: list[dict[str, Any]] = [
    {
        "id": "calendar_tomorrow_8am",
        "kind": "calendar",
        "user": "Add event tomorrow for meeting 8am",
        "expect_first_tool": "manage_calendar",
        "forbidden_tools": ["web_search", "mcp__email__list_emails", "update_document"],
        "expect_args_subset": {
            "action": "create_event",
            "summary": "Meeting",
            "dtstart": "2026-08-22T08:00:00",
            "dtend": "2026-08-22T09:00:00",
        },
        "forbidden_answer_fragments": ["2025-09-10", "2024-06-10"],
    },
    {
        "id": "latest_emails_personal_domain",
        "kind": "email",
        "user": "What's my latest emails",
        "expect_first_tool_any": ["mcp__email__list_emails", "list_emails"],
        "forbidden_tools": ["web_search", "web_fetch"],
        "expect_args_subset": {
            "folder": "INBOX",
            "max_results": 1,
            "unread_only": False,
        },
    },
    {
        "id": "web_snails_synthesis",
        "kind": "web",
        "user": "Look up why snails bubble up sometimes",
        "expect_first_tool": "web_search",
        "forbidden_repeat_tools": ["web_search"],
        "required_final_any": ["mucus", "foam", "bubbles"],
        "required_final_any_2": ["stress", "irritant", "salt", "predator", "dehydration", "moisture"],
        "forbidden_final_patterns": [
            r"^\s*\d+\s+Web sources",
            r"WEB SEARCH RESULTS AND FETCHED CONTENT",
            r"```sources",
            r"Here are links for that topic",
        ],
    },
    {
        "id": "active_email_draft_update",
        "kind": "draft",
        "user": "Write a response to it saying 8am works for me",
        "active_document": {
            "title": "Manual email draft probe",
            "language": "email",
            "content": (
                "To: test@example.com\n"
                "Subject: Re: Test manual draft\n"
                "In-Reply-To: <manual@example.com>\n"
                "References: <manual@example.com>\n"
                "X-Source-UID: 999999\n"
                "X-Source-Folder: INBOX\n"
                "X-Attachments: []\n"
                "---\n\n"
                "---------- Previous message ----------\n"
                "From: Test Sender <test@example.com>\n"
                "Can you confirm the meeting time?\n"
            ),
        },
        "expect_first_tool_any": ["update_document", "edit_document"],
        "forbidden_tools": [
            "manage_calendar",
            "web_search",
            "mcp__email__list_emails",
            "mcp__email__read_email",
            "list_emails",
            "read_email",
        ],
        "doc_must_contain": ["8am works"],
        "doc_must_preserve": ["To:", "Subject:", "In-Reply-To:", "References:", "X-Source-UID:", "---"],
    },
]


def _parse_sse(chunk: str) -> dict[str, Any] | None:
    if not chunk.startswith("data: "):
        return None
    payload = chunk[6:].strip()
    if payload == "[DONE]":
        return {"type": "done"}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"type": "parse_error", "payload": payload[:500]}


def _parse_tool_args(command: Any) -> Any:
    if not isinstance(command, str):
        return command
    text = command.strip()
    if not text:
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _tool_name_matches(actual: str | None, expected: str) -> bool:
    if actual == expected:
        return True
    aliases = {
        "list_emails": {"mcp__email__list_emails", "list_emails"},
        "mcp__email__list_emails": {"mcp__email__list_emails", "list_emails"},
        "read_email": {"mcp__email__read_email", "read_email"},
        "mcp__email__read_email": {"mcp__email__read_email", "read_email"},
    }
    return actual in aliases.get(expected, set())


def _contains_all_subset(actual: Any, expected: dict[str, Any]) -> bool:
    if not isinstance(actual, dict):
        return False
    for key, value in expected.items():
        if actual.get(key) != value:
            return False
    return True


def _score_case(case: dict[str, Any], result: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    first_tool = result.get("first_tool")
    tool_names = result.get("tool_names") or []
    first_args = result.get("first_tool_args")
    final_answer = result.get("final_answer") or ""
    final_lower = final_answer.lower()

    if "expect_first_tool" in case and not _tool_name_matches(first_tool, case["expect_first_tool"]):
        failures.append(f"first_tool expected {case['expect_first_tool']!r}, got {first_tool!r}")

    if "expect_first_tool_any" in case:
        expected_any = case["expect_first_tool_any"]
        if not any(_tool_name_matches(first_tool, expected) for expected in expected_any):
            failures.append(f"first_tool expected one of {expected_any!r}, got {first_tool!r}")

    for forbidden in case.get("forbidden_tools", []):
        if any(_tool_name_matches(name, forbidden) for name in tool_names):
            failures.append(f"forbidden tool called: {forbidden}")

    for repeated in case.get("forbidden_repeat_tools", []):
        count = sum(1 for name in tool_names if _tool_name_matches(name, repeated))
        if count > 1:
            failures.append(f"tool repeated {count} times: {repeated}")

    expected_subset = case.get("expect_args_subset")
    if expected_subset and not _contains_all_subset(first_args, expected_subset):
        failures.append(f"first tool args missing expected subset: {expected_subset!r}; got {first_args!r}")

    for fragment in case.get("forbidden_answer_fragments", []):
        if fragment in final_answer:
            failures.append(f"forbidden answer fragment present: {fragment}")

    if "required_final_any" in case and not any(s.lower() in final_lower for s in case["required_final_any"]):
        failures.append(f"final answer missing any of {case['required_final_any']!r}")

    if "required_final_any_2" in case and not any(s.lower() in final_lower for s in case["required_final_any_2"]):
        failures.append(f"final answer missing any of {case['required_final_any_2']!r}")

    for pattern in case.get("forbidden_final_patterns", []):
        if re.search(pattern, final_answer, re.IGNORECASE | re.DOTALL):
            failures.append(f"forbidden final pattern matched: {pattern}")

    after_doc = result.get("active_document_after") or ""
    before_doc = result.get("active_document_before") or ""
    if case.get("doc_must_contain"):
        if after_doc == before_doc:
            failures.append("active document was not mutated")
        for fragment in case["doc_must_contain"]:
            if fragment.lower() not in after_doc.lower():
                failures.append(f"active document missing: {fragment}")
    for fragment in case.get("doc_must_preserve", []):
        if fragment not in after_doc:
            failures.append(f"active document did not preserve: {fragment}")

    return not failures, failures


async def _run_case(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    set_user_tz_name(args.timezone)
    set_user_tz_offset(args.tz_offset_min)

    db = SessionLocal()
    active_document = None
    active_doc_row = None
    active_before = ""
    if case.get("active_document"):
        fixture = case["active_document"]
        doc_id = f"ody-live-hard-{case['id']}-{uuid.uuid4().hex[:8]}"
        active_before = fixture["content"]
        active_doc_row = Document(
            id=doc_id,
            owner=args.owner,
            session_id=None,
            title=fixture["title"],
            language=fixture["language"],
            current_content=fixture["content"],
            version_count=1,
            is_active=True,
        )
        db.add(active_doc_row)
        db.commit()
        db.refresh(active_doc_row)
        active_document = SimpleNamespace(
            id=active_doc_row.id,
            title=active_doc_row.title,
            language=active_doc_row.language,
            current_content=active_doc_row.current_content,
        )

    messages = [
        current_datetime_context_message(),
        {"role": "user", "content": case["user"]},
    ]

    started = time.time()
    text_parts: list[str] = []
    final_replacements: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_outputs: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    stream_errors: list[dict[str, Any]] = []

    try:
        async for chunk in stream_agent_loop(
            args.endpoint,
            args.model,
            messages,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            max_rounds=args.max_rounds,
            max_tool_calls=args.max_tool_calls,
            active_document=active_document,
            session_id=f"ody-live-hard-{case['id']}",
            owner=args.owner,
            client_runtime_context={
                "timezone": args.timezone,
                "tz_offset_min": args.tz_offset_min,
            },
        ):
            event = _parse_sse(chunk)
            if not event:
                continue
            if event.get("type") == "done":
                break
            if event.get("type") == "parse_error":
                stream_errors.append(event)
                continue
            if "delta" in event and not event.get("thinking"):
                text_parts.append(str(event.get("delta") or ""))
            elif event.get("type") == "final_response":
                final_replacements.append(str(event.get("content") or ""))
            elif event.get("type") == "tool_start":
                tool_calls.append({
                    "tool": event.get("tool"),
                    "command": event.get("command"),
                    "args": _parse_tool_args(event.get("full_command") or event.get("command")),
                    "round": event.get("round"),
                    "call_id": event.get("call_id") or event.get("tool_call_id"),
                })
            elif event.get("type") == "tool_output":
                tool_outputs.append({
                    "tool": event.get("tool"),
                    "command": event.get("command"),
                    "output": event.get("output"),
                    "exit_code": event.get("exit_code"),
                    "call_id": event.get("call_id") or event.get("tool_call_id"),
                })
            elif event.get("type") == "metrics":
                metrics = event.get("data") or {}
            elif event.get("type") == "error":
                stream_errors.append(event)
    finally:
        active_after = ""
        if active_doc_row is not None:
            db.refresh(active_doc_row)
            active_after = active_doc_row.current_content or ""
            active_doc_row.archived = True
            active_doc_row.is_active = False
            db.commit()

        created_event_uids: list[str] = []
        for output in tool_outputs:
            if output.get("tool") != "manage_calendar":
                continue
            for uid in re.findall(r"#event-([A-Za-z0-9_.:-]+)", str(output.get("output") or "")):
                created_event_uids.append(uid)
        if created_event_uids and not args.keep_mutations:
            db.query(CalendarEvent).filter(CalendarEvent.uid.in_(created_event_uids)).delete(
                synchronize_session=False
            )
            db.commit()
        db.close()

    final_answer = "".join(text_parts)
    if final_replacements:
        final_answer = final_replacements[-1]

    result = {
        "id": case["id"],
        "kind": case["kind"],
        "user": case["user"],
        "first_tool": tool_calls[0]["tool"] if tool_calls else None,
        "first_tool_args": tool_calls[0]["args"] if tool_calls else None,
        "tool_names": [call["tool"] for call in tool_calls],
        "tool_calls": tool_calls,
        "tool_outputs": tool_outputs,
        "final_answer": final_answer,
        "active_document_before": active_before,
        "active_document_after": active_after,
        "active_document_changed": bool(active_before and active_after != active_before),
        "created_calendar_event_uids": created_event_uids,
        "created_calendar_events_deleted": bool(created_event_uids and not args.keep_mutations),
        "metrics": metrics,
        "stream_errors": stream_errors,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    passed, failures = _score_case(case, result)
    result["pass"] = passed
    result["failures"] = failures
    return result


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    rows = payload["results"]
    lines = [
        "# Odysseus Live Hard-Example Eval Results",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Model: `{payload['model']}`",
        f"- Endpoint: `{payload['endpoint']}`",
        f"- Cases: `{payload['summary']['passed']}/{payload['summary']['total']}` passed",
        "",
        "## Summary",
        "",
        "| Case | Pass | First tool | Failures |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        failures = "; ".join(row["failures"]) if row["failures"] else ""
        lines.append(
            f"| `{row['id']}` | `{row['pass']}` | `{row['first_tool']}` | {failures} |"
        )
    lines.extend(["", "## Details", ""])
    for row in rows:
        lines.extend([
            f"### {row['id']}",
            "",
            f"- User: `{row['user']}`",
            f"- Pass: `{row['pass']}`",
            f"- First tool: `{row['first_tool']}`",
            f"- All tools: `{', '.join(row['tool_names'])}`",
            f"- Active document changed: `{row['active_document_changed']}`",
            f"- Calendar event UIDs: `{', '.join(row['created_calendar_event_uids'])}`",
            "",
            "Final answer:",
            "",
            "```text",
            (row["final_answer"] or "")[:2000],
            "```",
            "",
        ])
        if row["failures"]:
            lines.extend(["Failures:", ""])
            lines.extend(f"- {failure}" for failure in row["failures"])
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _amain(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with email_fixture(args.email_fixture, owner=args.owner):
        for case in CASES:
            results.append(await _run_case(case, args))
    summary = {
        "total": len(results),
        "passed": sum(1 for row in results if row["pass"]),
        "failed": sum(1 for row in results if not row["pass"]),
    }
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "endpoint": args.endpoint,
        "model": args.model,
        "owner": args.owner,
        "timezone": args.timezone,
        "tz_offset_min": args.tz_offset_min,
        "summary": summary,
        "cases": CASES,
        "results": results,
    }
    json_path = out_dir / "actual_results.json"
    md_path = out_dir / "actual_results.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(md_path, payload)
    print(json.dumps({"summary": summary, "json": str(json_path), "md": str(md_path)}, indent=2))
    return 0 if summary["failed"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--timezone", default=DEFAULT_TZ)
    parser.add_argument("--tz-offset-min", type=int, default=DEFAULT_TZ_OFFSET_MIN)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--max-tokens", type=int, default=768)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--max-tool-calls", type=int, default=6)
    parser.add_argument("--keep-mutations", action="store_true")
    parser.add_argument("--email-fixture", action="store_true", help="Use deterministic fixture email MCP for local eval runs.")
    parser.add_argument(
        "--out-dir",
        default=str(REPO_ROOT / "data/evals/ody_live_hard_examples_current"),
    )
    return asyncio.run(_amain(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
