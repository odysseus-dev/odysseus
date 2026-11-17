#!/usr/bin/env python3
from __future__ import annotations
import os

import argparse
import contextlib
import json
import re
import signal
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from core.database import CalendarCal, CalendarEvent, Note, SessionLocal
from scripts.curate_sft_trace_run import curate_rows, load_trace_rows, write_jsonl
from scripts.eval_odysseus_live_hard_examples import _parse_tool_args
from scripts.eval_odysseus_tool_use import _raise_for_status_with_body, _sse_events, _visible_event_text


DATA_DIR = ROOT / "data"
DEFAULT_BASE_URL = "http://127.0.0.1:7011"
DEFAULT_OWNER = "sft_maya_ops"
DEFAULT_PASSWORD = os.environ["ODYSSEUS_QA_PASSWORD"]
DEFAULT_ENDPOINT_ID = "f3904562"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "moonshotai/kimi-k3"
BAD_ANSWER_RE = re.compile(
    r"\b(?:can't|cannot|don't have|do not have|not available|no .*tool|enable .*integration|setup .*integration|"
    r"invalid credentials|not authenticated|i can only|i'm unable)\b",
    re.IGNORECASE,
)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


class CaseTimeoutError(TimeoutError):
    pass


def _case_timeout_handler(signum, frame):
    raise CaseTimeoutError("case exceeded wall-clock timeout")


def login(client: httpx.Client, base_url: str, username: str, password: str) -> None:
    res = client.post(
        base_url.rstrip() + "/api/auth/login",
        json={"username": username, "password": password, "remember": True},
        timeout=30,
    )
    _raise_for_status_with_body(res)
    if not res.json().get("ok"):
        raise RuntimeError(f"login failed for {username}: {res.text[:300]}")


def ensure_calendar(owner: str) -> CalendarCal:
    db = SessionLocal()
    try:
        cal = db.query(CalendarCal).filter(CalendarCal.owner == owner).first()
        if cal:
            return cal
        cal = CalendarCal(
            id=f"sft-overnight-cal-{uuid.uuid4().hex[:8]}",
            owner=owner,
            name="SFT Overnight",
            source="local",
        )
        db.add(cal)
        db.commit()
        db.refresh(cal)
        return cal
    finally:
        db.close()


def seed_case(owner: str, case: dict[str, Any]) -> dict[str, Any]:
    seeded: dict[str, Any] = {"note_id": "", "event_uid": ""}
    db = SessionLocal()
    try:
        marker = case.get("marker") or ""
        if marker:
            # A failed/interrupted retry can leave a previously seeded fixture
            # row behind. Remove stale rows before creating this case's fresh
            # target so title-based update/delete prompts remain unambiguous.
            stale_notes = db.query(Note).filter(
                Note.owner == owner,
                (Note.title.contains(marker)) | (Note.content.contains(marker)),
            ).all()
            for note in stale_notes:
                db.delete(note)
            stale_events = db.query(CalendarEvent).join(
                CalendarCal, CalendarEvent.calendar_id == CalendarCal.id
            ).filter(
                CalendarCal.owner == owner,
                (CalendarEvent.summary.contains(marker)) | (CalendarEvent.description.contains(marker)),
            ).all()
            for event in stale_events:
                db.delete(event)
            if stale_notes or stale_events:
                db.commit()
        if case.get("seed_note"):
            note = Note(
                id=f"sft-overnight-note-{uuid.uuid4().hex[:10]}",
                owner=owner,
                title=case["seed_note"]["title"],
                content=case["seed_note"]["content"],
                note_type="text",
                archived=False,
                source="sft_overnight",
            )
            db.add(note)
            db.commit()
            seeded["note_id"] = note.id
        if case.get("seed_event"):
            cal = db.query(CalendarCal).filter(CalendarCal.owner == owner).first()
            if not cal:
                cal = CalendarCal(
                    id=f"sft-overnight-cal-{uuid.uuid4().hex[:8]}",
                    owner=owner,
                    name="SFT Overnight",
                    source="local",
                )
                db.add(cal)
                db.commit()
                db.refresh(cal)
            start = datetime.fromisoformat(case["seed_event"]["dtstart"])
            end = datetime.fromisoformat(case["seed_event"]["dtend"])
            event = CalendarEvent(
                uid=f"sft-overnight-event-{uuid.uuid4().hex[:10]}",
                calendar_id=cal.id,
                summary=case["seed_event"]["summary"],
                description=marker,
                dtstart=start,
                dtend=end,
                all_day=False,
                is_utc=False,
                origin="local",
                status="confirmed",
            )
            db.add(event)
            db.commit()
            seeded["event_uid"] = event.uid
    finally:
        db.close()
    return seeded


def collect_state_and_cleanup(owner: str, case: dict[str, Any], seeded: dict[str, Any]) -> dict[str, Any]:
    marker = case.get("marker") or ""
    state: dict[str, Any] = {"note_found": False, "note_content": "", "events": []}
    if not marker and not seeded.get("note_id") and not seeded.get("event_uid"):
        return state
    db = SessionLocal()
    try:
        note_q = db.query(Note).filter(Note.owner == owner)
        if seeded.get("note_id"):
            note_q = note_q.filter(Note.id == seeded["note_id"])
        elif marker:
            note_q = note_q.filter((Note.title.contains(marker)) | (Note.content.contains(marker)))
        notes = note_q.all()
        state["note_found"] = any(not bool(n.archived) for n in notes)
        state["note_content"] = "\n".join((n.content or "") for n in notes)
        event_q = db.query(CalendarEvent).join(CalendarCal, CalendarEvent.calendar_id == CalendarCal.id).filter(CalendarCal.owner == owner)
        if seeded.get("event_uid"):
            event_q = event_q.filter(CalendarEvent.uid == seeded["event_uid"])
        elif marker:
            event_q = event_q.filter((CalendarEvent.summary.contains(marker)) | (CalendarEvent.description.contains(marker)))
        events = event_q.all()
        state["events"] = [
            {
                "uid": e.uid,
                "summary": e.summary,
                "dtstart": e.dtstart.isoformat() if e.dtstart else "",
                "status": e.status,
            }
            for e in events
            if (e.status or "").lower() != "cancelled"
        ]
        for note in notes:
            db.delete(note)
        for event in events:
            db.delete(event)
        db.commit()
    finally:
        db.close()
    return state


def create_session(client: httpx.Client, args: argparse.Namespace, case: dict[str, Any]) -> str:
    name = f"SFT trace batch {args.owner} {case['domain']} {case['index']:03d}"
    res = client.post(
        args.base_url.rstrip("/") + "/api/session",
        data={
            "name": name,
            "endpoint_url": args.endpoint,
            "endpoint_id": args.endpoint_id,
            "model": args.model,
            "skip_validation": "true",
            "rag": "false",
        },
        timeout=30,
    )
    _raise_for_status_with_body(res)
    return res.json()["id"]


def stream_turn(client: httpx.Client, args: argparse.Namespace, session_id: str, message: str) -> tuple[list[dict[str, Any]], str]:
    events: list[dict[str, Any]] = []
    text_parts: list[str] = []
    form = {
        "message": message,
        "session": session_id,
        "mode": "agent",
        "agent_prompt_mode": "auto",
        "selected_endpoint_id": args.endpoint_id,
        "selected_endpoint_url": args.endpoint,
        "selected_model": args.model,
        "client_runtime_context": json.dumps({"timezone": "UTC", "tz_offset_min": 0}, separators=(",", ":")),
    }
    with client.stream(
        "POST",
        args.base_url.rstrip("/") + "/api/chat_stream",
        data=form,
        headers={"Accept": "text/event-stream", "X-Tz-Name": "UTC", "X-Tz-Offset": "0"},
        timeout=args.timeout,
    ) as response:
        _raise_for_status_with_body(response)
        for event in _sse_events(response):
            events.append(event)
            visible = _visible_event_text(event)
            if visible:
                if event.get("type") == "final_response":
                    text_parts[:] = [visible]
                else:
                    text_parts.append(visible)
    return events, "".join(text_parts).strip()


def tool_names(events: list[dict[str, Any]]) -> list[str]:
    return [str(e.get("tool") or "") for e in events if e.get("type") == "tool_start"]


def tool_outputs(events: list[dict[str, Any]]) -> str:
    parts = []
    for event in events:
        if event.get("type") == "tool_output":
            parts.append(str(event.get("output") or ""))
    return "\n".join(parts)


def score(case: dict[str, Any], events: list[dict[str, Any]], answer: str, state: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    names = tool_names(events)
    combined = (answer + "\n" + tool_outputs(events)).lower()
    if any(e.get("type") in {"error", "parse_error"} for e in events):
        failures.append("stream_error")
    if BAD_ANSWER_RE.search(answer or ""):
        failures.append("bad_unavailable_answer")
    expected = case.get("expected_tools") or []
    if expected and not any(name in expected for name in names):
        failures.append(f"missing_expected_tool expected={expected} got={names}")
    for forbidden in case.get("forbidden_tools") or []:
        if forbidden in names:
            failures.append(f"forbidden_tool {forbidden}")
    if case["id"].startswith("email_draft_reply_") and names.count("ui_control") > 1:
        failures.append("duplicate_reply_draft_ui_control")
    for needle in case.get("must_contain_any") or []:
        if needle.lower() in combined:
            break
    else:
        if case.get("must_contain_any"):
            failures.append(f"missing_answer_content {case['must_contain_any']}")
    mutation = case.get("mutation")
    if mutation == "note_created" and not state.get("note_found"):
        failures.append("note_not_created")
    if mutation == "note_updated" and case.get("updated_text", "").lower() not in str(state.get("note_content") or "").lower():
        failures.append("note_not_updated")
    if mutation == "note_deleted" and state.get("note_found"):
        failures.append("note_not_deleted")
    if mutation == "calendar_created" and not state.get("events"):
        failures.append("calendar_event_not_created")
    if mutation == "calendar_updated":
        expected = str(case.get("updated_text") or "").lower()
        if not any(expected in str(e.get("summary") or "").lower() or "12:30" in str(e.get("dtstart") or "") for e in state.get("events") or []):
            failures.append("calendar_event_not_updated")
    if mutation == "calendar_deleted" and state.get("events"):
        failures.append("calendar_event_not_deleted")
    return not failures, failures


def quarantine_sft_rows(owner: str, session_id: str, reason: str) -> int:
    path = DATA_DIR / "sft_traces" / f"{owner}.jsonl"
    if not path.exists():
        return 0
    kept: list[str] = []
    removed: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if row.get("session_id") == session_id:
            row["deleted_from_training"] = True
            row["delete_reason"] = reason
            removed.append(json.dumps(row, ensure_ascii=False))
        else:
            kept.append(line)
    if not removed:
        return 0
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    trash = path.with_suffix(path.suffix + ".trash")
    with trash.open("a", encoding="utf-8") as f:
        for raw in removed:
            f.write(raw + "\n")
    return len(removed)


def delete_session(client: httpx.Client, base_url: str, session_id: str) -> None:
    with contextlib.suppress(Exception):
        client.delete(base_url.rstrip("/") + f"/api/session/{session_id}", timeout=20)


OWNER_PROFILES = {
    "sft_maya_ops": {
        "marker": "MAYA",
        "first_name": "Maya",
        "email_topic": "creator operations",
        "notes": [
            ("Renewal Questions", "LedgerFlow"),
            ("Customer success summary", "export gap"),
            ("Reply Queue", "newest emails"),
            ("Weekly Digest Inputs", "calendar"),
        ],
        "events": [
            ("LedgerFlow renewal meeting", "LedgerFlow"),
            ("Billing export postmortem", "Billing"),
            ("Atlas Rooms pilot decision", "Atlas"),
            ("Inbox triage", "Inbox"),
        ],
    },
    "sft_jules_research": {
        "marker": "JULES",
        "first_name": "Jules",
        "email_topic": "research synthesis",
        "notes": [
            ("Ablation Runs", "reranker depth"),
            ("Appendix cleanup", "private source"),
            ("Reply Queue", "newest emails"),
            ("Weekly Digest Inputs", "calendar"),
        ],
        "events": [
            ("Retrieval eval readout", "Retrieval"),
            ("License review with Rowan", "License"),
            ("Reranker ablation window", "Reranker"),
            ("Inbox triage", "Inbox"),
        ],
    },
    "sft_nora_design": {
        "marker": "NORA",
        "first_name": "Nora",
        "email_topic": "product design",
        "notes": [
            ("Prototype Followups", "empty state"),
            ("Settings cleanup", "destructive action"),
            ("Reply Queue", "newest emails"),
            ("Weekly Digest Inputs", "calendar"),
        ],
        "events": [
            ("Onboarding critique review", "Onboarding"),
            ("Usability synthesis", "Usability"),
            ("Settings component audit", "Settings"),
            ("Inbox triage", "Inbox"),
        ],
    },
    "sft_omar_finance": {
        "marker": "OMAR",
        "first_name": "Omar",
        "email_topic": "finance planning",
        "notes": [
            ("Leadership Pack", "stress"),
            ("Contractor list", "extensions"),
            ("Reply Queue", "newest emails"),
            ("Weekly Digest Inputs", "calendar"),
        ],
        "events": [
            ("Leadership budget review", "Leadership"),
            ("Infra spend follow-up", "Infra"),
            ("Forecast lock", "Forecast"),
            ("Inbox triage", "Inbox"),
        ],
    },
}


def owner_profile(owner: str) -> dict[str, Any]:
    return OWNER_PROFILES.get(owner, OWNER_PROFILES["sft_maya_ops"])


def marker(owner: str, domain: str, index: int) -> str:
    label = str(owner_profile(owner).get("marker") or "SFT").upper()
    return f"OVN-{label}-{domain.upper()}-{index:03d}"


def build_email_case(i: int, owner: str = DEFAULT_OWNER) -> dict[str, Any]:
    profile = owner_profile(owner)
    email_topic = str(profile.get("email_topic") or "work")
    senders = [
        ("Casey Morgan", "latest materials"),
        ("Priya Shah", "Monday agenda"),
        ("Marco Wells", "draft"),
        ("Iris Bell", "decision deadline"),
        ("Sam Rivera", "sanity-check"),
    ]
    sender, needle = senders[i % len(senders)]
    variants = [
        ("list", "show my latest 3 emails", ["mcp__email__list_emails", "list_emails"], ["Casey", "Priya", "UID"]),
        ("today", "what emails did I receive today?", ["mcp__email__list_emails", "list_emails"], [email_topic, "UID"]),
        ("read_sender", f"open the email from {sender} and tell me what they need", ["mcp__email__read_email", "read_email"], [needle]),
        ("search", f"find the email about {needle} and summarize it", ["mcp__email__search_emails", "search_emails", "mcp__email__list_emails"], [needle]),
        (
            "draft_reply",
            f"draft a polite reply to {sender} saying thanks, I'll take care of it. No signature needed.",
            ["ui_control"],
            ["draft", "thanks"],
        ),
    ]
    kind, user, tools, content = variants[i % len(variants)]
    return {
        "id": f"email_{kind}_{i:03d}",
        "domain": "email",
        "index": i,
        "user": user,
        "expected_tools": tools,
        "forbidden_tools": ["web_search", "manage_memory"],
        "must_contain_any": content,
    }


def build_note_case(i: int, owner: str = DEFAULT_OWNER) -> dict[str, Any]:
    profile = owner_profile(owner)
    existing = list(profile["notes"])
    title, needle = existing[i % len(existing)]
    mark = marker(owner, "note", i)
    variant = i % 5
    base = {
        "id": f"notes_{i:03d}",
        "domain": "notes",
        "index": i,
        "expected_tools": ["manage_notes"],
        "forbidden_tools": ["web_search"],
    }
    if variant == 0:
        return {**base, "user": "show my notes", "must_contain_any": [existing[0][0], "Reply Queue"]}
    if variant == 1:
        return {**base, "user": f"find my note titled {title} and summarize it", "must_contain_any": [needle]}
    if variant == 2:
        return {**base, "user": f"create a note titled {mark} with content remember to check the ops dashboard", "marker": mark, "mutation": "note_created"}
    if variant == 3:
        updated = f"{mark} updated follow-up owner is {profile.get('first_name') or 'the owner'}"
        return {
            **base,
            "user": f"update the note titled {mark} to say {updated}",
            "marker": mark,
            "seed_note": {"title": mark, "content": f"{mark} initial"},
            "mutation": "note_updated",
            "updated_text": updated,
        }
    return {
        **base,
        "user": f"delete the note titled {mark}",
        "marker": mark,
        "seed_note": {"title": mark, "content": f"{mark} temporary"},
        "mutation": "note_deleted",
    }


def build_calendar_case(i: int, owner: str = DEFAULT_OWNER) -> dict[str, Any]:
    existing = list(owner_profile(owner)["events"])
    summary, needle = existing[i % len(existing)]
    mark = marker(owner, "calendar", i)
    day = datetime(2026, 8, 24, 10, 0) + timedelta(days=i % 10)
    variant = i % 5
    base = {
        "id": f"calendar_{i:03d}",
        "domain": "calendar",
        "index": i,
        "expected_tools": ["manage_calendar"],
        "forbidden_tools": ["web_search"],
    }
    if variant == 0:
        return {**base, "user": "what is on my calendar this week?", "must_contain_any": [existing[0][1], "Inbox", existing[1][1]]}
    if variant == 1:
        return {**base, "user": f"find the calendar event about {needle} and tell me when it is", "must_contain_any": [summary, needle]}
    if variant == 2:
        return {
            **base,
            "user": f"schedule {mark} tomorrow at 10am for 30 minutes",
            "marker": mark,
            "mutation": "calendar_created",
        }
    if variant == 3:
        return {
            **base,
            "user": f"move {mark} to 12:30pm and rename it {mark} updated",
            "marker": mark,
            "seed_event": {
                "summary": mark,
                "dtstart": day.isoformat(),
                "dtend": (day + timedelta(minutes=30)).isoformat(),
            },
            "mutation": "calendar_updated",
            "updated_text": "updated",
        }
    return {
        **base,
        "user": f"delete the calendar event named {mark}",
        "marker": mark,
        "seed_event": {
            "summary": mark,
            "dtstart": day.isoformat(),
            "dtend": (day + timedelta(minutes=30)).isoformat(),
        },
        "mutation": "calendar_deleted",
    }


def build_cases(per_domain: int, owner: str = DEFAULT_OWNER) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for i in range(per_domain):
        cases.append(build_email_case(i, owner))
    for i in range(per_domain):
        cases.append(build_note_case(i, owner))
    for i in range(per_domain):
        cases.append(build_calendar_case(i, owner))
    return cases


def run_case(client: httpx.Client, args: argparse.Namespace, case: dict[str, Any]) -> dict[str, Any]:
    session_id = ""
    started = time.time()
    seeded: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    answer = ""
    error = ""
    state: dict[str, Any] = {}
    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _case_timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, max(1.0, float(args.case_timeout)))
    try:
        seeded = seed_case(args.owner, case)
        session_id = create_session(client, args, case)
        events, answer = stream_turn(client, args, session_id, case["user"])
        state = collect_state_and_cleanup(args.owner, case, seeded)
        passed, failures = score(case, events, answer, state)
    except Exception as exc:
        error = repr(exc)
        state = collect_state_and_cleanup(args.owner, case, seeded)
        passed = False
        failures = [f"exception: {error}"]
    if not passed and session_id:
        delete_session(client, args.base_url, session_id)
        removed = quarantine_sft_rows(args.owner, session_id, "; ".join(failures)[:300])
    else:
        removed = 0
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, old_handler)
    return {
        "id": case["id"],
        "domain": case["domain"],
        "index": case["index"],
        "session_id": session_id,
        "user": case["user"],
        "pass": passed,
        "failures": failures,
        "tool_names": tool_names(events),
        "answer": answer,
        "state": state,
        "quarantined_trace_rows": removed,
        "elapsed_seconds": round(time.time() - started, 3),
        "error": error,
    }


def load_existing_results(out_dir: Path, allowed_ids: set[str]) -> list[dict[str, Any]]:
    path = out_dir / "actual_results.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("results")
    if not isinstance(rows, list):
        return []
    clean_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = str(row.get("id") or "")
        if allowed_ids and row_id not in allowed_ids:
            continue
        names = list(row.get("tool_names") or [])
        if row.get("pass") is not True:
            continue
        if row_id.startswith("email_draft_reply_") and names.count("ui_control") > 1:
            continue
        if row.get("domain") == "email" and "manage_memory" in names:
            continue
        # Keep the latest clean result for a case id. This makes resume robust
        # if a prior collector was interrupted while another round was starting
        # and the report briefly accumulated duplicate clean rows.
        clean_by_id[row_id] = row
    return list(clean_by_id.values())


def write_outputs(out_dir: Path, cases: list[dict[str, Any]], results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    summary: dict[str, Any] = {
        "total": len(results),
        "passed": sum(1 for r in results if r["pass"]),
        "failed": sum(1 for r in results if not r["pass"]),
        "by_domain": {},
    }
    for domain in ["email", "notes", "calendar"]:
        subset = [r for r in results if r["domain"] == domain]
        summary["by_domain"][domain] = {
            "total": len(subset),
            "passed": sum(1 for r in subset if r["pass"]),
            "failed": sum(1 for r in subset if not r["pass"]),
        }
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "owner": args.owner,
        "endpoint": args.endpoint,
        "endpoint_id": args.endpoint_id,
        "model": args.model,
        "summary": summary,
        "cases": cases,
        "results": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        out_dir / "actual_results.json",
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
    )
    lines = [
        f"# SFT Overnight Fixture Flow Run",
        "",
        f"- owner: `{args.owner}`",
        f"- model: `{args.model}`",
        f"- total: {summary['passed']}/{summary['total']} passed",
        "",
    ]
    for domain, row in summary["by_domain"].items():
        lines.append(f"- {domain}: {row['passed']}/{row['total']} passed")
    failed = [r for r in results if not r["pass"]]
    if failed:
        lines.extend(["", "## Failures"])
        for r in failed[:80]:
            lines.append(f"- `{r['id']}` session `{r['session_id']}`: {', '.join(r['failures'])}")
    atomic_write_text(out_dir / "summary.md", "\n".join(lines) + "\n")


def clean_counts_by_domain(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"email": 0, "notes": 0, "calendar": 0}
    for row in results:
        if row.get("pass") is True:
            domain = str(row.get("domain") or "")
            if domain in counts:
                counts[domain] += 1
    return counts


def write_curated_trace_outputs(args: argparse.Namespace, results: list[dict[str, Any]]) -> dict[str, Any]:
    trace_path = DATA_DIR / "sft_traces" / f"{args.owner}.jsonl"
    if not trace_path.exists():
        return {"skipped": True, "reason": f"missing trace file {trace_path}"}

    passing_sessions = {
        str(row.get("session_id") or ""): row
        for row in results
        if row.get("pass") is True and row.get("session_id")
    }
    rows = load_trace_rows(trace_path)
    stem = args.out_dir.name
    curated_path = DATA_DIR / "sft_traces" / f"{args.owner}.{stem}.curated.jsonl"
    thinking_path = DATA_DIR / "sft_traces" / f"{args.owner}.{stem}.curated_thinking.jsonl"

    curated, summary = curate_rows(rows, passing_sessions)
    write_jsonl(curated_path, curated)
    thinking_curated, thinking_summary = curate_rows(rows, passing_sessions, require_thinking=True)
    write_jsonl(thinking_path, thinking_curated)

    summary_path = args.out_dir / "curated_trace_summary.json"
    thinking_summary_path = args.out_dir / "curated_thinking_trace_summary.json"
    atomic_write_text(summary_path, json.dumps(summary, indent=2, ensure_ascii=True) + "\n")
    atomic_write_text(
        thinking_summary_path,
        json.dumps(thinking_summary, indent=2, ensure_ascii=True) + "\n",
    )

    return {
        "skipped": False,
        "curated_path": str(curated_path),
        "curated_summary": summary,
        "curated_thinking_path": str(thinking_path),
        "curated_thinking_summary": thinking_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--endpoint-id", default=DEFAULT_ENDPOINT_ID)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--per-domain", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--case-timeout", type=float, default=240)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR / "evals" / f"sft_overnight_{DEFAULT_OWNER}_{time.strftime('%Y%m%d_%H%M%S')}")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--domains", default="email,notes,calendar", help="Comma-separated domains to run.")
    parser.add_argument(
        "--target-clean-per-domain",
        type=int,
        default=0,
        help="Stop once each requested domain has this many passing rows; failures remain quarantined/auditable.",
    )
    parser.add_argument(
        "--skip-curated-export",
        action="store_true",
        help="Do not emit run-specific curated SFT JSONL outputs at completion.",
    )
    args = parser.parse_args()

    cases = build_cases(args.per_domain, args.owner)
    wanted_domains = {part.strip() for part in args.domains.split(",") if part.strip()}
    if wanted_domains:
        cases = [case for case in cases if case["domain"] in wanted_domains]
    if args.limit:
        cases = cases[: args.limit]
    ensure_calendar(args.owner)

    selected_ids = {str(case["id"]) for case in cases}
    results: list[dict[str, Any]] = load_existing_results(args.out_dir, selected_ids)
    completed_ids = {str(result.get("id") or "") for result in results}
    if completed_ids:
        print(json.dumps({
            "resume": True,
            "out_dir": str(args.out_dir),
            "completed": len(completed_ids),
        }), flush=True)
    client = httpx.Client(follow_redirects=False)
    try:
        login(client, args.base_url, args.owner, args.password)
        for idx, case in enumerate(cases, start=1):
            if args.target_clean_per_domain:
                clean_counts = clean_counts_by_domain(results)
                if clean_counts.get(case["domain"], 0) >= args.target_clean_per_domain:
                    continue
            if case["id"] in completed_ids:
                continue
            result = run_case(client, args, case)
            results.append(result)
            completed_ids.add(case["id"])
            print(json.dumps({
                "idx": idx,
                "total": len(cases),
                "id": result["id"],
                "pass": result["pass"],
                "tools": result["tool_names"],
                "session_id": result["session_id"],
                "failures": result["failures"],
            }), flush=True)
            write_outputs(args.out_dir, cases, results, args)
            if args.sleep:
                time.sleep(args.sleep)
    finally:
        client.close()
    write_outputs(args.out_dir, cases, results, args)
    failed = sum(1 for r in results if not r["pass"])
    clean_counts = clean_counts_by_domain(results)
    target_met = True
    if args.target_clean_per_domain:
        target_met = all(
            clean_counts.get(domain, 0) >= args.target_clean_per_domain
            for domain in wanted_domains
        )
    curated_info: dict[str, Any] = {}
    if not args.skip_curated_export:
        try:
            curated_info = write_curated_trace_outputs(args, results)
        except Exception as exc:
            curated_info = {"skipped": True, "reason": f"curated export failed: {exc!r}"}

    print(json.dumps({
        "out_dir": str(args.out_dir),
        "total": len(results),
        "failed": failed,
        "clean_counts": clean_counts,
        "target_clean_per_domain": args.target_clean_per_domain,
        "target_met": target_met,
        "curated_trace": curated_info,
    }, indent=2), flush=True)
    curated_ok = (
        args.skip_curated_export
        or curated_info.get("skipped") is False
        and not (curated_info.get("curated_summary") or {}).get("missing_without_reason")
        and not (curated_info.get("curated_thinking_summary") or {}).get("missing_without_reason")
    )
    return 0 if target_met and curated_ok and (args.target_clean_per_domain or failed == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
