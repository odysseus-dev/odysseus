#!/usr/bin/env python3
"""Everyday-use Odysseus live-hard eval against the real agent loop.

Records actual tool calls, final answers, and backing DB mutations. This is not
an offline scorer: it calls stream_agent_loop with the selected endpoint/model.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.database import CalendarCal, CalendarEvent, Document, Note, ScheduledTask, SessionLocal
from scripts.ody_eval_email_fixture import email_fixture
from scripts.eval_odysseus_live_hard_examples import _parse_sse, _parse_tool_args
from src.agent_loop import stream_agent_loop
from src.user_time import current_datetime_context_message, now_user_local, set_user_tz_name, set_user_tz_offset, user_timezone


DEFAULT_OWNER = "pewds"
DEFAULT_TZ = "Asia/Tokyo"
DEFAULT_TZ_OFFSET_MIN = 540


def marker() -> str:
    return "ODY-LIVE-HARD-" + uuid.uuid4().hex[:8]


def _replace_marker_placeholders(value: Any, marker_value: str) -> Any:
    if isinstance(value, str):
        return value.replace("__MARKER__", marker_value)
    if isinstance(value, list):
        return [_replace_marker_placeholders(item, marker_value) for item in value]
    if isinstance(value, dict):
        return {key: _replace_marker_placeholders(item, marker_value) for key, item in value.items()}
    return value


def load_cases(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return cases()
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(selected, list):
        raise ValueError(f"cases file must contain a list or {{'cases': [...]}}: {path}")
    out: list[dict[str, Any]] = []
    for raw in selected:
        if not isinstance(raw, dict):
            raise ValueError(f"invalid case in {path}: {raw!r}")
        item = copy.deepcopy(raw)
        marker_value = item.get("marker")
        if marker_value == "__MARKER__" or "__MARKER__" in json.dumps(item, ensure_ascii=False):
            marker_value = marker()
            item = _replace_marker_placeholders(item, marker_value)
            item["marker"] = marker_value
        out.append(item)
    return out


def cases() -> list[dict[str, Any]]:
    note_marker = marker()
    task_marker = marker()
    cal_add_marker = marker()
    cal_move_marker = marker()
    cal_delete_marker = marker()
    return [
        {
            "id": "notes_create_marker",
            "kind": "note",
            "user": f"Add a note titled {note_marker} saying buy oats after school pickup",
            "marker": note_marker,
            "expect_first_tool": "manage_notes",
            "must_mutate": "note_created",
        },
        {
            "id": "tasks_daily_create_marker",
            "kind": "task",
            "user": f"Every morning at 7:30, remind me to review the latest inbox email. Name it {task_marker}",
            "marker": task_marker,
            "expect_first_tool": "manage_tasks",
            "must_mutate": "task_created",
        },
        {
            "id": "calendar_add_tomorrow_7pm",
            "kind": "calendar",
            "user": f"Add dinner tomorrow at 7pm titled {cal_add_marker}",
            "marker": cal_add_marker,
            "expect_first_tool": "manage_calendar",
            "must_mutate": "calendar_created_2026_08_22_19",
        },
        {
            "id": "calendar_move_tomorrow_8pm",
            "kind": "calendar",
            "user": f"Move my calendar event {cal_move_marker} to 8pm tomorrow",
            "marker": cal_move_marker,
            "precreate_calendar_event": {
                "summary": cal_move_marker,
                "dtstart": "2026-08-22T19:00:00",
                "dtend": "2026-08-22T20:00:00",
            },
            "expect_first_tool": "manage_calendar",
            "must_mutate": "calendar_moved_2026_08_22_20",
        },
        {
            "id": "calendar_delete_existing",
            "kind": "calendar",
            "user": f"Delete the calendar event named {cal_delete_marker}",
            "marker": cal_delete_marker,
            "precreate_calendar_event": {
                "summary": cal_delete_marker,
                "dtstart": "2026-08-22T13:00:00",
                "dtend": "2026-08-22T14:00:00",
            },
            "expect_first_tool": "manage_calendar",
            "must_mutate": "calendar_deleted",
        },
        {
            "id": "email_latest_uses_email_tool",
            "kind": "email",
            "user": "What's my latest emails",
            "expect_first_tool_any": ["mcp__email__list_emails", "list_emails"],
            "forbidden_tools": ["web_search", "web_fetch"],
            "must_answer_any": ["From:", "UID", "Booking.com", "latest email"],
        },
        {
            "id": "web_search_must_answer_snails",
            "kind": "web",
            "user": "Look up why snails bubble up sometimes",
            "expect_first_tool": "web_search",
            "forbidden_repeat_tools": ["web_search"],
            "must_answer_any": ["mucus", "foam", "bubble"],
            "must_answer_any_2": ["stress", "irritant", "predator", "moisture", "defense"],
            "forbidden_final": ["Here are links for that topic", "WEB SEARCH RESULTS", "```sources"],
        },
        {
            "id": "draft_active_email_update",
            "kind": "draft",
            "user": "Write a response to it saying 8am works for me",
            "active_document": {
                "title": "Everyday email draft probe",
                "language": "email",
                "content": (
                    "To: test@example.com\n"
                    "Subject: Re: Test manual draft\n"
                    "In-Reply-To: <manual@example.com>\n"
                    "References: <manual@example.com>\n"
                    "X-Source-UID: 999999\n"
                    "---\n\n"
                    "---------- Previous message ----------\n"
                    "Can you confirm the meeting time?\n"
                ),
            },
            "expect_first_tool_any": ["update_document", "edit_document"],
            "forbidden_tools": ["manage_calendar", "web_search", "mcp__email__list_emails", "mcp__email__read_email"],
            "must_mutate": "document_contains_8am",
        },
    ]


def _tool_name_matches(actual: str | None, expected: str) -> bool:
    if actual == expected:
        return True
    aliases = {
        "list_emails": {"mcp__email__list_emails", "list_emails"},
        "mcp__email__list_emails": {"mcp__email__list_emails", "list_emails"},
    }
    return actual in aliases.get(expected, set())


def _ensure_calendar(db: Any, owner: str) -> CalendarCal:
    cal = db.query(CalendarCal).filter(CalendarCal.owner == owner).first()
    if cal:
        return cal
    cal = CalendarCal(id=f"ody-live-hard-cal-{uuid.uuid4().hex[:8]}", owner=owner, name="Odysseus Live Hard", source="local")
    db.add(cal)
    db.commit()
    db.refresh(cal)
    return cal


def _precreate_calendar(db: Any, owner: str, fixture: dict[str, str]) -> str:
    cal = _ensure_calendar(db, owner)
    uid = f"ody-live-hard-event-{uuid.uuid4().hex[:8]}"
    event = CalendarEvent(
        uid=uid,
        calendar_id=cal.id,
        summary=fixture["summary"],
        dtstart=datetime.fromisoformat(fixture["dtstart"]),
        dtend=datetime.fromisoformat(fixture["dtend"]),
        all_day=False,
        is_utc=False,
        origin="local",
        status="confirmed",
    )
    db.add(event)
    db.commit()
    return uid


async def run_case(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    set_user_tz_name(args.timezone)
    set_user_tz_offset(args.tz_offset_min)

    db = SessionLocal()
    precreated_event_uid = ""
    active_doc_row = None
    active_document = None
    active_before = ""
    try:
        if case.get("precreate_calendar_event"):
            precreated_event_uid = _precreate_calendar(db, args.owner, case["precreate_calendar_event"])
        if case.get("active_document"):
            fixture = case["active_document"]
            active_before = fixture["content"]
            active_doc_row = Document(
                id=f"ody-live-hard-doc-{uuid.uuid4().hex[:8]}",
                owner=args.owner,
                title=fixture["title"],
                language=fixture["language"],
                current_content=fixture["content"],
                version_count=1,
                is_active=True,
                archived=False,
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
    finally:
        db.close()

    messages = [current_datetime_context_message(), {"role": "user", "content": case["user"]}]
    text_parts: list[str] = []
    final_replacements: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_outputs: list[dict[str, Any]] = []
    stream_errors: list[dict[str, Any]] = []
    started = time.time()

    async for chunk in stream_agent_loop(
        args.endpoint,
        args.model,
        messages,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        max_rounds=args.max_rounds,
        max_tool_calls=args.max_tool_calls,
        active_document=active_document,
        session_id=f"ody-everyday-live-hard-{case['id']}",
        owner=args.owner,
        client_runtime_context={"timezone": args.timezone, "tz_offset_min": args.tz_offset_min},
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
                "args": _parse_tool_args(event.get("full_command") or event.get("command")),
                "round": event.get("round"),
            })
        elif event.get("type") == "tool_output":
            tool_outputs.append({
                "tool": event.get("tool"),
                "output": event.get("output"),
                "exit_code": event.get("exit_code"),
            })
        elif event.get("type") == "error":
            stream_errors.append(event)

    final_answer = final_replacements[-1] if final_replacements else "".join(text_parts)
    result = {
        "id": case["id"],
        "kind": case["kind"],
        "user": case["user"],
        "marker": case.get("marker", ""),
        "first_tool": tool_calls[0]["tool"] if tool_calls else None,
        "first_tool_args": tool_calls[0]["args"] if tool_calls else None,
        "tool_names": [call["tool"] for call in tool_calls],
        "tool_calls": tool_calls,
        "tool_outputs": tool_outputs,
        "final_answer": final_answer,
        "precreated_event_uid": precreated_event_uid,
        "active_document_before": active_before,
        "active_document_after": "",
        "state": {},
        "stream_errors": stream_errors,
        "elapsed_seconds": round(time.time() - started, 3),
    }

    db = SessionLocal()
    try:
        marker_text = case.get("marker") or ""
        if marker_text:
            note = db.query(Note).filter(Note.owner == args.owner, Note.archived == False).filter(  # noqa: E712
                (Note.title.contains(marker_text)) | (Note.content.contains(marker_text))
            ).first()
            task = db.query(ScheduledTask).filter(ScheduledTask.owner == args.owner).filter(
                (ScheduledTask.name.contains(marker_text)) | (ScheduledTask.prompt.contains(marker_text))
            ).first()
            events = db.query(CalendarEvent).filter(CalendarEvent.summary.contains(marker_text)).all()
            result["state"]["note_found"] = bool(note)
            result["state"]["task_found"] = bool(task)
            result["state"]["events"] = [
                {
                    "uid": e.uid,
                    "summary": e.summary,
                    "dtstart": e.dtstart.isoformat(),
                    "is_utc": bool(e.is_utc),
                    "status": e.status,
                }
                for e in events
            ]
            if note:
                db.delete(note)
            if task:
                db.delete(task)
            for event in events:
                db.delete(event)
        if active_doc_row is not None:
            doc = db.query(Document).filter(Document.id == active_doc_row.id).first()
            if doc:
                result["active_document_after"] = doc.current_content or ""
                result["state"]["active_document_changed"] = (doc.current_content or "") != active_before
                doc.archived = True
                doc.is_active = False
        db.commit()
    finally:
        db.close()

    result["pass"], result["failures"] = score_case(case, result)
    return result


def score_case(case: dict[str, Any], result: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    first = result.get("first_tool")
    tools = result.get("tool_names") or []
    answer = result.get("final_answer") or ""
    answer_lower = answer.lower()

    if "expect_first_tool" in case and not _tool_name_matches(first, case["expect_first_tool"]):
        failures.append(f"first_tool expected {case['expect_first_tool']!r}, got {first!r}")
    if "expect_first_tool_any" in case and not any(_tool_name_matches(first, expected) for expected in case["expect_first_tool_any"]):
        failures.append(f"first_tool expected one of {case['expect_first_tool_any']!r}, got {first!r}")
    if case.get("expect_no_tool") and tools:
        failures.append(f"expected no tool calls, got {tools!r}")
    for forbidden in case.get("forbidden_tools", []):
        if any(_tool_name_matches(tool, forbidden) for tool in tools):
            failures.append(f"forbidden tool called: {forbidden}")
    if case.get("forbidden_tool_arg_values"):
        tool_arg_text = "\n".join(
            json.dumps(call.get("args"), ensure_ascii=False, sort_keys=True)
            for call in result.get("tool_calls", [])
        ).lower()
        for token in case["forbidden_tool_arg_values"]:
            if str(token).lower() in tool_arg_text:
                failures.append(f"forbidden tool arg value present: {token}")
    for repeated in case.get("forbidden_repeat_tools", []):
        count = sum(1 for tool in tools if _tool_name_matches(tool, repeated))
        if count > 1:
            failures.append(f"tool repeated {count} times: {repeated}")
    for token in case.get("forbidden_final", []):
        if token.lower() in answer_lower:
            failures.append(f"forbidden final text present: {token}")
    if case.get("must_answer_any") and not any(token.lower() in answer_lower for token in case["must_answer_any"]):
        failures.append(f"final answer missing any of {case['must_answer_any']!r}")
    if case.get("must_answer_any_2") and not any(token.lower() in answer_lower for token in case["must_answer_any_2"]):
        failures.append(f"final answer missing any of {case['must_answer_any_2']!r}")
    if case.get("must_answer_any_3") and not any(token.lower() in answer_lower for token in case["must_answer_any_3"]):
        failures.append(f"final answer missing any of {case['must_answer_any_3']!r}")
    active_after = result.get("active_document_after") or ""
    active_after_lower = active_after.lower()
    if "expect_document_changed" in case:
        changed = bool((result.get("state") or {}).get("active_document_changed"))
        if changed != bool(case["expect_document_changed"]):
            failures.append(f"active document changed={changed}, expected {bool(case['expect_document_changed'])}")
    if case.get("must_active_document_contain_all"):
        missing = [
            token for token in case["must_active_document_contain_all"]
            if str(token).lower() not in active_after_lower
        ]
        if missing:
            failures.append(f"active document missing required text: {missing!r}")
    if case.get("must_active_document_contain_any") and not any(
        str(token).lower() in active_after_lower for token in case["must_active_document_contain_any"]
    ):
        failures.append(f"active document missing any of {case['must_active_document_contain_any']!r}")
    for preserved in case.get("must_preserve_active_document_all", []):
        if str(preserved) not in active_after:
            failures.append(f"active document did not preserve {preserved!r}")
    web_queries = [
        str(call.get("args") if not isinstance(call.get("args"), dict) else call.get("args", {}).get("query") or "")
        for call in result.get("tool_calls", [])
        if _tool_name_matches(call.get("tool"), "web_search")
    ]
    web_query_text = "\n".join(web_queries).lower()
    for key in ("must_query_any", "must_query_any_2", "must_query_any_3", "must_query_any_4"):
        if case.get(key) and not any(token.lower() in web_query_text for token in case[key]):
            failures.append(f"web_search query missing any of {case[key]!r}")
    for token in case.get("forbidden_query_any", []):
        if token.lower() in web_query_text:
            failures.append(f"forbidden query text present: {token}")
    if "min_web_searches" in case:
        expected_min = int(case["min_web_searches"])
        if len(web_queries) < expected_min:
            failures.append(f"expected at least {expected_min} web_search call(s), got {len(web_queries)}")
    if "max_web_searches" in case:
        expected_max = int(case["max_web_searches"])
        if len(web_queries) > expected_max:
            failures.append(f"expected at most {expected_max} web_search call(s), got {len(web_queries)}")
    if case.get("must_emit_ui_event"):
        expected_ui_event = str(case["must_emit_ui_event"])
        emitted = False
        for event in result.get("events") or []:
            if event.get("type") == "ui_control":
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                if data.get("ui_event") == expected_ui_event:
                    emitted = True
                    break
            if event.get("type") == "tool_output" and event.get("ui_event") == expected_ui_event:
                emitted = True
                break
        if not emitted:
            failures.append(f"missing ui event: {expected_ui_event}")

    state = result.get("state") or {}
    mutation = case.get("must_mutate")
    events = state.get("events") or []
    tomorrow = now_user_local().date() + timedelta(days=1)
    tomorrow_19 = f"{tomorrow.isoformat()}T19:00"
    tomorrow_20 = f"{tomorrow.isoformat()}T20:00"
    tomorrow_19_utc = (
        datetime.combine(tomorrow, datetime.min.time().replace(hour=19), tzinfo=user_timezone())
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M")
    )
    tomorrow_20_utc = (
        datetime.combine(tomorrow, datetime.min.time().replace(hour=20), tzinfo=user_timezone())
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M")
    )
    if mutation == "note_created" and not state.get("note_found"):
        failures.append("note was not created in DB")
    elif mutation == "task_created" and not state.get("task_found"):
        failures.append("scheduled task was not created in DB")
    elif mutation == "calendar_created_2026_08_22_19":
        if not any(
            tomorrow_19 in event.get("dtstart", "")
            or (event.get("is_utc") and tomorrow_19_utc in event.get("dtstart", ""))
            for event in events
        ):
            failures.append(f"calendar event was not created for {tomorrow_19}")
    elif mutation == "calendar_moved_2026_08_22_20":
        if not any(
            tomorrow_20 in event.get("dtstart", "")
            or (event.get("is_utc") and tomorrow_20_utc in event.get("dtstart", ""))
            for event in events
        ):
            failures.append(f"calendar event was not moved to {tomorrow_20}")
    elif mutation == "calendar_created_at":
        expected_dtstart = str(case.get("expect_created_event_dtstart") or "")
        if not expected_dtstart:
            failures.append("calendar_created_at requires expect_created_event_dtstart")
        elif not any(expected_dtstart in event.get("dtstart", "") for event in events):
            failures.append(f"calendar event was not created for {expected_dtstart}")
    elif mutation == "calendar_deleted":
        if events:
            failures.append("calendar event still exists after delete request")
    elif mutation == "document_contains_8am":
        if not state.get("active_document_changed"):
            failures.append("active document was not mutated")
        if "8am works" not in active_after_lower:
            failures.append("active document missing '8am works'")
        for preserved in ["To:", "Subject:", "In-Reply-To:", "References:", "X-Source-UID:", "---"]:
            if preserved not in active_after:
                failures.append(f"active document did not preserve {preserved}")

    return not failures, failures


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Odysseus Everyday Live-Hard Eval Results",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Model: `{payload['model']}`",
        f"- Endpoint: `{payload['endpoint']}`",
        f"- Cases: `{payload['summary']['passed']}/{payload['summary']['total']}` passed",
        "",
        "| Case | Pass | First tool | Failures |",
        "| --- | --- | --- | --- |",
    ]
    for row in payload["results"]:
        failures = "; ".join(row["failures"])
        lines.append(f"| `{row['id']}` | `{row['pass']}` | `{row['first_tool']}` | {failures} |")
    lines.extend(["", "## Details", ""])
    for row in payload["results"]:
        lines.extend([
            f"### {row['id']}",
            "",
            f"- User: `{row['user']}`",
            f"- First tool: `{row['first_tool']}`",
            f"- Tools: `{', '.join(row['tool_names'])}`",
            f"- State: `{json.dumps(row['state'], ensure_ascii=False)[:1000]}`",
            "",
            "Final answer:",
            "",
            "```text",
            (row.get("final_answer") or "")[:2000],
            "```",
            "",
        ])
        if row["failures"]:
            lines.append("Failures:")
            lines.extend(f"- {failure}" for failure in row["failures"])
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def amain(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    selected = load_cases(Path(args.cases_file) if args.cases_file else None)
    with email_fixture(args.email_fixture, owner=args.owner):
        results = [await run_case(case, args) for case in selected]
    summary = {"total": len(results), "passed": sum(1 for row in results if row["pass"])}
    summary["failed"] = summary["total"] - summary["passed"]
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "endpoint": args.endpoint,
        "model": args.model,
        "owner": args.owner,
        "summary": summary,
        "cases": selected,
        "results": results,
    }
    (out_dir / "actual_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(out_dir / "actual_results.md", payload)
    print(json.dumps({"summary": summary, "json": str(out_dir / "actual_results.json"), "md": str(out_dir / "actual_results.md")}, indent=2))
    return 0 if summary["failed"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--timezone", default=DEFAULT_TZ)
    parser.add_argument("--tz-offset-min", type=int, default=DEFAULT_TZ_OFFSET_MIN)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--max-tokens", type=int, default=768)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--max-tool-calls", type=int, default=8)
    parser.add_argument("--cases-file", default=None, help="Optional JSON file containing held-out live-hard cases.")
    parser.add_argument("--email-fixture", action="store_true", help="Use deterministic fixture email MCP for local eval runs.")
    parser.add_argument("--out-dir", required=True)
    return asyncio.run(amain(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
