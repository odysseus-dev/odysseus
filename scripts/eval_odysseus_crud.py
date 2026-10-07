#!/usr/bin/env python3
"""Exercise disposable CRUD workflows through the real Odysseus chat route.

The model must choose and execute the tools.  This runner never mutates the
database directly: each fixture is uniquely tagged, and cleanup is requested
through the model before the final verification turn.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import signal
import time
import uuid
from pathlib import Path

import httpx


def cookie(path: Path) -> str:
    sessions = json.loads(path.read_text())
    now = time.time()
    for token, row in sessions.items():
        if row.get("username") == "pewds" and row.get("expiry", 0) > now:
            return token
    raise RuntimeError("No valid pewds Odysseus session cookie found")


def events(response: httpx.Response):
    for line in response.iter_lines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def tool_ok(name: str | None, expected: set[str]) -> bool:
    aliases = {
        "mcp__contacts__manage_contact": "manage_contact",
        "mcp__email__list_emails": "list_emails",
    }
    return aliases.get(name, name) in expected


def output_ok(event: dict) -> bool:
    if event.get("exit_code") not in (0, None):
        return False
    output = event.get("output")
    return not isinstance(output, str) or not output.lstrip().lower().startswith("error")


def visible_event_text(event: dict) -> str:
    """Collect text from both streaming deltas and replacement final events."""
    if isinstance(event.get("delta"), str):
        return event["delta"]
    if event.get("type") == "final_response" and isinstance(event.get("content"), str):
        return event["content"]
    return ""


def approval_from_event(event: dict) -> dict | None:
    """Extract an opaque exact-approval payload from any SSE wrapper."""
    candidates = [event, event.get("data"), event.get("ask_user")]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        approval = candidate.get("ask_user") if isinstance(candidate.get("ask_user"), dict) else candidate
        if (
            isinstance(approval, dict)
            and approval.get("kind") == "tool_approval"
            and approval.get("approval_id")
        ):
            return approval
    return None


@contextlib.contextmanager
def hard_timeout(seconds: float | None, label: str):
    if not seconds or seconds <= 0:
        yield
        return

    def _raise_timeout(signum, frame):  # type: ignore[no-untyped-def]
        raise TimeoutError(f"{label} exceeded hard timeout {seconds}s")

    previous = signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def parse_command(command: str | None) -> tuple[str, dict | str | None]:
    if not command:
        return "", None
    try:
        parsed = json.loads(command)
    except json.JSONDecodeError:
        parsed = command
    if isinstance(parsed, dict):
        return str(parsed.get("action") or ""), parsed
    if isinstance(parsed, str):
        return parsed.strip().splitlines()[0] if parsed.strip() else "", parsed
    return "", parsed


def build_summary(records: list[dict], model: str, tag: str) -> dict:
    """Build the same scorecard for complete and checkpointed eval runs."""
    turns = [turn for workflow in records for turn in workflow.get("turns", [])]
    return {
        "model": model,
        "tag": tag,
        "workflows": len(records),
        "turns": len(turns),
        "native_success": sum(bool(turn.get("native_call_ok")) for turn in turns),
        "first_action_success": sum(bool(turn.get("first_action_ok")) for turn in turns),
        "tool_count_success": sum(bool(turn.get("tool_count_ok")) for turn in turns),
        "exact_arg_success": sum(bool(turn.get("exact_args_ok", True)) for turn in turns),
        "exact_arg_checked": sum(bool(turn.get("expected_exact_args")) for turn in turns),
        "execution_success": sum(bool(turn.get("execution_ok")) for turn in turns),
        "cleanup_or_verify_turns": sum(
            bool(turn.get("native_call_ok")) and bool(turn.get("execution_ok"))
            for turn in turns
            if turn.get("cleanup_or_verify_turn")
        ),
        "duplicate_textual_calls": sum(bool(turn.get("duplicate_textual_call")) for turn in turns),
        "stream_errors": sum(bool(turn["stream_errors"]) for turn in turns),
        "records": records,
    }


def write_checkpoint(output: Path, records: list[dict], model: str, tag: str) -> None:
    """Persist progress atomically after every completed turn.

    A hard timeout, killed terminal, or backend restart should leave a usable
    scorecard instead of an empty/missing result file. The temporary sibling is
    replaced only after the JSON has been fully written.
    """
    checkpoint = output.with_name(output.name + ".tmp")
    checkpoint.write_text(
        json.dumps(build_summary(records, model, tag), indent=2, ensure_ascii=True) + "\n"
    )
    checkpoint.replace(output)


def infra_record(message: str, expected: set[str], exc: BaseException, cleanup: bool = False) -> dict:
    return {
        "message": message,
        "expected_tools": sorted(expected),
        "expected_first_action": None,
        "max_tool_calls": None,
        "expected_exact_args": {},
        "tools": [],
        "tool_events": [],
        "approval_tool_events": [],
        "first_action": "",
        "native_call_ok": False,
        "first_action_ok": False,
        "tool_count_ok": False,
        "exact_args_ok": False,
        "exact_arg_failures": [
            {
                "field": "*",
                "expected": "turn could run",
                "actual": repr(exc),
            }
        ],
        "approval_required": False,
        "execution_ok": False,
        "duplicate_textual_call": False,
        "stream_errors": [{"type": "infra_exception", "error": repr(exc)}],
        "tool_outputs": [],
        "response": "",
        "elapsed_seconds": 0,
        "approval_turns": 0,
        "cleanup_or_verify_turn": cleanup,
        "infra_failure": True,
    }


def turn(
    client: httpx.Client,
    args,
    session_id: str,
    message: str,
    expected: set[str],
    expected_first_action: str | tuple[str, ...] | None = None,
    max_tool_calls: int | None = None,
    expected_exact_args: dict[str, str] | None = None,
) -> dict:
    started = time.monotonic()
    captured = []
    text = []
    stream_exception = None
    approval_turns = 0
    turn_data = {
        "message": message,
        "session": session_id,
        "mode": "agent",
        "agent_prompt_mode": args.prompt_mode,
        **({"selected_endpoint_id": args.endpoint_id} if args.endpoint_id else {}),
        **({"selected_endpoint_url": args.selected_endpoint_url} if args.selected_endpoint_url else {}),
        **({"selected_model": args.selected_model} if args.selected_model else {}),
    }
    try:
        with hard_timeout(args.hard_turn_timeout, message[:80]):
            while True:
                approval = None
                with client.stream(
                    "POST",
                    args.base_url.rstrip("/") + "/api/chat_stream",
                    data=turn_data,
                    headers={"Accept": "text/event-stream"},
                    timeout=args.timeout,
                ) as response:
                    response.raise_for_status()
                    for event in events(response):
                        captured.append(event)
                        approval = approval or approval_from_event(event)
                        visible_text = visible_event_text(event)
                        if visible_text:
                            if event.get("type") == "final_response":
                                # A continuation can replace the approval
                                # draft from the previous HTTP stream. Keep
                                # the evaluator's response metric aligned
                                # with the TUI/client rendering contract.
                                text[:] = [visible_text]
                            else:
                                text.append(visible_text)
                if not getattr(args, "auto_approve", True) or not approval or approval_turns >= 3:
                    break
                approval_turns += 1
                turn_data = {
                    **turn_data,
                    "tool_approval_id": approval["approval_id"],
                    "tool_approval_decision": "approve",
                }
    except Exception as exc:
        stream_exception = repr(exc)

    starts = [e for e in captured if e.get("type") == "tool_start"]
    outputs = [e for e in captured if e.get("type") == "tool_output"]
    doc_updates = [e for e in captured if e.get("type") == "doc_update"]
    errors = [e for e in captured if e.get("type") == "error"]
    metric_events = [e for e in captured if e.get("type") == "metrics"]
    latest_metrics = (metric_events[-1].get("data") or {}) if metric_events else {}
    model_request_snapshots = [
        {
            key: event.get(key)
            for key in (
                "round",
                "model",
                "messages",
                "tools",
                "temperature",
                "max_tokens",
                "prompt_type",
                "agent_prompt_mode",
            )
        }
        for event in captured
        if event.get("type") == "model_request_snapshot"
    ]
    metrics_round_texts = [
        str(item)[:2000]
        for item in (latest_metrics.get("round_texts") or [])
        if str(item).strip()
    ]
    event_types = [str(e.get("type") or "") for e in captured]
    if stream_exception:
        errors.append({"type": "client_exception", "error": stream_exception})
    rendered = "".join(text).strip()
    if not rendered:
        if metric_events:
            rendered = next(
                (str(item).strip() for item in reversed(metrics_round_texts) if str(item).strip()),
                "",
            )
    tool_events = []
    for idx, event in enumerate(starts):
        command = event.get("command")
        action, parsed = parse_command(command)
        tool_events.append(
            {
                "index": idx,
                "tool": event.get("tool"),
                "command": command,
                "action": action,
                "parsed_command": parsed,
            }
        )
    approval_events = []
    if not tool_events:
        for idx, event in enumerate(outputs):
            ask_user = event.get("ask_user")
            action_payload = ask_user.get("action") if isinstance(ask_user, dict) else None
            if not isinstance(action_payload, dict):
                continue
            command = action_payload.get("content")
            action, parsed = parse_command(command)
            approval_events.append(
                {
                    "index": idx,
                    "tool": action_payload.get("tool") or event.get("tool"),
                    "command": command,
                    "action": action,
                    "parsed_command": parsed,
                    "approval_required": True,
                }
            )
        if approval_events:
            tool_events = approval_events
    rendered_lower = rendered.lower()
    duplicate = any(
        marker in rendered_lower
        for marker in (
            "manage_notes(",
            "manage_calendar(",
            "manage_memory(",
            "manage_contact(",
            '"function"',
            "function=",
            "<function",
            "</function",
            "parameter=",
            "<parameter",
            "</parameter",
            "mcp__email__",
        )
    )
    expected_actions = (
        list(expected_first_action)
        if isinstance(expected_first_action, tuple)
        else expected_first_action
    )
    first_action_ok = expected_first_action is None or (
        bool(tool_events)
        and (
            tool_events[0]["action"] == expected_first_action
            if isinstance(expected_first_action, str)
            else tool_events[0]["action"] in expected_first_action
        )
    )
    exact_arg_failures = []
    expected_exact_args = dict(expected_exact_args or {})
    expected_arg_spec = dict(expected_exact_args)
    required_executed_actions = expected_exact_args.pop("__actions_include", [])
    if isinstance(required_executed_actions, str):
        required_executed_actions = [required_executed_actions]
    observed_actions = [str(event.get("action") or "") for event in tool_events]
    missing_required_actions = [
        action for action in required_executed_actions if action not in observed_actions
    ]
    for missing_action in missing_required_actions:
        exact_arg_failures.append(
            {
                "field": "__actions_include",
                "expected": missing_action,
                "actual": observed_actions,
            }
        )
    parsed_first = tool_events[0].get("parsed_command") if tool_events else None
    raw_first_command = tool_events[0].get("command") if tool_events else ""
    required_command_substrings = expected_exact_args.pop("__command_contains", [])
    if isinstance(required_command_substrings, str):
        required_command_substrings = [required_command_substrings]
    for expected_substring in required_command_substrings:
        if str(expected_substring) not in str(raw_first_command or ""):
            exact_arg_failures.append(
                {
                    "field": "__command_contains",
                    "expected": expected_substring,
                    "actual": raw_first_command,
                }
            )
    required_state_substrings = expected_exact_args.pop("__state_contains", [])
    if isinstance(required_state_substrings, str):
        required_state_substrings = [required_state_substrings]
    state_parts = [str(raw_first_command or ""), rendered]
    for event in outputs:
        for key in (
            "output",
            "document_title",
            "document_language",
            "document_content",
            "doc_id",
        ):
            value = event.get(key)
            if value is not None:
                state_parts.append(str(value))
    for event in doc_updates:
        for key in ("title", "language", "content", "doc_id", "version"):
            value = event.get(key)
            if value is not None:
                state_parts.append(str(value))
    state_text = "\n".join(state_parts)
    for expected_substring in required_state_substrings:
        if str(expected_substring) not in state_text:
            exact_arg_failures.append(
                {
                    "field": "__state_contains",
                    "expected": expected_substring,
                    "actual": state_text[:1200],
                }
            )
    if isinstance(parsed_first, dict):
        for key, expected_value in expected_exact_args.items():
            actual_value = parsed_first.get(key)
            if actual_value != expected_value:
                exact_arg_failures.append(
                    {
                        "field": key,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )
    elif expected_exact_args:
        exact_arg_failures.append(
            {
                "field": "*",
                "expected": expected_exact_args,
                "actual": parsed_first,
            }
        )
    return {
        "message": message,
        "expected_tools": sorted(expected),
        "expected_first_action": expected_actions,
        "max_tool_calls": max_tool_calls,
        "expected_exact_args": expected_arg_spec,
        "tools": [e.get("tool") for e in starts],
        "tool_events": tool_events,
        "approval_tool_events": approval_events,
        "first_action": tool_events[0]["action"] if tool_events else "",
        "native_call_ok": bool(tool_events) and any(tool_ok(e.get("tool"), expected) for e in tool_events),
        "first_action_ok": first_action_ok,
        "tool_count_ok": max_tool_calls is None or len(tool_events) <= max_tool_calls,
        "exact_args_ok": not exact_arg_failures,
        "exact_arg_failures": exact_arg_failures,
        "approval_required": bool(approval_events),
        "execution_ok": (
            bool(outputs)
            and not approval_events
            and all(output_ok(e) for e in outputs)
            and not missing_required_actions
        ),
        "duplicate_textual_call": duplicate,
        "stream_errors": errors,
        "tool_outputs": [
            {
                "tool": e.get("tool"),
                "exit_code": e.get("exit_code"),
                "output": str(e.get("output", ""))[:500],
                **(
                    {
                        "doc_id": e.get("doc_id"),
                        "document_action": e.get("document_action"),
                        "document_title": e.get("document_title"),
                        "document_language": e.get("document_language"),
                        "document_version": e.get("document_version"),
                        "document_content": str(e.get("document_content", ""))[:1200],
                    }
                    if e.get("doc_id") or e.get("document_content")
                    else {}
                ),
                **({"ask_user_action": e.get("ask_user", {}).get("action")} if isinstance(e.get("ask_user"), dict) else {}),
            }
            for e in outputs
        ],
        "doc_updates": [
            {
                "doc_id": e.get("doc_id"),
                "title": e.get("title"),
                "language": e.get("language"),
                "version": e.get("version"),
                "content": str(e.get("content", ""))[:1200],
            }
            for e in doc_updates
        ],
        "response": rendered.strip()[:1200],
        "metrics_round_texts": metrics_round_texts[-6:],
        "metrics_tool_calls": latest_metrics.get("tool_calls"),
        "model_request_snapshots": model_request_snapshots,
        "captured_event_types": event_types,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "approval_turns": approval_turns,
    }


def workflow(name: str, tag: str) -> list[tuple[str, set[str], bool, str | tuple[str, ...] | None, int | None, dict[str, str]]]:
    """Return prompt, expected tools, cleanup marker, expected action, max calls, exact args."""
    if name == "notes":
        return [
            (
                f"Create a temporary normal note titled {tag} with content 'temporary fixture'.",
                {"manage_notes"},
                False,
                "add",
                1,
                {"title": tag, "content": "temporary fixture"},
            ),
            # Title-based mutations may resolve the title first; require the
            # corresponding mutation to execute and allow that bounded pair.
            (
                f"Update the exact note titled {tag} so its content is 'updated fixture'.",
                {"manage_notes"},
                False,
                "update",
                1,
                {"title": tag, "content": "updated fixture"},
            ),
            (
                f"Delete the exact temporary note titled {tag}. Use the title directly; do not search first.",
                {"manage_notes"},
                True,
                "delete",
                1,
                {"title": tag},
            ),
            (
                f"Verify that the note titled {tag} no longer exists. Search for the exact title; do not create anything.",
                {"manage_notes"},
                True,
                "search",
                1,
                {"title": tag},
            ),
        ]
    if name == "calendar":
        return [
            (
                f"Create one temporary calendar event titled {tag} on 2030-01-02 from 10:00 to 11:00, description 'temporary fixture'.",
                {"manage_calendar"},
                False,
                "create_event",
                None,
                {"summary": tag, "description": "temporary fixture"},
            ),
            (
                f"Update the exact calendar event titled {tag}; change its location to 'Updated fixture location'. Use the exact title as the identifier.",
                {"manage_calendar"},
                False,
                "update_event",
                1,
                {"summary": tag, "location": "Updated fixture location"},
            ),
            (
                f"Delete only the temporary calendar event titled {tag}. Use the exact title as the identifier.",
                {"manage_calendar"},
                True,
                "delete_event",
                1,
                {"summary": tag},
            ),
            (
                f"Verify that calendar event {tag} is absent. Search the 2030-01-02 range; do not create anything.",
                {"manage_calendar"},
                True,
                "list_events",
                1,
                {"start": "2030-01-02", "end": "2030-01-03", "query": tag},
            ),
        ]
    if name == "memory":
        return [
            (
                f"Add one temporary saved memory with exact marker {tag} and text 'temporary fixture'; category fact.",
                {"manage_memory"},
                False,
                "add",
                1,
                {"__command_contains": [tag, "temporary fixture", "fact"]},
            ),
            (
                f"Search saved memory for the exact marker {tag}.",
                {"manage_memory"},
                False,
                "search",
                1,
                {"__command_contains": tag},
            ),
            (
                f"Delete only the temporary memory containing exact marker {tag}. Search first and use its memory_id.",
                {"manage_memory"},
                True,
                None,
                None,
                {"__command_contains": tag, "__actions_include": "delete"},
            ),
            (
                f"Verify that no saved memory containing exact marker {tag} remains. Search only; do not add anything.",
                {"manage_memory"},
                True,
                "search",
                1,
                {"__command_contains": tag},
            ),
        ]
    if name == "documents":
        return [
            (
                f"Create a temporary editor document titled {tag} with exactly this short content: temporary fixture.",
                {"create_document"},
                False,
                None,
                1,
                {"__state_contains": [tag, "temporary fixture"]},
            ),
            (
                f"Edit the active document {tag}: replace 'temporary fixture' with 'updated fixture'. Use the document edit tool.",
                {"edit_document", "update_document"},
                False,
                None,
                1,
                {"__state_contains": ["updated fixture"]},
            ),
            (
                f"Delete only the editor document titled {tag}. Find its document id if needed, then use the document management delete action.",
                {"manage_documents"},
                True,
                ("list", "delete"),
                None,
                {"__state_contains": tag, "__actions_include": "delete"},
            ),
            (
                f"Verify that editor document {tag} no longer exists by searching documents. Do not create anything.",
                {"manage_documents"},
                True,
                "list",
                1,
                {"__command_contains": tag},
            ),
        ]
    if name == "contacts":
        return [
            (
                f"Add one temporary fake contact named {tag}, email {tag.lower()}@invalid.example, phone +1-202-555-0199.",
                {"manage_contact"},
                False,
                "add",
                1,
                {
                    "name": tag,
                    "email": f"{tag.lower()}@invalid.example",
                    "__command_contains": "+1-202-555-0199",
                },
            ),
            (
                f"Update the exact contact named {tag}; change the phone to +1-202-555-0188.",
                {"manage_contact"},
                False,
                "update",
                None,
                {"__command_contains": [tag, "+1-202-555-0188"]},
            ),
            (
                f"Delete only the fake contact named {tag}. List/search first to get its UID, then delete it.",
                {"manage_contact"},
                True,
                None,
                None,
                {"__command_contains": tag, "__actions_include": "delete"},
            ),
            (
                f"Verify that contact {tag} is absent. Search contacts for the exact name; do not change any other contact.",
                {"manage_contact"},
                True,
                "search",
                1,
                {"__command_contains": tag},
            ),
        ]
    if name == "tasks":
        return [
            (
                f"Create one disposable scheduled task named {tag} that runs daily at 23:59 UTC and prompts exactly 'temporary fixture'. Use task_type llm and output_target session.",
                {"manage_tasks"},
                False,
                "create",
                1,
                {
                    "action": "create",
                    "name": tag,
                    "prompt": "temporary fixture",
                    "task_type": "llm",
                    "schedule": "daily",
                    "scheduled_time": "23:59",
                    "output_target": "session",
                },
            ),
            (
                f"Pause only the disposable scheduled task named {tag}. List/search first if needed to get its task_id.",
                {"manage_tasks"},
                False,
                None,
                None,
                {"__command_contains": tag, "__actions_include": "pause"},
            ),
            (
                f"Resume only the disposable scheduled task named {tag}. List/search first if needed to get its task_id.",
                {"manage_tasks"},
                False,
                None,
                None,
                {"__command_contains": tag, "__actions_include": "resume"},
            ),
            (
                f"Delete only the disposable scheduled task named {tag}. List/search first if needed to get its task_id.",
                {"manage_tasks"},
                True,
                None,
                None,
                {"__command_contains": tag, "__actions_include": "delete"},
            ),
            (
                f"Verify that scheduled task {tag} is absent. List/search tasks for the exact name; do not create anything.",
                {"manage_tasks"},
                True,
                "list",
                1,
                {"__command_contains": tag},
            ),
        ]
    if name == "skills":
        return [
            (
                f"Add one disposable draft skill named {tag.lower()} with description 'temporary fixture', procedure ['do nothing'], verification ['confirm fixture'], status draft.",
                {"manage_skills"},
                False,
                "add",
                1,
                {
                    "name": tag.lower(),
                    "description": "temporary fixture",
                    "__command_contains": ["do nothing", "confirm fixture", "draft"],
                },
            ),
            (
                f"View the disposable draft skill named {tag.lower()} and confirm it exists.",
                {"manage_skills"},
                False,
                "view",
                1,
                {"__command_contains": tag.lower()},
            ),
            (
                f"Delete only the disposable draft skill named {tag.lower()}.",
                {"manage_skills"},
                True,
                "delete",
                1,
                {"__command_contains": tag.lower()},
            ),
            (
                f"Verify that disposable skill {tag.lower()} is absent by searching/listing skills. Do not create anything.",
                {"manage_skills"},
                True,
                ("list", "search"),
                1,
                {"__command_contains": tag.lower()},
            ),
        ]
    raise ValueError(name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", action="append", choices=["notes", "calendar", "memory", "documents", "contacts", "skills", "tasks"])
    parser.add_argument("--base-url", default="http://127.0.0.1:7011")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--endpoint-id", default="82e5463e")
    parser.add_argument("--model", required=True)
    parser.add_argument("--selected-endpoint-url", default="")
    parser.add_argument("--selected-model", default="")
    parser.add_argument("--cookie-file", default="data/sessions.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--prompt-mode", default="auto")
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--hard-turn-timeout", type=float, default=0)
    parser.add_argument(
        "--no-auto-approve",
        dest="auto_approve",
        action="store_false",
        help="Stop at the first exact approval instead of continuing it.",
    )
    parser.add_argument(
        "--independent-turns",
        action="store_true",
        help="Create a fresh session for each turn. Useful for no-approve proposal-accuracy checks where prior unexecuted approvals would contaminate history.",
    )
    args = parser.parse_args()
    workflows = args.workflow or ["notes", "calendar", "memory", "documents", "contacts", "skills"]
    tag = "ODY-EVAL-CRUD-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(cookies={"odysseus_session": cookie(Path(args.cookie_file))}, follow_redirects=False)
    records = []
    try:
        for name in workflows:
            workflow_records = []
            previous = None
            session_id = None
            try:
                if not args.independent_turns:
                    try:
                        create = client.post(
                            args.base_url.rstrip("/") + "/api/session",
                            data={
                                "name": f"[eval-crud] {name} {tag}",
                                "endpoint_url": args.endpoint,
                                "model": args.model,
                                "skip_validation": "true",
                                "rag": "false",
                                **({"endpoint_id": args.endpoint_id} if args.endpoint_id else {}),
                            },
                            timeout=30,
                        )
                        create.raise_for_status()
                        session_id = create.json()["id"]
                    except Exception as exc:
                        record = infra_record(
                            f"Create session for workflow {name}",
                            set(),
                            exc,
                        )
                        workflow_records.append(record)
                        print(json.dumps({"workflow": name, **record}, ensure_ascii=True), flush=True)
                        continue
                for turn_index, (prompt, expected, cleanup, expected_action, max_calls, exact_args) in enumerate(workflow(name, tag), start=1):
                    if args.independent_turns:
                        try:
                            create = client.post(
                                args.base_url.rstrip("/") + "/api/session",
                                data={
                                    "name": f"[eval-crud] {name} {tag} turn {turn_index}",
                                    "endpoint_url": args.endpoint,
                                    "model": args.model,
                                    "skip_validation": "true",
                                    "rag": "false",
                                    **({"endpoint_id": args.endpoint_id} if args.endpoint_id else {}),
                                },
                                timeout=30,
                            )
                            create.raise_for_status()
                            session_id = create.json()["id"]
                        except Exception as exc:
                            record = infra_record(prompt, expected, exc, cleanup)
                            workflow_records.append(record)
                            print(json.dumps({"workflow": name, **record}, ensure_ascii=True), flush=True)
                            break
                    # A fuzzy memory search must never authorize deletion of an
                    # unrelated record.  Require the unique marker to appear in
                    # the search result before allowing the delete turn.
                    if (
                        name == "memory"
                        and "Delete only the temporary memory" in prompt
                        and previous is not None
                        and tag not in " ".join(
                            item.get("output", "") for item in previous.get("tool_outputs", [])
                        )
                    ):
                        record = {
                            "message": prompt,
                            "expected_tools": sorted(expected),
                            "tools": [],
                            "native_call_ok": False,
                            "execution_ok": False,
                            "duplicate_textual_call": False,
                            "stream_errors": [],
                            "tool_outputs": [],
                            "response": "BLOCKED: preceding memory search did not return the unique fixture marker",
                            "elapsed_seconds": 0,
                            "cleanup_or_verify_turn": cleanup,
                            "blocked_by_safety_guard": True,
                        }
                        workflow_records.append(record)
                        print(json.dumps({"workflow": name, **record}, ensure_ascii=True), flush=True)
                        break
                    record = turn(
                        client,
                        args,
                        session_id,
                        prompt,
                        expected,
                        expected_action,
                        max_calls,
                        exact_args,
                    )
                    record["cleanup_or_verify_turn"] = cleanup
                    record["independent_turn"] = bool(args.independent_turns)
                    workflow_records.append(record)
                    previous = record
                    print(json.dumps({"workflow": name, **record}, ensure_ascii=True), flush=True)
                    if args.independent_turns and session_id:
                        try:
                            client.delete(args.base_url.rstrip("/") + f"/api/session/{session_id}", timeout=15)
                        except Exception:
                            pass
                        session_id = None
            finally:
                if session_id:
                    try:
                        client.delete(args.base_url.rstrip("/") + f"/api/session/{session_id}", timeout=15)
                    except Exception:
                        pass
                records.append({"workflow": name, "tag": tag, "turns": workflow_records})
                write_checkpoint(output, records, args.model, tag)
    finally:
        client.close()
    summary = build_summary(records, args.model, tag)
    write_checkpoint(output, records, args.model, tag)
    print("SUMMARY", json.dumps({k: summary[k] for k in summary if k != "records"}))


if __name__ == "__main__":
    main()
