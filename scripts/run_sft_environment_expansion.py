#!/usr/bin/env python3
"""Execute generated SFT workflows through Odysseus with rollback and gating."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from core.database import (  # noqa: E402
    CalendarCal,
    CalendarEvent,
    Document,
    DocumentVersion,
    Memory,
    Note,
    ScheduledTask,
    SessionLocal,
)
from scripts.eval_odysseus_tool_use import (  # noqa: E402
    _raise_for_status_with_body,
    _sse_events,
    _visible_event_text,
)

from src.constants import DATA_DIR as CONFIGURED_DATA_DIR  # noqa: E402

DATA_DIR = Path(CONFIGURED_DATA_DIR)
BAD_ANSWER_RE = re.compile(
    r"\b(?:can't|cannot|don't have|do not have|not available|no .*tool|enable .*integration|"
    r"invalid credentials|not authenticated|i can only|i'm unable)\b",
    re.I,
)
_COOKIE_CACHE: dict[str, str] = {}
TOOL_FAILURE_RE = re.compile(r"(?:tool (?:failed|error)|exit_code[^\d]*[1-9]|permission denied|not found)", re.I)
INTERNAL_NARRATION_RE = re.compile(
    r"(?:^|\n)(?:The user (?:asks|asked|wants)|I (?:should|need to|can see)|Let me (?:call|use|retry|try))\b",
    re.I,
)


class CaseTimeoutError(TimeoutError):
    pass


def timeout_handler(signum, frame):
    raise CaseTimeoutError("case exceeded wall-clock timeout")


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def install_fixture_environments(path: Path) -> dict[str, int]:
    """Materialize an inventory snapshot for an isolated replay app."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    environments = payload.get("environments", []) if isinstance(payload, dict) else []
    messages: list[dict[str, Any]] = []
    counts = {
        "emails": 0, "notes": 0, "memories": 0, "documents": 0,
        "tasks": 0, "calendars": 0, "events": 0,
    }
    db = SessionLocal()
    owners = [
        str(row.get("owner") or "").strip()
        for row in environments if isinstance(row, dict)
    ]
    try:
        for owner in filter(None, owners):
            document_ids = [
                value[0] for value in db.query(Document.id).filter(Document.owner == owner).all()
            ]
            if document_ids:
                db.query(DocumentVersion).filter(
                    DocumentVersion.document_id.in_(document_ids)
                ).delete(synchronize_session=False)
            calendar_ids = [
                value[0] for value in db.query(CalendarCal.id).filter(CalendarCal.owner == owner).all()
            ]
            if calendar_ids:
                db.query(CalendarEvent).filter(
                    CalendarEvent.calendar_id.in_(calendar_ids)
                ).delete(synchronize_session=False)
            db.query(Document).filter(Document.owner == owner).delete(synchronize_session=False)
            db.query(Note).filter(Note.owner == owner).delete(synchronize_session=False)
            db.query(Memory).filter(Memory.owner == owner).delete(synchronize_session=False)
            db.query(ScheduledTask).filter(ScheduledTask.owner == owner).delete(synchronize_session=False)
            db.query(CalendarCal).filter(CalendarCal.owner == owner).delete(synchronize_session=False)

        for environment in environments:
            if not isinstance(environment, dict):
                continue
            owner = str(environment.get("owner") or "").strip()
            for row in environment.get("notes") or []:
                db.add(Note(
                    id=str(row.get("id") or uuid.uuid4()), owner=owner,
                    title=str(row.get("title") or ""), content=str(row.get("content") or ""),
                    note_type=str(row.get("type") or "note"), label=row.get("label"),
                    archived=False, source="user",
                ))
                counts["notes"] += 1
            for row in environment.get("memories") or []:
                db.add(Memory(
                    id=str(row.get("id") or uuid.uuid4()), owner=owner,
                    text=str(row.get("text") or ""),
                    category=str(row.get("category") or "fact"), source="user",
                ))
                counts["memories"] += 1
            for row in environment.get("documents") or []:
                document_id = str(row.get("id") or uuid.uuid4())
                content = str(row.get("content") or "")
                db.add(Document(
                    id=document_id, owner=owner, title=str(row.get("title") or "Untitled"),
                    language=str(row.get("language") or "text"), current_content=content,
                    version_count=1, is_active=True, archived=False,
                ))
                db.add(DocumentVersion(
                    id=str(uuid.uuid4()), document_id=document_id, version_number=1,
                    content=content, summary="Isolated replay fixture", source="user",
                ))
                counts["documents"] += 1
            for row in environment.get("tasks") or []:
                db.add(ScheduledTask(
                    id=str(row.get("id") or uuid.uuid4()), owner=owner,
                    name=str(row.get("name") or "Untitled Task"),
                    status=str(row.get("status") or "active"),
                    schedule=row.get("schedule"), task_type="llm",
                ))
                counts["tasks"] += 1
            calendar_map: dict[str, str] = {}
            for row in environment.get("calendars") or []:
                calendar_id = str(row.get("id") or uuid.uuid4())
                calendar_map[calendar_id] = calendar_id
                db.add(CalendarCal(
                    id=calendar_id, owner=owner, name=str(row.get("name") or "Personal"),
                    source=str(row.get("source") or "local"),
                ))
                counts["calendars"] += 1
            default_calendar = next(iter(calendar_map), None)
            for row in environment.get("events") or []:
                if default_calendar is None:
                    default_calendar = str(uuid.uuid4())
                    db.add(CalendarCal(
                        id=default_calendar, owner=owner, name="Personal", source="local",
                    ))
                    counts["calendars"] += 1
                start = datetime.fromisoformat(str(row.get("start") or "").replace("Z", "+00:00"))
                db.add(CalendarEvent(
                    uid=str(row.get("uid") or uuid.uuid4()), calendar_id=default_calendar,
                    summary=str(row.get("summary") or ""), dtstart=start,
                    dtend=start + timedelta(hours=1), all_day=bool(row.get("all_day")),
                ))
                counts["events"] += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    for environment in environments:
        if not isinstance(environment, dict):
            continue
        owner = str(environment.get("owner") or "").strip()
        profile = environment.get("profile") if isinstance(environment.get("profile"), dict) else {}
        primary_name = str(profile.get("primary_account") or "Primary Inbox")
        secondary_name = str(profile.get("secondary_account") or "Secondary Inbox")
        for source in environment.get("emails") or []:
            if not isinstance(source, dict):
                continue
            row = dict(source)
            account = str(row.get("account") or primary_name)
            secondary = account == secondary_name
            row.update({
                "owner": owner,
                "account": account,
                "account_email": str(profile.get("secondary" if secondary else "primary") or owner),
                "account_id": "secondary-inbox" if secondary else "primary-inbox",
                "folder": str(row.get("folder") or "INBOX"),
                "body": str(row.get("body") or (
                    f"Fixture message for: {row.get('subject') or '(no subject)'}. "
                    "Please review the referenced materials and reply with the next step."
                )),
            })
            messages.append(row)
    atomic_json(DATA_DIR / "fixture_email_messages.json", {"messages": messages})
    counts["emails"] = len(messages)
    return counts


def login(client: httpx.Client, base_url: str, owner: str, password: str) -> None:
    token = _COOKIE_CACHE.get(owner)
    if not token:
        sessions_path = DATA_DIR / "sessions.json"
        if sessions_path.exists():
            with contextlib.suppress(Exception):
                sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
                token = next(
                    key for key, value in reversed(list(sessions.items()))
                    if isinstance(value, dict) and value.get("username") == owner
                )
    if token:
        _COOKIE_CACHE[owner] = token
        client.cookies.set("odysseus_session", token)
        return
    response = client.post(
        base_url.rstrip("/") + "/api/auth/login",
        json={"username": owner, "password": password, "remember": True},
        timeout=30,
    )
    _raise_for_status_with_body(response)
    if not response.json().get("ok"):
        raise RuntimeError(f"login failed for {owner}")
    token = client.cookies.get("odysseus_session")
    if token:
        _COOKIE_CACHE[owner] = token


def create_session(client: httpx.Client, args: argparse.Namespace, case: dict[str, Any]) -> str:
    response = client.post(
        args.base_url.rstrip("/") + "/api/session",
        data={
            "name": f"SFT expansion {case['case_id']} {case['title']}",
            "endpoint_url": args.endpoint,
            "endpoint_id": args.endpoint_id,
            "model": args.model,
            "skip_validation": "true",
            "rag": "false",
        },
        timeout=30,
    )
    _raise_for_status_with_body(response)
    return str(response.json()["id"])


def stream_turn(
    client: httpx.Client, args: argparse.Namespace, session_id: str, prompt: str,
    *, active_doc_id: str = "",
) -> tuple[list[dict[str, Any]], str]:
    events: list[dict[str, Any]] = []
    text: list[str] = []
    form = {
        "message": prompt,
        "session": session_id,
        "mode": "agent",
        "agent_prompt_mode": "auto",
        "selected_endpoint_id": args.endpoint_id,
        "selected_endpoint_url": args.endpoint,
        "selected_model": args.model,
        "thinking_mode": args.thinking_mode,
        "client_runtime_context": json.dumps(
            {"timezone": args.timezone, "tz_offset_min": args.tz_offset_min}, separators=(",", ":")
        ),
    }
    if active_doc_id:
        form["active_doc_id"] = active_doc_id
    if getattr(args, "allow_web_search", False):
        form["allow_web_search"] = "true"
    with client.stream(
        "POST",
        args.base_url.rstrip("/") + "/api/chat_stream",
        data=form,
        headers={
            "Accept": "text/event-stream",
            "X-Tz-Name": args.timezone,
            "X-Tz-Offset": str(args.tz_offset_min),
        },
        timeout=args.turn_timeout,
    ) as response:
        _raise_for_status_with_body(response)
        for event in _sse_events(response):
            events.append(event)
            if event.get("thinking") is True or event.get("type") in {"thinking", "reasoning"}:
                continue
            visible = _visible_event_text(event)
            if visible:
                if event.get("type") == "final_response":
                    text[:] = [visible]
                else:
                    text.append(visible)
    return events, "".join(text).strip()


def normalized_tool(name: str) -> str:
    value = name.removeprefix("mcp__").split("__")[-1]
    if name.startswith("mcp__builtin_browser__") or value.startswith("browser_"):
        return "private_browser"
    return value


def tool_names(events: list[dict[str, Any]]) -> list[str]:
    names = []
    for event in events:
        if event.get("type") == "tool_start" and event.get("tool"):
            names.append(normalized_tool(str(event["tool"])))
    return names


def tool_outputs(events: list[dict[str, Any]]) -> str:
    return "\n".join(str(e.get("output") or "") for e in events if e.get("type") == "tool_output")


def has_unrecovered_tool_failure(events: list[dict[str, Any]]) -> bool:
    """Count a tool failure only when that tool never subsequently succeeds."""
    pending: set[str] = set()
    for event in events:
        if event.get("type") != "tool_output":
            continue
        name = normalized_tool(str(event.get("tool") or "unknown"))
        output = str(event.get("output") or "")
        failed = bool(
            event.get("error")
            or event.get("exit_code") not in (None, 0)
            or TOOL_FAILURE_RE.search(output)
        )
        if failed:
            pending.add(name)
        else:
            pending.discard(name)
    return bool(pending)


def compact_evidence(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep routing and execution evidence without bloating the replay report."""
    retained = {
        "turn_contract", "tool_start", "tool_output", "tool_resolution_audit",
        "error", "parse_error", "metrics", "final_response",
    }
    rows = []
    for event in events:
        if event.get("type") not in retained:
            continue
        row = dict(event)
        for key in ("output", "text", "delta"):
            if isinstance(row.get(key), str) and len(row[key]) > 3000:
                row[key] = row[key][:3000] + "..."
        if row.get("type") == "turn_contract":
            row.pop("executable", None)
        rows.append(row)
    return rows


def tool_actions(events: list[dict[str, Any]], tool_name: str) -> set[str]:
    actions: set[str] = set()
    for event in events:
        if event.get("type") != "tool_start" or normalized_tool(str(event.get("tool") or "")) != tool_name:
            continue
        command = str(event.get("full_command") or event.get("command") or "").strip()
        try:
            parsed = json.loads(command)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        action = (
            str(parsed.get("action") or "").strip().lower()
            if isinstance(parsed, dict)
            else command.splitlines()[0].strip().lower().split(maxsplit=1)[0]
        )
        if action:
            actions.add(action)
    return actions


def inferred_expected_actions(turn: dict[str, Any]) -> dict[str, set[str]]:
    explicit = turn.get("expected_actions") or {}
    if isinstance(explicit, dict) and explicit:
        return {
            normalized_tool(str(tool)): {str(action).lower() for action in actions}
            for tool, actions in explicit.items()
            if isinstance(actions, list)
        }
    prompt = str(turn.get("prompt") or "").lower()
    if "manage_calendar" not in set(turn.get("expected_tools") or []):
        return {}
    if re.search(r"\b(?:add|create|schedule|book|set up)\b", prompt):
        return {"manage_calendar": {"create", "create_event", "add", "add_event"}}
    if re.search(r"\b(?:delete|remove|cancel|get rid of)\b", prompt):
        return {"manage_calendar": {"delete", "delete_event", "remove", "remove_event", "cancel"}}
    if re.search(r"\b(?:move|shift|reschedule|change|update|edit|rename|tag|retag)\b", prompt):
        return {"manage_calendar": {"update", "update_event", "move", "reschedule", "edit_event"}}
    if re.search(r"\b(?:show|list|check|find|what|when|confirm|verify|pull up)\b", prompt):
        return {"manage_calendar": {"list", "list_events", "search", "find", "view"}}
    return {}


def score_turn(turn: dict[str, Any], events: list[dict[str, Any]], answer: str) -> list[str]:
    failures: list[str] = []
    names = tool_names(events)
    expected = {normalized_tool(str(name)) for name in turn.get("expected_tools") or []}
    # Both document writers satisfy a requested active-draft mutation. Which
    # one is most efficient depends on how much of the draft the model changes;
    # exact-name imitation is not a functional correctness requirement.
    if expected & {"edit_document", "update_document"}:
        expected.update({"edit_document", "update_document"})
    if expected and not expected.intersection(names):
        failures.append(f"missing_acceptable_tool expected={sorted(expected)} got={names}")
    for tool_name, expected_actions in inferred_expected_actions(turn).items():
        observed_actions = tool_actions(events, tool_name)
        if expected_actions and not expected_actions.intersection(observed_actions):
            failures.append(
                f"missing_tool_action tool={tool_name} expected={sorted(expected_actions)} "
                f"got={sorted(observed_actions)}"
            )
    if any(e.get("type") in {"error", "parse_error"} for e in events):
        failures.append("stream_error")
    if BAD_ANSWER_RE.search(answer):
        failures.append("tool_unavailable_answer")
    if has_unrecovered_tool_failure(events):
        failures.append("tool_output_failure")
    if INTERNAL_NARRATION_RE.search(answer):
        failures.append("internal_narration_leaked")
    if not answer.strip() and "ask_user" not in names:
        failures.append("empty_final_answer")
    return failures


def row_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class OwnerSnapshot:
    MODELS = (Note, Memory, ScheduledTask, Document)

    def __init__(self, owner: str, tools: set[str], marker: str):
        self.owner = owner
        self.tools = tools
        self.marker = marker.lower()
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.prefs: Any = None
        self.email_rows: list[dict[str, Any]] | None = None
        self.blocked_senders: Any = None

    def capture(self) -> None:
        db = SessionLocal()
        try:
            selected = []
            has_email_tools = any(tool.startswith("mcp__email__") for tool in self.tools)
            if "manage_notes" in self.tools:
                selected.append(Note)
            if "manage_memory" in self.tools:
                selected.append(Memory)
            if "manage_tasks" in self.tools:
                selected.append(ScheduledTask)
            if has_email_tools or {
                "manage_documents", "create_document", "edit_document", "update_document", "suggest_document"
            } & self.tools:
                selected.append(Document)
            for model in selected:
                values = db.query(model).filter(model.owner == self.owner).all()
                self.rows[model.__tablename__] = [row_dict(row) for row in values]
            document_ids = [row["id"] for row in self.rows.get(Document.__tablename__, [])]
            versions = db.query(DocumentVersion).filter(DocumentVersion.document_id.in_(document_ids)).all() if document_ids else []
            self.rows[DocumentVersion.__tablename__] = [row_dict(row) for row in versions]
            calendars = db.query(CalendarCal).filter(CalendarCal.owner == self.owner).all() if "manage_calendar" in self.tools else []
            self.rows[CalendarCal.__tablename__] = [row_dict(row) for row in calendars]
            calendar_ids = [row.id for row in calendars]
            events = db.query(CalendarEvent).filter(CalendarEvent.calendar_id.in_(calendar_ids)).all() if calendar_ids else []
            self.rows[CalendarEvent.__tablename__] = [row_dict(row) for row in events]
        finally:
            db.close()
        prefs_path = DATA_DIR / "user_prefs.json"
        prefs = json.loads(prefs_path.read_text(encoding="utf-8")) if prefs_path.exists() else {"_users": {}}
        if "ui_control" in self.tools:
            self.prefs = (prefs.get("_users") or {}).get(self.owner, None)
        if any(tool.startswith("mcp__email__") for tool in self.tools):
            email_path = DATA_DIR / "fixture_email_messages.json"
            if email_path.exists():
                payload = json.loads(email_path.read_text(encoding="utf-8"))
                values = payload.get("messages") if isinstance(payload, dict) else payload
                self.email_rows = [
                    row for row in (values if isinstance(values, list) else [])
                    if isinstance(row, dict) and str(row.get("owner") or "") == self.owner
                ]
            blocked_path = DATA_DIR / "email_blocked_senders.json"
            if blocked_path.exists():
                blocked = json.loads(blocked_path.read_text(encoding="utf-8"))
                self.blocked_senders = (blocked.get("owners") or {}).get(self.owner)

    def restore(self) -> None:
        db = SessionLocal()
        try:
            if Document.__tablename__ in self.rows:
                document_ids = [value[0] for value in db.query(Document.id).filter(Document.owner == self.owner).all()]
                if document_ids:
                    db.query(DocumentVersion).filter(DocumentVersion.document_id.in_(document_ids)).delete(synchronize_session=False)
                db.query(Document).filter(Document.owner == self.owner).delete(synchronize_session=False)
            if Note.__tablename__ in self.rows:
                db.query(Note).filter(Note.owner == self.owner).delete(synchronize_session=False)
            if Memory.__tablename__ in self.rows:
                db.query(Memory).filter(Memory.owner == self.owner).delete(synchronize_session=False)
            if ScheduledTask.__tablename__ in self.rows:
                db.query(ScheduledTask).filter(ScheduledTask.owner == self.owner).delete(synchronize_session=False)
            if CalendarCal.__tablename__ in self.rows:
                calendar_ids = [value[0] for value in db.query(CalendarCal.id).filter(CalendarCal.owner == self.owner).all()]
                if calendar_ids:
                    db.query(CalendarEvent).filter(CalendarEvent.calendar_id.in_(calendar_ids)).delete(synchronize_session=False)
                db.query(CalendarCal).filter(CalendarCal.owner == self.owner).delete(synchronize_session=False)
            db.flush()
            for model in (Note, Memory, ScheduledTask, Document, DocumentVersion, CalendarCal, CalendarEvent):
                for values in self.rows.get(model.__tablename__, []):
                    db.add(model(**values))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        if "manage_skills" in self.tools:
            skills = DATA_DIR / "skills"
            if skills.exists():
                for path in sorted(skills.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                    if self.marker not in path.name.lower():
                        continue
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
                usage_path = skills / "_usage.json"
                if usage_path.exists():
                    usage = json.loads(usage_path.read_text(encoding="utf-8"))
                    if isinstance(usage, dict):
                        usage = {
                            key: value for key, value in usage.items()
                            if self.marker not in str(key).lower()
                        }
                        atomic_json(usage_path, usage)
        if "ui_control" in self.tools:
            prefs_path = DATA_DIR / "user_prefs.json"
            prefs = json.loads(prefs_path.read_text(encoding="utf-8")) if prefs_path.exists() else {"_users": {}}
            users = prefs.setdefault("_users", {})
            if self.prefs is None:
                users.pop(self.owner, None)
            else:
                users[self.owner] = self.prefs
            atomic_json(prefs_path, prefs)
        if self.email_rows is not None:
            email_path = DATA_DIR / "fixture_email_messages.json"
            payload = json.loads(email_path.read_text(encoding="utf-8")) if email_path.exists() else {"messages": []}
            values = payload.get("messages") if isinstance(payload, dict) else payload
            other_rows = [
                row for row in (values if isinstance(values, list) else [])
                if not (isinstance(row, dict) and str(row.get("owner") or "") == self.owner)
            ]
            if isinstance(payload, dict):
                payload["messages"] = other_rows + self.email_rows
            else:
                payload = other_rows + self.email_rows
            atomic_json(email_path, payload)
            blocked_path = DATA_DIR / "email_blocked_senders.json"
            blocked = json.loads(blocked_path.read_text(encoding="utf-8")) if blocked_path.exists() else {"owners": {}}
            owners = blocked.setdefault("owners", {})
            if self.blocked_senders is None:
                owners.pop(self.owner, None)
            else:
                owners[self.owner] = self.blocked_senders
            atomic_json(blocked_path, blocked)


def marker_fields(value: Any, marker: str) -> Any:
    if isinstance(value, str):
        return value.replace("{marker}", marker)
    if isinstance(value, list):
        return [marker_fields(item, marker) for item in value]
    if isinstance(value, dict):
        return {key: marker_fields(item, marker) for key, item in value.items()}
    return value


def apply_fixture_plan(case: dict[str, Any], owner: str, session_id: str, marker: str) -> dict[str, str]:
    """Create only owner-scoped local fixtures required before the first turn."""
    first_tools = set((case.get("turns") or [{}])[0].get("expected_tools") or [])
    db = SessionLocal()
    context: dict[str, str] = {}
    try:
        for fixture in case.get("fixture_plan") or []:
            if not isinstance(fixture, dict):
                continue
            fixture_type = str(fixture.get("type") or "")
            fields = marker_fields(fixture.get("fields") or {}, marker)
            if fixture_type == "document" and "create_document" not in first_tools:
                document_id = str(uuid.uuid4())
                content = str(fields.get("content") or "")
                db.add(Document(
                    id=document_id,
                    session_id=session_id,
                    owner=owner,
                    title=str(fields.get("title") or "Untitled"),
                    language=str(fields.get("language") or "text"),
                    current_content=content,
                    version_count=1,
                    is_active=True,
                    archived=False,
                ))
                db.add(DocumentVersion(
                    id=str(uuid.uuid4()),
                    document_id=document_id,
                    version_number=1,
                    content=content,
                    summary="Expansion fixture",
                    source="user",
                ))
                context["active_doc_id"] = document_id
            elif fixture_type == "note":
                db.add(Note(
                    id=str(uuid.uuid4()),
                    owner=owner,
                    title=str(fields.get("title") or ""),
                    content=str(fields.get("content") or ""),
                    items=json.dumps(fields.get("items"), ensure_ascii=False) if fields.get("items") is not None else None,
                    note_type=str(fields.get("note_type") or "note"),
                    label=fields.get("label"),
                    pinned=bool(fields.get("pinned", False)),
                    source="user",
                    session_id=session_id,
                ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return context


def delete_session(client: httpx.Client, base_url: str, session_id: str) -> None:
    with contextlib.suppress(Exception):
        client.delete(base_url.rstrip("/") + f"/api/session/{session_id}", timeout=30)


def annotate_trace(owner: str, session_id: str, case: dict[str, Any], marker: str) -> int:
    path = DATA_DIR / "sft_traces" / f"{owner}.jsonl"
    if not path.exists():
        return 0
    changed = 0
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        if str(row.get("session_id") or "") == session_id:
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                with contextlib.suppress(json.JSONDecodeError):
                    metadata = json.loads(metadata)
            if not isinstance(metadata, dict):
                metadata = {}
            metadata.update({
                "expansion_case_id": case["case_id"],
                "seed_family_id": case["seed_family_id"],
                "source_session_id": case["source_session_id"],
                "dataset_split": case["split"],
                "target_owner": owner,
                "fixture_marker": marker,
            })
            row["metadata"] = metadata
            changed += 1
        lines.append(json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return changed


def run_case(args: argparse.Namespace, case: dict[str, Any]) -> dict[str, Any]:
    owner = case["owner"]
    marker = f"EXP-{case['case_id']}-{uuid.uuid4().hex[:6]}"
    session_id = ""
    turns_out = []
    failures: list[str] = []
    started = time.time()
    case_tools = {tool for turn in case["turns"] for tool in turn.get("expected_tools") or []}
    snapshot = OwnerSnapshot(owner, case_tools, marker)
    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, max(1, args.case_timeout))
    client = httpx.Client(follow_redirects=False)
    try:
        snapshot.capture()
        login(client, args.base_url, owner, args.password)
        session_id = create_session(client, args, case)
        fixture_context = apply_fixture_plan(case, owner, session_id, marker)
        upstream_failed = False
        for turn in case["turns"]:
            prompt = str(turn["prompt"]).replace("{marker}", marker)
            events, answer = stream_turn(
                client, args, session_id, prompt,
                active_doc_id=fixture_context.get("active_doc_id", ""),
            )
            turn_failures = score_turn(turn, events, answer)
            turns_out.append({
                "id": turn["id"],
                "prompt": prompt,
                "expected_tools": turn["expected_tools"],
                "observed_tools": tool_names(events),
                "answer": answer,
                "failures": turn_failures,
                "evidence": compact_evidence(events),
                "upstream_failed": upstream_failed,
            })
            failures.extend(f"{turn['id']}:{failure}" for failure in turn_failures)
            # Keep executing the full 3-4 turn trajectory. Later misses may be
            # causal fallout from an earlier failed create/read, so the judge
            # receives this marker and can separate root causes from cascades.
            upstream_failed = upstream_failed or bool(turn_failures)
    except Exception as exc:
        failures.append(f"exception:{exc!r}")
    finally:
        with contextlib.suppress(Exception):
            snapshot.restore()
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        client.close()
    passed = not failures and len(turns_out) == len(case["turns"])
    with httpx.Client(follow_redirects=False) as cleanup_client:
        with contextlib.suppress(Exception):
            login(cleanup_client, args.base_url, owner, args.password)
        if passed:
            annotated = annotate_trace(owner, session_id, case, marker)
            if annotated != len(case["turns"]):
                failures.append(f"trace_turn_count expected={len(case['turns'])} got={annotated}")
                passed = False
        if not passed and session_id:
            delete_session(cleanup_client, args.base_url, session_id)
    return {
        "case_id": case["case_id"],
        "seed_family_id": case["seed_family_id"],
        "owner": owner,
        "session_id": session_id,
        "pass": passed,
        "failures": failures,
        "turns": turns_out,
        "elapsed_seconds": round(time.time() - started, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:7011")
    parser.add_argument("--password", default=os.environ.get("ODYSSEUS_QA_PASSWORD"), required=os.environ.get("ODYSSEUS_QA_PASSWORD") is None)
    parser.add_argument("--endpoint-id", default="f3904562")
    parser.add_argument("--endpoint", default="https://openrouter.ai/api/v1/chat/completions")
    parser.add_argument("--model", default="moonshotai/kimi-k3")
    parser.add_argument("--thinking-mode", choices=("on", "off"), default="off")
    parser.add_argument(
        "--allow-web-search",
        action="store_true",
        help="Enable Odysseus public web_search/web_fetch for this replay.",
    )
    parser.add_argument(
        "--fixture-environments",
        type=Path,
        help=(
            "Install owner-scoped synthetic inventory rows for replay. "
            "Use only against an isolated app with ODYSSEUS_EMAIL_FIXTURE=1."
        ),
    )
    parser.add_argument("--turn-timeout", type=float, default=180)
    parser.add_argument("--case-timeout", type=float, default=600)
    parser.add_argument("--timezone", default="Asia/Tokyo")
    parser.add_argument("--tz-offset-min", type=int, default=-540)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--owner", action="append")
    parser.add_argument("--case-id", action="append")
    parser.add_argument(
        "--one-per-seed",
        action="store_true",
        help="Run the first validated environment variant for each source seed family",
    )
    args = parser.parse_args()

    if args.fixture_environments:
        if os.environ.get("ODYSSEUS_EMAIL_FIXTURE") != "1":
            parser.error("--fixture-environments requires ODYSSEUS_EMAIL_FIXTURE=1")
        installed = install_fixture_environments(args.fixture_environments)
        print(f"installed isolated fixture inventory: {installed}", flush=True)

    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    if args.owner:
        cases = [case for case in cases if case["owner"] in set(args.owner)]
    if args.case_id:
        cases = [case for case in cases if case["case_id"] in set(args.case_id)]
    if args.one_per_seed:
        seen_seeds: set[str] = set()
        first_cases = []
        for case in cases:
            seed_id = str(case.get("seed_family_id") or "")
            if seed_id in seen_seeds:
                continue
            seen_seeds.add(seed_id)
            first_cases.append(case)
        cases = first_cases
    if args.limit:
        cases = cases[: args.limit]
    existing = {row["case_id"]: row for row in json.loads(args.out.read_text(encoding="utf-8")).get("results", [])} if args.out.exists() else {}
    for index, case in enumerate(cases, 1):
        if existing.get(case["case_id"], {}).get("pass") is True:
            print(f"skip {case['case_id']} already passed", flush=True)
            continue
        print(f"[{index}/{len(cases)}] {case['owner']} {case['title']}", flush=True)
        result = run_case(args, case)
        existing[case["case_id"]] = result
        atomic_json(args.out, {"results": list(existing.values())})
        print(f"  pass={result['pass']} failures={result['failures']} elapsed={result['elapsed_seconds']}s", flush=True)
    results = list(existing.values())
    print(json.dumps({
        "cases": len(results),
        "passed": sum(row.get("pass") is True for row in results),
        "failed": sum(row.get("pass") is not True for row in results),
    }, indent=2))


if __name__ == "__main__":
    main()
