#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
import sys

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.database import CalendarCal, CalendarEvent, Document, Note, ScheduledTask, SessionLocal
from scripts.eval_odysseus_everyday_live_hard import load_cases, score_case, write_markdown
from scripts.eval_odysseus_live_hard_examples import _parse_tool_args
from scripts.eval_odysseus_tool_use import _cookie, _raise_for_status_with_body, _sse_events, _visible_event_text
from scripts.ody_eval_email_fixture import email_fixture


DEFAULT_OWNER = "pewds"
DEFAULT_TZ = "Asia/Tokyo"
DEFAULT_TZ_OFFSET_MIN = 540


def _default_cases() -> list[dict[str, Any]]:
    return [
        {
            "id": "no_tool_web_search_engine",
            "kind": "negative_web",
            "user": "Explain what a web search engine does without looking anything up.",
            "expect_no_tool": True,
            "must_answer_any": ["search", "pages"],
        },
        {
            "id": "positive_latest_email",
            "kind": "email",
            "user": "What's my latest email?",
            "expect_first_tool": "mcp__email__list_emails",
            "forbidden_tools": ["web_search"],
            "must_answer_any": ["latest email", "UID"],
        },
    ]


def _ensure_calendar(db: Any, owner: str) -> CalendarCal:
    cal = db.query(CalendarCal).filter(CalendarCal.owner == owner).first()
    if cal:
        return cal
    cal = CalendarCal(id=f"ody-app-gate-cal-{uuid.uuid4().hex[:8]}", owner=owner, name="Odysseus App Gate", source="local")
    db.add(cal)
    db.commit()
    db.refresh(cal)
    return cal


def _precreate_calendar(db: Any, owner: str, fixture: dict[str, str]) -> str:
    cal = _ensure_calendar(db, owner)
    uid = f"ody-app-gate-event-{uuid.uuid4().hex[:8]}"
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


def _create_session(client: httpx.Client, args: argparse.Namespace, case: dict[str, Any]) -> str:
    create = client.post(
        args.base_url.rstrip("/") + "/api/session",
        data={
            "name": "[eval-app-route] " + case["id"],
            "endpoint_url": args.endpoint,
            "endpoint_id": args.endpoint_id,
            "model": args.model,
            "skip_validation": "true",
            "rag": "false",
        },
        timeout=30,
    )
    _raise_for_status_with_body(create)
    return create.json()["id"]


def _seed_case_state(
    case: dict[str, Any], args: argparse.Namespace, session_id: str, client: httpx.Client
) -> dict[str, Any]:
    state = {"precreated_event_uid": "", "active_document_id": "", "active_document_before": ""}
    db = SessionLocal()
    try:
        if case.get("precreate_calendar_event"):
            state["precreated_event_uid"] = _precreate_calendar(db, args.owner, case["precreate_calendar_event"])
        if case.get("active_document"):
            # Seed through the same authenticated app runtime being evaluated.
            # Importing SessionLocal here may point at a different deployment's
            # SQLite file, producing cross-database foreign-key failures or,
            # worse, a fixture the live 7011 process can never see.
            fixture = case["active_document"]
            created = client.post(
                args.base_url.rstrip("/") + "/api/document",
                json={
                    "session_id": session_id,
                    "title": fixture["title"],
                    "language": fixture["language"],
                    "content": fixture["content"],
                },
                timeout=30,
            )
            _raise_for_status_with_body(created)
            state["active_document_id"] = created.json()["id"]
            state["active_document_before"] = fixture["content"]
    finally:
        db.close()
    return state


def _tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "tool_start":
            continue
        calls.append({
            "tool": event.get("tool"),
            "args": _parse_tool_args(event.get("full_command") or event.get("command")),
            "round": event.get("round"),
        })
    return calls


def _tool_outputs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "tool_output":
            continue
        outputs.append({
            "tool": event.get("tool"),
            "output": event.get("output"),
            "exit_code": event.get("exit_code"),
        })
    return outputs


def _collect_and_cleanup(
    case: dict[str, Any], args: argparse.Namespace, seeded: dict[str, Any], client: httpx.Client
) -> dict[str, Any]:
    result_state: dict[str, Any] = {}
    active_after = ""
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
            result_state["note_found"] = bool(note)
            result_state["task_found"] = bool(task)
            result_state["events"] = [
                {
                    "uid": event.uid,
                    "summary": event.summary,
                    "dtstart": event.dtstart.isoformat(),
                    "is_utc": bool(event.is_utc),
                    "status": event.status,
                }
                for event in events
            ]
            if note:
                db.delete(note)
            if task:
                db.delete(task)
            for event in events:
                db.delete(event)
        active_doc_id = seeded.get("active_document_id") or ""
        if active_doc_id:
            response = client.get(
                args.base_url.rstrip("/") + f"/api/document/{active_doc_id}", timeout=15
            )
            if response.is_success:
                active_after = response.json().get("current_content") or ""
                result_state["active_document_changed"] = active_after != (seeded.get("active_document_before") or "")
            with contextlib.suppress(Exception):
                client.delete(
                    args.base_url.rstrip("/") + f"/api/document/{active_doc_id}", timeout=15
                )
        db.commit()
    finally:
        db.close()
    return {"state": result_state, "active_document_after": active_after}


def _run_turn(client: httpx.Client, args: argparse.Namespace, case: dict[str, Any]) -> dict[str, Any]:
    session_id = _create_session(client, args, case)
    seeded = _seed_case_state(case, args, session_id, client)
    events: list[dict[str, Any]] = []
    prior_events: list[dict[str, Any]] = []
    response_text: list[str] = []
    stream_errors: list[dict[str, Any]] = []
    error = None
    started = time.time()

    def _form_data(message: str, current_case: dict[str, Any]) -> dict[str, str]:
        active_email = current_case.get("active_email") or {}
        form_data = {
            "message": message,
            "session": session_id,
            "mode": "agent",
            "agent_prompt_mode": "auto",
            "selected_endpoint_id": args.endpoint_id,
            "selected_model": args.model,
            "allow_web_search": (
                "true"
                if (
                    current_case.get("kind") == "web"
                    or current_case.get("allow_web_search") is True
                    or current_case.get("expect_first_tool") == "web_search"
                    or "web_search" in current_case.get("expect_first_tool_any", [])
                )
                else ""
            ),
            "client_runtime_context": json.dumps(
                {"timezone": args.timezone, "tz_offset_min": args.tz_offset_min},
                ensure_ascii=True,
            ),
        }
        if active_email:
            form_data.update({
                "active_email_uid": str(active_email.get("uid") or ""),
                "active_email_folder": str(active_email.get("folder") or "INBOX"),
                "active_email_account": str(active_email.get("account") or ""),
            })
        return form_data

    def _submit(message: str, current_case: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
        turn_events: list[dict[str, Any]] = []
        turn_text: list[str] = []
        turn_stream_errors: list[dict[str, Any]] = []
        with client.stream(
            "POST",
            args.base_url.rstrip("/") + "/api/chat_stream",
            data=_form_data(message, current_case),
            headers={
                "Accept": "text/event-stream",
                "X-Tz-Name": args.timezone,
                "X-Tz-Offset": str(args.tz_offset_min),
            },
            timeout=args.timeout,
        ) as response:
            _raise_for_status_with_body(response)
            for event in _sse_events(response):
                turn_events.append(event)
                if event.get("type") in {"error", "parse_error"}:
                    turn_stream_errors.append(event)
                text = _visible_event_text(event)
                if text:
                    if event.get("type") == "final_response":
                        turn_text[:] = [text]
                    else:
                        turn_text.append(text)
        return turn_events, turn_text, turn_stream_errors

    try:
        for prior in case.get("prior_turns", []):
            if isinstance(prior, str):
                prior_case = {"kind": "", "allow_web_search": False}
                prior_message = prior
            else:
                prior_case = prior
                prior_message = str(prior.get("user") or "")
            if not prior_message:
                continue
            prior_turn_events, _, prior_turn_errors = _submit(prior_message, prior_case)
            prior_events.extend(prior_turn_events)
            stream_errors.extend(prior_turn_errors)
        events, response_text, final_errors = _submit(case["user"], case)
        stream_errors.extend(final_errors)
    except Exception as exc:
        error = repr(exc)

    calls = _tool_calls(events)
    cleanup = _collect_and_cleanup(case, args, seeded, client)
    with contextlib.suppress(Exception):
        client.delete(args.base_url.rstrip("/") + f"/api/session/{session_id}", timeout=15)
    final_answer = "".join(response_text).strip()
    result = {
        "id": case["id"],
        "kind": case.get("kind", ""),
        "user": case["user"],
        "marker": case.get("marker", ""),
        "first_tool": calls[0]["tool"] if calls else None,
        "first_tool_args": calls[0]["args"] if calls else None,
        "tool_names": [call["tool"] for call in calls],
        "tool_calls": calls,
        "tool_outputs": _tool_outputs(events),
        "final_answer": final_answer,
        "answer": final_answer,
        "precreated_event_uid": seeded.get("precreated_event_uid", ""),
        "active_document_before": seeded.get("active_document_before", ""),
        "active_document_after": cleanup["active_document_after"],
        "state": cleanup["state"],
        "stream_errors": stream_errors,
        "prior_events": prior_events,
        "events": events,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    passed, failures = score_case(case, result)
    if error:
        failures.append(f"exception: {error}")
        passed = False
    if stream_errors:
        failures.append(f"stream errors: {len(stream_errors)}")
        passed = False
    result["pass"] = passed
    result["failures"] = failures
    return result


def _output_paths(args: argparse.Namespace) -> tuple[Path, Path | None]:
    if args.out_dir:
        out_dir = Path(args.out_dir)
        return out_dir / "actual_results.json", out_dir / "actual_results.md"
    output = Path(args.output)
    md = output.with_suffix(".md") if args.write_md else None
    return output, md


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--cookie-file", default="data/sessions.json")
    parser.add_argument("--output", default="data/evals/ody_app_route_smoke_results.json")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--cases-file", default="")
    parser.add_argument("--email-fixture", action="store_true")
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--timezone", default=DEFAULT_TZ)
    parser.add_argument("--tz-offset-min", type=int, default=DEFAULT_TZ_OFFSET_MIN)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--write-md", action="store_true")
    args = parser.parse_args()
    cases = load_cases(Path(args.cases_file)) if args.cases_file else _default_cases()
    client = httpx.Client(
        cookies={"odysseus_session": _cookie(Path(args.cookie_file), args.owner)},
        follow_redirects=False,
    )
    try:
        with email_fixture(args.email_fixture, owner=args.owner):
            results = [_run_turn(client, args, case) for case in cases]
    finally:
        client.close()
    summary = {
        "total": len(results),
        "passed": sum(1 for result in results if result["pass"]),
    }
    summary["failed"] = summary["total"] - summary["passed"]
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base_url,
        "endpoint": args.endpoint,
        "endpoint_id": args.endpoint_id,
        "model": args.model,
        "owner": args.owner,
        "timezone": args.timezone,
        "tz_offset_min": args.tz_offset_min,
        "summary": summary,
        "cases": cases,
        "results": results,
    }
    json_path, md_path = _output_paths(args)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    if md_path is not None:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        write_markdown(md_path, payload)
    print(json.dumps({"summary": summary, "json": str(json_path), "md": str(md_path) if md_path else ""}, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
