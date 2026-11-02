#!/usr/bin/env python3
"""Multi-turn Odysseus tool-use eval for contextual follow-up behavior."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

try:
    from scripts.eval_odysseus_tool_use import (
        _cookie,
        _raise_for_status_with_body,
        _sse_events,
        _tool_approval_from_event,
        _visible_event_text,
    )
except ModuleNotFoundError:
    from eval_odysseus_tool_use import (
        _cookie,
        _raise_for_status_with_body,
        _sse_events,
        _tool_approval_from_event,
        _visible_event_text,
    )


SCENARIOS: list[dict[str, Any]] = [
    {
        "scenario": "public_domain_art_links_followup",
        "turns": [
            {
                "message": "What are some good sites for public domain art?",
                "expected_tool": "no_tool",
                "required_any": ["public domain", "met", "wikimedia", "rijksmuseum"],
            },
            {
                "message": "send links",
                "expected_tool": "web_search",
                "required_any": ["http", "wikimedia", "metmuseum", "rijksmuseum", "public domain"],
            },
        ],
    },
    {
        "scenario": "notes_then_identity_boundary",
        "turns": [
            {
                "message": "what are my notes?",
                "expected_tool": "manage_notes",
                "expected_action": "list",
                "required_any": ["note", "[", "test scenario"],
            },
            {
                "message": "who are you?",
                "expected_tool": "no_tool",
                "required_any": ["odysseus", "assistant"],
            },
        ],
    },
    {
        "scenario": "email_followup_search",
        "turns": [
            {
                "message": "what is my latest email?",
                "expected_tool": "mcp__email__list_emails",
                "required_any": ["email", "from", "subject", "latest"],
            },
            {
                "message": "find emails from Runpod instead",
                "expected_tool": "mcp__email__search_emails",
                "required_any": ["runpod", "email", "no emails"],
            },
        ],
    },
    {
        "scenario": "calendar_then_general_fact",
        "turns": [
            {
                "message": "what is on my calendar?",
                "expected_tool": "manage_calendar",
                "expected_action": "list_events",
                "required_any": ["event", "calendar", "found", "no events"],
            },
            {
                "message": "what does VAT stand for?",
                "expected_tool": "no_tool",
                "required_any": ["value-added tax", "value added tax"],
            },
        ],
    },
    {
        "scenario": "public_domain_art_typo_links_followup",
        "turns": [
            {
                "message": "What are some good sites for public domain art?",
                "expected_tool": "no_tool",
                "required_any": ["public domain", "met", "wikimedia", "rijksmuseum"],
            },
            {
                "message": "sned links for those",
                "expected_tool": "web_search",
                "required_any": ["http", "wikimedia", "metmuseum", "rijksmuseum", "public domain"],
            },
        ],
    },
    {
        "scenario": "notes_then_calendar_switch",
        "turns": [
            {
                "message": "what are my notes?",
                "expected_tool": "manage_notes",
                "expected_action": "list",
                "required_any": ["note", "[", "test scenario"],
            },
            {
                "message": "what is on my calendar next week?",
                "expected_tool": "manage_calendar",
                "expected_action": "list_events",
                "required_any": ["event", "calendar", "found", "no events"],
            },
        ],
    },
    {
        "scenario": "email_then_ambiguous_links_clarify",
        "turns": [
            {
                "message": "what is my latest email?",
                "expected_tool": "mcp__email__list_emails",
                "required_any": ["email", "from", "subject", "latest"],
            },
            {
                "message": "send links",
                "expected_tool": "no_tool",
                "required_any": ["which links", "what links", "clarify", "what topic", "which topic"],
            },
        ],
    },
    {
        "scenario": "web_answer_then_calendar_boundary",
        "turns": [
            {
                "message": "What are some good sites for public domain art?",
                "expected_tool": "no_tool",
                "required_any": ["public domain", "met", "wikimedia", "rijksmuseum"],
            },
            {
                "message": "what is on my calendar?",
                "expected_tool": "manage_calendar",
                "expected_action": "list_events",
                "required_any": ["event", "calendar", "found", "no events"],
            },
        ],
    },
    {
        "scenario": "public_domain_art_sites_tail_followup",
        "turns": [
            {
                "message": "What are some good sites for public domain art?",
                "expected_tool": "no_tool",
                "required_any": ["public domain", "met", "wikimedia", "rijksmuseum"],
            },
            {
                "message": "send links for the sites",
                "expected_tool": "web_search",
                "required_any": ["http", "wikimedia", "metmuseum", "rijksmuseum", "public domain"],
            },
        ],
    },
    {
        "scenario": "public_domain_art_typo_bare_links_followup",
        "turns": [
            {
                "message": "What are some good sites for public domain art?",
                "expected_tool": "no_tool",
                "required_any": ["public domain", "met", "wikimedia", "rijksmuseum"],
            },
            {
                "message": "sned links",
                "expected_tool": "web_search",
                "required_any": ["http", "wikimedia", "metmuseum", "rijksmuseum", "public domain"],
            },
        ],
    },
    {
        "scenario": "public_domain_art_bare_websites_followup",
        "turns": [
            {
                "message": "What are some good sites for public domain art?",
                "expected_tool": "no_tool",
                "required_any": ["public domain", "met", "wikimedia", "rijksmuseum"],
            },
            {
                "message": "for the websites",
                "expected_tool": "web_search",
                "required_any": ["http", "wikimedia", "metmuseum", "rijksmuseum", "public domain"],
            },
        ],
    },
    {
        "scenario": "email_then_typo_links_tail_clarify",
        "turns": [
            {
                "message": "what is my latest email?",
                "expected_tool": "mcp__email__list_emails",
                "required_any": ["email", "from", "subject", "latest"],
            },
            {
                "message": "sned links for those",
                "expected_tool": "no_tool",
                "required_any": ["which links", "what links", "clarify", "what topic", "which topic", "topic"],
            },
        ],
    },
    {
        "scenario": "email_then_bare_websites_clarify",
        "turns": [
            {
                "message": "what is my latest email?",
                "expected_tool": "mcp__email__list_emails",
                "required_any": ["email", "from", "subject", "latest"],
            },
            {
                "message": "for the websites",
                "expected_tool": "no_tool",
                "required_any": ["which links", "what links", "clarify", "what topic", "which topic", "topic", "website"],
            },
        ],
    },
    {
        "scenario": "notes_then_typo_links_tail_clarify",
        "turns": [
            {
                "message": "what are my notes?",
                "expected_tool": "manage_notes",
                "expected_action": "list",
                "required_any": ["note", "[", "test scenario"],
            },
            {
                "message": "sned links for those",
                "expected_tool": "no_tool",
                "required_any": ["which links", "what links", "clarify", "what topic", "which topic", "topic"],
            },
        ],
    },
    {
        "scenario": "notes_crud_followthrough",
        "fixture_prefix": "ODY-EVAL-CRUD-NOTES-",
        "turns": [
            {
                "message": "Create a note titled ODY-EVAL-CRUD-NOTES-FLOW with content alpha checkpoint.",
                "expected_tool": "manage_notes",
                "expected_actions": ["add", "create"],
                "required_all": ["created", "ody-eval-crud-notes-flow"],
                "max_tool_count": 1,
            },
            {
                "message": "Update that note so its content says beta checkpoint.",
                "expected_tool": "manage_notes",
                "expected_action": "update",
                "required_all": ["updated", "note"],
                "max_tool_count": 1,
            },
            {
                "message": "Delete that note.",
                "expected_tool": "manage_notes",
                "expected_action": "delete",
                "required_all": ["deleted", "note"],
                "max_tool_count": 1,
            },
        ],
    },
    {
        "scenario": "calendar_crud_followthrough",
        "fixture_prefix": "ODY-EVAL-CRUD-CALENDAR-",
        "turns": [
            {
                "message": (
                    "Create a calendar event titled ODY-EVAL-CRUD-CALENDAR-FLOW "
                    "on 2026-08-25 from 10:00 to 10:30 at Test Lab."
                ),
                "expected_tool": "manage_calendar",
                "expected_actions": ["create_event", "create"],
                "required_all": ["created", "event", "ody-eval-crud-calendar-flow"],
                "max_tool_count": 1,
            },
            {
                "message": "Update that calendar event location to Blue Room.",
                "expected_tool": "manage_calendar",
                "expected_actions": ["update_event", "update"],
                "required_all": ["updated", "event"],
                "max_tool_count": 1,
            },
            {
                "message": "Delete that calendar event.",
                "expected_tool": "manage_calendar",
                "expected_actions": ["delete_event", "delete"],
                "required_all": ["deleted", "event"],
                "max_tool_count": 1,
            },
        ],
    },
    {
        "scenario": "memory_crud_followthrough",
        "fixture_prefix": "ODY-EVAL-CRUD-MEMORY-",
        "turns": [
            {
                "message": "Remember this temporary eval fact: ODY-EVAL-CRUD-MEMORY-FLOW alpha checkpoint.",
                "expected_tool": "manage_memory",
                "expected_action": "add",
                "required_all": ["memory", "added"],
                "max_tool_count": 1,
            },
            {
                "message": "Update that memory to say ODY-EVAL-CRUD-MEMORY-FLOW beta checkpoint.",
                "expected_tool": "manage_memory",
                "expected_action": "edit",
                "required_all": ["memory", "updated"],
                "max_tool_count": 1,
            },
            {
                "message": "Delete that memory.",
                "expected_tool": "manage_memory",
                "expected_action": "delete",
                "required_all": ["memory", "deleted"],
                "max_tool_count": 1,
            },
        ],
    },
    {
        "scenario": "memory_add_one_call_efficiency",
        "fixture_prefix": "ODY-EVAL-CRUD-MEMORY-",
        "turns": [
            {
                "message": "Remember this temporary eval fact: ODY-EVAL-CRUD-MEMORY-ONECALL alpha checkpoint.",
                "expected_tool": "manage_memory",
                "expected_action": "add",
                "required_all": ["memory", "added"],
                "max_tool_count": 1,
            },
            {
                "message": "Delete that memory.",
                "expected_tool": "manage_memory",
                "expected_action": "delete",
                "required_all": ["memory", "deleted"],
                "max_tool_count": 1,
            },
        ],
    },
    {
        "scenario": "memory_add_wording_variants_efficiency",
        "fixture_prefix": "ODY-EVAL-CRUD-MEMORY-",
        "turns": [
            {
                "message": "Save this as a memory: ODY-EVAL-CRUD-MEMORY-VAR-A alpha checkpoint.",
                "expected_tool": "manage_memory",
                "expected_action": "add",
                "required_all": ["memory", "added"],
                "max_tool_count": 1,
            },
            {
                "message": "Delete that memory.",
                "expected_tool": "manage_memory",
                "expected_action": "delete",
                "required_all": ["memory", "deleted"],
                "max_tool_count": 1,
            },
            {
                "message": "Add to memory that ODY-EVAL-CRUD-MEMORY-VAR-B beta checkpoint is temporary.",
                "expected_tool": "manage_memory",
                "expected_action": "add",
                "required_all": ["memory", "added"],
                "max_tool_count": 1,
            },
            {
                "message": "Remove that memory.",
                "expected_tool": "manage_memory",
                "expected_action": "delete",
                "required_all": ["memory", "deleted"],
                "max_tool_count": 1,
            },
            {
                "message": "Please remember: ODY-EVAL-CRUD-MEMORY-VAR-C gamma checkpoint.",
                "expected_tool": "manage_memory",
                "expected_action": "add",
                "required_all": ["memory", "added"],
                "max_tool_count": 1,
            },
            {
                "message": "Forget that memory.",
                "expected_tool": "manage_memory",
                "expected_action": "delete",
                "required_all": ["memory", "deleted"],
                "max_tool_count": 1,
            },
        ],
    },
    {
        "scenario": "memory_no_tool_boundary",
        "turns": [
            {
                "message": "do you remember what VAT stands for?",
                "expected_tool": "no_tool",
                "required_any": ["value-added tax", "value added tax"],
            },
            {
                "message": "what should I remember before buying public domain art?",
                "expected_tool": "no_tool",
                "required_any": ["license", "copyright", "public domain", "source"],
            },
            {
                "message": "remind me what Sweden is bordered by",
                "expected_tool": "no_tool",
                "required_any": ["norway", "finland"],
            },
            {
                "message": "what does it mean to remember something in a computer?",
                "expected_tool": "no_tool",
                "required_any": ["store", "storage", "memory", "data", "information"],
            },
        ],
    },
    {
        "scenario": "tasks_crud_followthrough",
        "fixture_prefix": "ODY-EVAL-CRUD-TASKS-",
        "turns": [
            {
                "message": (
                    "Create a scheduled task named ODY-EVAL-CRUD-TASKS-FLOW that runs daily at 09:00 UTC "
                    "and has prompt alpha checkpoint."
                ),
                "expected_tool": "manage_tasks",
                "expected_action": "create",
                "required_all": ["created", "task", "ody-eval-crud-tasks-flow"],
                "max_tool_count": 1,
            },
            {
                "message": "Update that task prompt to beta checkpoint.",
                "expected_tool": "manage_tasks",
                "expected_action": "edit",
                "required_all": ["updated", "task"],
                "max_tool_count": 1,
            },
            {
                "message": "Delete that task.",
                "expected_tool": "manage_tasks",
                "expected_action": "delete",
                "required_all": ["deleted", "task"],
                "max_tool_count": 1,
            },
        ],
    },
    {
        "scenario": "documents_create_delete_followthrough",
        "fixture_prefix": "ODY-EVAL-CRUD-DOCUMENTS-",
        "turns": [
            {
                "message": (
                    "Create an editor document titled ODY-EVAL-CRUD-DOCUMENTS-FLOW "
                    "with markdown content alpha checkpoint."
                ),
                "expected_tool": "create_document",
                "required_all": ["document", "ody-eval-crud-documents-flow"],
                "max_tool_count": 1,
            },
            {
                "message": "Delete that document.",
                "expected_tool": "manage_documents",
                "expected_action": "delete",
                "required_all": ["deleted", "document"],
                "max_tool_count": 1,
            },
        ],
    },
]


TOOL_ALIASES = {
    "mcp_email_list_emails": "mcp__email__list_emails",
    "mcp_email_search_emails": "mcp__email__search_emails",
    "list_emails": "mcp__email__list_emails",
    "search_emails": "mcp__email__search_emails",
}


def malformed_text_surface(response_text: str) -> bool:
    value = (response_text or "").lower()
    if any(
        marker in value
        for marker in (
            "<function",
            "<parameter",
            "function=",
            "parameter=",
            "</parameter",
            '"function"',
            "tool_call",
            "|end|",
            "web search results and fetched content",
            "search results summary:",
        )
    ):
        return True
    return any(
        re.search(pattern, response_text or "")
        for pattern in (
            r"\bIamOdysseus\b",
            r"\bSwedenisin\b",
            r"\bVATstands\b",
            r"\bpublic\s+domain\s+ar\b(?!t)",
            r"\bthe\s+me\s+open\s+access\b",
            r"\bar\s+institute\b(?!t)",
            r"\bpublicdomainar\b",
            r"\bTheMeOpenAccess\b",
            r"\bCanyouclarifywhich\b",
            r"\bwan me\s+link\b",
            r"\bwhat you wan me\b",
        )
    )


def canonical_tool(tool: str | None) -> str | None:
    if not tool:
        return tool
    return TOOL_ALIASES.get(tool, tool)


def parse_action(command: str | None) -> str:
    if not command:
        return ""
    try:
        parsed = json.loads(command)
    except json.JSONDecodeError:
        parsed = command
    if isinstance(parsed, dict):
        return str(parsed.get("action") or "")
    if isinstance(parsed, str):
        return parsed.strip().splitlines()[0] if parsed.strip() else ""
    return ""


def _fixture_owner() -> str:
    return os.environ.get("ODY_EVAL_OWNER", "pewds")


def _cleanup_crud_fixtures() -> None:
    """Remove only eval-owned CRUD artifacts created by this script."""
    owner = _fixture_owner()
    try:
        from core.database import (
            CalendarCal,
            CalendarEvent,
            Document,
            DocumentVersion,
            Note,
            ScheduledTask,
            SessionLocal,
        )
    except Exception as exc:
        print(json.dumps({"cleanup_warning": f"database import failed: {exc!r}"}), flush=True)
    else:
        db = SessionLocal()
        try:
            notes_q = db.query(Note).filter(Note.title.like("ODY-EVAL-CRUD-%"))
            if owner:
                notes_q = notes_q.filter(Note.owner == owner)
            for note in notes_q.all():
                db.delete(note)

            events_q = db.query(CalendarEvent).filter(CalendarEvent.summary.like("ODY-EVAL-CRUD-%"))
            if owner:
                events_q = events_q.join(CalendarCal, CalendarEvent.calendar_id == CalendarCal.id).filter(
                    CalendarCal.owner == owner
                )
            for event in events_q.all():
                db.delete(event)

            cals_q = db.query(CalendarCal).filter(CalendarCal.name.like("ODY-EVAL-CRUD-%"))
            if owner:
                cals_q = cals_q.filter(CalendarCal.owner == owner)
            for calendar in cals_q.all():
                db.delete(calendar)

            docs_q = db.query(Document).filter(Document.title.like("ODY-EVAL-CRUD-%"))
            if owner:
                docs_q = docs_q.filter(Document.owner == owner)
            for doc in docs_q.all():
                db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id).delete()
                db.delete(doc)

            tasks_q = db.query(ScheduledTask).filter(ScheduledTask.name.like("ODY-EVAL-CRUD-%"))
            if owner:
                tasks_q = tasks_q.filter(ScheduledTask.owner == owner)
            tasks_q.delete(synchronize_session=False)
            db.commit()
        except Exception as exc:
            db.rollback()
            print(json.dumps({"cleanup_warning": repr(exc)}), flush=True)
        finally:
            db.close()

    try:
        from src.constants import MEMORY_FILE
        memory_path = Path(MEMORY_FILE)
        if memory_path.exists():
            entries = json.loads(memory_path.read_text(encoding="utf-8"))
            if isinstance(entries, list):
                filtered = [
                    entry
                    for entry in entries
                    if not (
                        isinstance(entry, dict)
                        and "ODY-EVAL-CRUD-MEMORY-" in str(entry.get("text") or "")
                        and (not owner or entry.get("owner") == owner)
                    )
                ]
                if len(filtered) != len(entries):
                    memory_path.write_text(json.dumps(filtered, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    except Exception as exc:
        print(json.dumps({"cleanup_warning": f"memory cleanup failed: {exc!r}"}), flush=True)


@contextlib.contextmanager
def _crud_fixture_cleanup(enabled: bool):
    if enabled:
        _cleanup_crud_fixtures()
    try:
        yield
    finally:
        if enabled:
            _cleanup_crud_fixtures()


def output_ok(event: dict[str, Any]) -> bool:
    if event.get("exit_code") not in (0, None):
        return False
    text = str(event.get("output") or "")
    return not text.lstrip().lower().startswith("error")


def event_action(event: dict[str, Any]) -> str:
    return parse_action(str(event.get("command") or ""))


def create_session(client: httpx.Client, args, name: str) -> str:
    response = client.post(
        args.base_url.rstrip("/") + "/api/session",
        data={
            "name": name,
            "endpoint_url": args.selected_endpoint_url or args.endpoint,
            "model": args.selected_model or args.model,
            "skip_validation": "true",
            "rag": "false",
            **({"endpoint_id": args.endpoint_id} if args.endpoint_id else {}),
        },
        timeout=30,
    )
    _raise_for_status_with_body(response)
    return response.json()["id"]


def run_turn(client: httpx.Client, args, session_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    events: list[dict[str, Any]] = []
    text: list[str] = []
    errors: list[dict[str, Any]] = []
    approval_turns = 0
    turn_data = {
        "message": spec["message"],
        "session": session_id,
        "mode": "agent",
        "agent_prompt_mode": args.prompt_mode,
        **({"selected_endpoint_id": args.endpoint_id} if args.endpoint_id else {}),
        **({"selected_endpoint_url": args.selected_endpoint_url} if args.selected_endpoint_url else {}),
        **({"selected_model": args.selected_model} if args.selected_model else {}),
    }
    try:
        while True:
            approval = None
            with client.stream(
                "POST",
                args.base_url.rstrip("/") + "/api/chat_stream",
                data=turn_data,
                headers={"Accept": "text/event-stream"},
                timeout=args.timeout,
            ) as response:
                _raise_for_status_with_body(response)
                for event in _sse_events(response):
                    events.append(event)
                    if event.get("type") == "error":
                        errors.append(event)
                    visible = _visible_event_text(event)
                    if visible:
                        if event.get("type") == "final_response":
                            text[:] = [visible]
                        else:
                            text.append(visible)
                    approval = approval or _tool_approval_from_event(event)
            if not args.auto_approve or not approval or approval_turns >= 3:
                break
            approval_turns += 1
            turn_data = {
                **turn_data,
                "tool_approval_id": approval["approval_id"],
                "tool_approval_decision": "approve",
            }
    except Exception as exc:
        errors.append({"type": "client_exception", "error": repr(exc)})

    starts = [event for event in events if event.get("type") == "tool_start"]
    outputs = [event for event in events if event.get("type") == "tool_output"]
    metrics = [
        event.get("data")
        for event in events
        if event.get("type") == "metrics" and isinstance(event.get("data"), dict)
    ]
    snapshots = [
        {
            key: event.get(key)
            for key in (
                "round",
                "model",
                "messages",
                "tools",
                "temperature",
                "max_tokens",
                "agent_prompt_mode",
            )
        }
        for event in events
        if event.get("type") == "model_request_snapshot"
    ]
    metric_tool_events = [
        tool_event
        for metric in metrics
        for tool_event in (metric.get("tool_events") or [])
        if isinstance(tool_event, dict)
    ]
    summarized_tool_events = [
        {
            "tool": canonical_tool(str(event.get("tool") or "")),
            "command": str(event.get("command") or ""),
            "exit_code": event.get("exit_code"),
            "output_preview": str(event.get("output") or "")[:500],
        }
        for event in [*outputs, *metric_tool_events]
        if isinstance(event, dict)
    ]
    observed_events = metric_tool_events or outputs or starts
    first = observed_events[0] if observed_events else {}
    first_tool = canonical_tool(first.get("tool"))
    first_action = parse_action(first.get("command"))
    response_text = "".join(text).strip()
    if not response_text and metrics:
        round_texts = metrics[-1].get("round_texts") or []
        response_text = next((str(item).strip() for item in reversed(round_texts) if str(item).strip()), "")

    expected_tool = spec["expected_tool"]
    expected_action = spec.get("expected_action") or ""
    expected_actions = [str(item) for item in (spec.get("expected_actions") or [])]
    max_tool_count = spec.get("max_tool_count")
    if expected_action and not expected_actions:
        expected_actions = [expected_action]
    if expected_tool == "no_tool":
        tool_ok = not observed_events
        execution_ok = bool(response_text) and not errors
    else:
        tool_ok = first_tool == expected_tool
        executed = [
            event
            for event in [*outputs, *metric_tool_events]
            if canonical_tool(str(event.get("tool") or "")) == expected_tool
            and (not expected_actions or event_action(event) in expected_actions)
            and output_ok(event)
        ]
        execution_ok = bool(executed) and not errors
    action_ok = not expected_actions or first_action in expected_actions
    lower_response = response_text.lower()
    required_any = [str(item).lower() for item in spec.get("required_any") or []]
    required_all = [str(item).lower() for item in spec.get("required_all") or []]
    response_quality_ok = bool(response_text) and (
        not required_any or any(item in lower_response for item in required_any)
    ) and all(item in lower_response for item in required_all)
    if malformed_text_surface(response_text):
        response_quality_ok = False
    tool_efficiency_ok = True
    if isinstance(max_tool_count, int):
        tool_efficiency_ok = len(observed_events) <= max_tool_count

    latest_metrics = metrics[-1] if metrics else {}
    usage_buckets = latest_metrics.get("usage_buckets") if isinstance(latest_metrics, dict) else None
    return {
        "message": spec["message"],
        "expected_tool": expected_tool,
        "expected_action": expected_action,
        "expected_actions": expected_actions,
        "first_tool": first_tool,
        "first_action": first_action,
        "tool_count": len(observed_events),
        "tool_ok": bool(tool_ok),
        "action_ok": bool(action_ok),
        "execution_ok": bool(execution_ok),
        "response_quality_ok": bool(response_quality_ok),
        "tool_efficiency_ok": bool(tool_efficiency_ok),
        "max_tool_count": max_tool_count,
        "stream_errors": errors,
        "response": response_text[:2000],
        "input_tokens": latest_metrics.get("input_tokens"),
        "output_tokens": latest_metrics.get("output_tokens"),
        "tokens_per_second": latest_metrics.get("tokens_per_second"),
        "request_context_tokens": latest_metrics.get("request_context_tokens"),
        "usage_buckets": usage_buckets if isinstance(usage_buckets, list) else [],
        "tool_events": summarized_tool_events,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "approval_turns": approval_turns,
        "model_request_snapshots": snapshots,
    }


def write_output(path: Path, records: list[dict[str, Any]], model: str) -> None:
    turns = [turn for record in records for turn in record["turns"]]
    summary = {
        "model": model,
        "scenarios": len(records),
        "turns": len(turns),
        "tool_success": sum(turn["tool_ok"] for turn in turns),
        "action_success": sum(turn["action_ok"] for turn in turns),
        "execution_success": sum(turn["execution_ok"] for turn in turns),
        "response_quality_success": sum(turn["response_quality_ok"] for turn in turns),
        "tool_efficiency_success": sum(turn.get("tool_efficiency_ok", True) for turn in turns),
        "stream_errors": sum(bool(turn["stream_errors"]) for turn in turns),
        "records": records,
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n")
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:7011")
    parser.add_argument("--endpoint", default="http://host.docker.internal:18052/v1")
    parser.add_argument("--endpoint-id", default="v8c1000")
    parser.add_argument("--model", default="qwen35-9b-tool-router-v15-regular-chat-boundary-final")
    parser.add_argument("--selected-endpoint-url", default="")
    parser.add_argument("--selected-model", default="")
    parser.add_argument("--cookie-file", default="data/sessions.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--prompt-mode", default="compact")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--cases", default="")
    parser.add_argument("--no-auto-approve", dest="auto_approve", action="store_false")
    parser.add_argument("--keep-sessions", action="store_true")
    args = parser.parse_args()

    selected = {item.strip() for item in args.cases.split(",") if item.strip()}
    scenarios = [case for case in SCENARIOS if not selected or case["scenario"] in selected]
    unknown = selected - {case["scenario"] for case in SCENARIOS}
    if unknown:
        raise SystemExit(f"Unknown scenario(s): {', '.join(sorted(unknown))}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(
        cookies={"odysseus_session": _cookie(Path(args.cookie_file))},
        follow_redirects=False,
    )
    records: list[dict[str, Any]] = []
    try:
        needs_crud_cleanup = any(str(case.get("fixture_prefix") or "").startswith("ODY-EVAL-CRUD-") for case in scenarios)
        with _crud_fixture_cleanup(needs_crud_cleanup):
            for scenario in scenarios:
                session_id = create_session(
                    client,
                    args,
                    "[eval-context] "
                    + scenario["scenario"]
                    + " "
                    + time.strftime("%Y%m%d-%H%M%S")
                    + "-"
                    + uuid.uuid4().hex[:6],
                )
                turns = []
                try:
                    for spec in scenario["turns"]:
                        turn = run_turn(client, args, session_id, spec)
                        turns.append(turn)
                        print(
                            json.dumps(
                                {
                                    "scenario": scenario["scenario"],
                                    **{
                                        key: turn.get(key)
                                        for key in (
                                            "message",
                                            "expected_tool",
                                            "first_tool",
                                            "expected_action",
                                            "expected_actions",
                                            "first_action",
                                            "tool_ok",
                                            "action_ok",
                                            "execution_ok",
                                            "response_quality_ok",
                                            "tool_efficiency_ok",
                                            "max_tool_count",
                                            "tool_count",
                                            "input_tokens",
                                            "output_tokens",
                                            "elapsed_seconds",
                                            "stream_errors",
                                        )
                                    },
                                },
                                ensure_ascii=True,
                            ),
                            flush=True,
                        )
                finally:
                    if args.keep_sessions:
                        print(json.dumps({"kept_session": session_id, "scenario": scenario["scenario"]}), flush=True)
                    else:
                        try:
                            client.delete(args.base_url.rstrip("/") + f"/api/session/{session_id}", timeout=15)
                        except Exception:
                            pass
                records.append({"scenario": scenario["scenario"], "turns": turns})
                write_output(output, records, args.selected_model or args.model)
    finally:
        client.close()
    write_output(output, records, args.selected_model or args.model)
    summary = json.loads(output.read_text())
    print("SUMMARY", json.dumps({k: v for k, v in summary.items() if k != "records"}))


if __name__ == "__main__":
    main()
