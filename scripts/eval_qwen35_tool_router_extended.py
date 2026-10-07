#!/usr/bin/env python3
"""Expanded live Odysseus eval for compact Qwen tool-router models.

The important distinction for this project is exact native emission vs.
app-level success after parser repair. This script records both.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import signal
import time
from pathlib import Path
from typing import Any

import httpx

try:
    # Works when imported by the test suite from the repository root.
    from scripts.eval_odysseus_tool_use import _cookie, _reported_model, run_case
except ModuleNotFoundError:
    # Preserve direct script execution from the scripts directory.
    from eval_odysseus_tool_use import _cookie, _reported_model, run_case


DEFAULT_CASES: list[dict[str, Any]] = [
    {
        "case": "general_hi",
        "message": "hi",
        "expected_tool": "",
        "expected_action": "",
        "kind": "no_tool",
    },
    {
        "case": "general_map_no_tool",
        "message": "where is Sweden on a map?",
        "expected_tool": "",
        "expected_action": "",
        "kind": "no_tool",
    },
    {
        "case": "notes_list",
        "message": "what are my notes?",
        "expected_tool": "manage_notes",
        "expected_action": "list",
    },
    {
        "case": "notes_search",
        "message": "find my note called Japan",
        "expected_tool": "manage_notes",
        "expected_action": "search",
    },
    {
        "case": "notes_create",
        "message": "create a note titled ODY-EVAL-EXT-CREATE with body live eval create body",
        "expected_tool": "manage_notes",
        "expected_action": "add",
        "mutates": True,
    },
    {
        "case": "notes_delete_title",
        "message": "delete the note titled ODY-EVAL-EXT-DELETE-TITLE",
        "expected_tool": "manage_notes",
        "expected_action": "delete",
        # Title deletes may safely resolve the title before the destructive
        # call; score the first lookup as valid only when a delete executes.
        "acceptable_first_actions": ["delete", "search"],
        "seed_note_title": "ODY-EVAL-EXT-DELETE-TITLE",
        "seed_note_content": "delete title seed",
        "mutates": True,
    },
    {
        "case": "notes_delete_id",
        "message_template": "delete note {note_id}",
        "expected_tool": "manage_notes",
        "expected_action": "delete",
        "seed_note_title": "ODY-EVAL-EXT-DELETE-ID",
        "seed_note_content": "delete id seed",
        "mutates": True,
    },
    {
        "case": "calendar_list",
        "message": "what is on my calendar?",
        "expected_tool": "manage_calendar",
        "expected_action": "list_events",
    },
    {
        "case": "email_latest",
        "message": "what is my latest email?",
        "expected_tool": "mcp__email__list_emails",
        "expected_action": "",
    },
    {
        "case": "email_search",
        "message": "find emails from Runpod",
        "expected_tool": "mcp__email__search_emails",
        "expected_action": "",
    },
    {
        "case": "tasks_list",
        "message": "list my tasks",
        "expected_tool": "manage_tasks",
        "expected_action": "list",
    },
    {
        "case": "documents_list",
        "message": "list my documents",
        "expected_tool": "manage_documents",
        "expected_action": "list",
    },
    {
        "case": "memory_list",
        "message": "list my saved memories",
        "expected_tool": "manage_memory",
        "expected_action": "list",
    },
    {
        "case": "memory_search",
        "message": "what do you remember about my nationality?",
        "expected_tool": "manage_memory",
        "expected_action": "search",
    },
    {
        "case": "sessions_list",
        "message": "list my chat sessions",
        "expected_tool": "list_sessions",
        "expected_action": "",
    },
    {
        "case": "contacts_list",
        "message": "list my contacts",
        "expected_tool": "manage_contact",
        "expected_action": "list",
    },
    {
        "case": "research_list",
        "message": "list my saved research reports",
        "expected_tool": "manage_research",
        "expected_action": "list",
    },
]


WEB_CASE = {
    "case": "web_search",
    "message": "search the web for current public domain art websites",
    "expected_tool": "web_search",
    "expected_action": "",
}


TOOL_ALIASES = {
    "mcp_email_list_emails": "mcp__email__list_emails",
    "mcp_email_search_emails": "mcp__email__search_emails",
    "search_chats": "list_sessions",
}


def cleanup_notes(client: httpx.Client, base_url: str) -> None:
    try:
        response = client.get(base_url.rstrip("/") + "/api/notes", timeout=20)
        response.raise_for_status()
        notes = response.json().get("notes", [])
    except Exception as exc:
        # Cleanup is auxiliary. A slow scheduler or unavailable notes route
        # must not erase the checkpoint containing the actual eval results.
        print(json.dumps({"cleanup_warning": repr(exc)}), flush=True)
        return
    for note in notes:
        title = str(note.get("title") or "")
        note_id = str(note.get("id") or "")
        if title.startswith("ODY-EVAL-EXT-") and note_id:
            try:
                client.delete(base_url.rstrip("/") + f"/api/notes/{note_id}", timeout=20)
            except Exception as exc:
                print(json.dumps({"cleanup_warning": repr(exc), "note_id": note_id}), flush=True)


def seed_note(client: httpx.Client, base_url: str, title: str, content: str) -> str:
    response = client.post(
        base_url.rstrip("/") + "/api/notes",
        json={
            "title": title,
            "content": content,
            "note_type": "note",
            "pinned": False,
            "archived": False,
            "source": "agent",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["id"]


def _raw_round_text(record: dict[str, Any]) -> str:
    metrics = record.get("metrics") or {}
    round_texts = metrics.get("round_texts") or []
    return "\n---ROUND---\n".join(str(item) for item in round_texts)


def _extract_raw_tool(raw: str) -> str | None:
    patterns = [
        r"<function=([A-Za-z0-9_]+)>",
        r"\bfunction=([A-Za-z0-9_]+)",
        r'"function"\s*:\s*"([^"]+)"',
        r'"tool"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, raw)
        if match:
            return match.group(1)
    return None


def _extract_raw_action(raw: str) -> str | None:
    patterns = [
        r"parameter=action\s*\n([^\n<]+)",
        r"<parameter=action>\s*([^<]+)",
        r'"action"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, raw)
        if match:
            return match.group(1).strip()
    return None


def _canonical_tool(tool: str | None) -> str | None:
    if not tool:
        return tool
    return TOOL_ALIASES.get(tool, tool)


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


def timeout_record(case: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    return {
        "case": case["case"],
        "message": case.get("message") or case.get("message_template") or "",
        "expected_tool": case["expected_tool"],
        "first_tool": None,
        "native_call_ok": False,
        "tool_count": 0,
        "execution_ok": False,
        "duplicate_textual_call": False,
        "stream_errors": [{"type": "hard_timeout", "error": repr(exc)}],
        "stream_exception": repr(exc),
        "tool_outputs": [],
        "metrics": None,
        "elapsed_seconds": None,
        "response": "",
    }


def _discover_router_model(endpoint: str) -> str:
    """Choose the advertised Qwen router when the eval caller omits a model."""
    probe_urls = [endpoint.rstrip("/") + "/models"]
    if "host.docker.internal" in endpoint:
        probe_urls.append(endpoint.replace("host.docker.internal", "127.0.0.1").rstrip("/") + "/models")
    response = None
    last_error: Exception | None = None
    for probe_url in probe_urls:
        try:
            response = httpx.get(probe_url, timeout=15)
            break
        except httpx.HTTPError as exc:
            last_error = exc
    if response is None:
        raise SystemExit(f"Could not discover models from {probe_urls}: {last_error}")
    response.raise_for_status()
    payload = response.json()
    model_ids = [
        str(item.get("id") or "").strip()
        for item in (payload.get("data") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    candidates = [
        model_id for model_id in model_ids
        if "qwen35-9b-tool-router" in model_id.lower()
    ]
    if not candidates:
        raise SystemExit(
            "No advertised qwen35-9b-tool-router model found; "
            f"available={model_ids}"
        )
    return candidates[0]


def annotate(record: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    raw = _raw_round_text(record)
    raw_tool = _extract_raw_tool(raw)
    raw_action = _extract_raw_action(raw)
    expected_tool = case["expected_tool"]
    expected_action = case.get("expected_action") or ""
    acceptable_first_actions = set(case.get("acceptable_first_actions") or [])
    if expected_action and not acceptable_first_actions:
        acceptable_first_actions = {expected_action}
    no_tool = case.get("kind") == "no_tool"
    metrics = record.get("metrics") or {}
    round_texts = metrics.get("round_texts") or []
    final_round_text = str(round_texts[-1]) if round_texts else ""
    response = record.get("response") or ""
    tool_events = metrics.get("tool_events") or []
    executed_actions: list[str] = []
    structured_tool = None
    structured_action = None
    for event in tool_events:
        raw_command = event.get("command") or ""
        try:
            command = json.loads(raw_command or "{}")
        except Exception:
            command = raw_command
        if structured_tool is None:
            structured_tool = event.get("tool")
        if isinstance(command, dict):
            action = str(command.get("action") or "")
            executed_actions.append(action)
            if structured_action is None:
                structured_action = action
        elif isinstance(command, str) and command.strip():
            action = command.strip().splitlines()[0]
            executed_actions.append(action)
            if structured_action is None:
                structured_action = action
    visible_tool = _canonical_tool(raw_tool)
    structured_tool = _canonical_tool(structured_tool or record.get("first_tool"))
    visible_action = raw_action
    exact_tool_ok = (visible_tool is None and no_tool) or (
        (visible_tool or structured_tool) == expected_tool
    )
    exact_action_ok = not expected_action or (
        (visible_action or structured_action) in acceptable_first_actions
        and (
            "search" not in acceptable_first_actions
            or "delete" not in acceptable_first_actions
            or "delete" in executed_actions
        )
    )
    raw_visible_exact_ok = bool(
        ((raw_tool is None and no_tool) or visible_tool == expected_tool)
        and (not expected_action or visible_action in acceptable_first_actions)
    )
    structured_native_ok = bool(
        ((structured_tool is None and no_tool) or structured_tool == expected_tool)
        and (not expected_action or structured_action in acceptable_first_actions)
    )
    if no_tool:
        behavior_ok = record.get("tool_count") == 0 and bool(response or final_round_text)
        # No-tool turns have no execution artifact by design. Treat a clean
        # final response as the successful execution of the case so the
        # matrix's aggregate execution score remains meaningful.
        if behavior_ok and not record.get("stream_errors"):
            record["execution_ok"] = True
    elif case.get("mutates") and expected_action:
        behavior_ok = bool(record.get("execution_ok")) and expected_action in executed_actions
    else:
        behavior_ok = bool(record.get("execution_ok"))
    record.update(
        {
            "expected_action": expected_action,
            "raw_tool": raw_tool,
            "raw_action": raw_action,
            "structured_tool": structured_tool,
            "structured_action": structured_action,
            "raw_round_text": raw[:2000],
            "raw_visible_exact_ok": raw_visible_exact_ok,
            "structured_native_ok": structured_native_ok,
            "exact_tool_ok": bool(exact_tool_ok),
            "exact_action_ok": bool(exact_action_ok),
            "exact_native_ok": bool(exact_tool_ok and exact_action_ok),
            "behavior_ok": bool(behavior_ok),
            "response_or_round_text_present": bool(response or final_round_text.strip()),
            "input_tokens": metrics.get("input_tokens"),
            "output_tokens": metrics.get("output_tokens"),
            "tokens_per_second": metrics.get("tokens_per_second"),
        }
    )
    return record


def write_checkpoint(output: Path, records: list[dict[str, Any]], model: str) -> None:
    """Persist a usable matrix result after each case, including interruptions."""
    summary = {
        "model": model,
        "cases": len(records),
        "exact_native_success": sum(r["exact_native_ok"] for r in records),
        "structured_native_success": sum(r["structured_native_ok"] for r in records),
        "raw_visible_exact_success": sum(r["raw_visible_exact_ok"] for r in records),
        "behavior_success": sum(r["behavior_ok"] for r in records),
        "execution_success": sum(r["execution_ok"] for r in records),
            "response_present": sum(r["response_or_round_text_present"] for r in records),
            "response_quality_success": sum(r.get("response_quality_ok", True) for r in records),
        "stream_errors": sum(bool(r["stream_errors"]) for r in records),
        "records": records,
    }
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n")
    temporary.replace(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:7011")
    parser.add_argument("--endpoint", default="http://host.docker.internal:18048/v1")
    parser.add_argument("--endpoint-id", default="ca27bdc1")
    parser.add_argument(
        "--model",
        default="",
        help="Advertised router model; omitted means discover it from --endpoint.",
    )
    parser.add_argument("--selected-endpoint-url", default="http://host.docker.internal:18048/v1")
    parser.add_argument("--selected-model", default="")
    parser.add_argument(
        "--client-runtime-context",
        default="",
        help="JSON object passed as the TUI client_runtime_context form field.",
    )
    parser.add_argument("--cookie-file", default="data/sessions.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--prompt-mode", default="auto")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--hard-case-timeout",
        type=float,
        default=0.0,
        help="Optional SIGALRM watchdog per case. Use for wedgy tools like web.",
    )
    parser.add_argument("--include-web", action="store_true")
    parser.add_argument("--cases", default="")
    args = parser.parse_args()
    if args.client_runtime_context:
        try:
            args.client_runtime_context = json.loads(args.client_runtime_context)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--client-runtime-context must be valid JSON: {exc}") from exc
        if not isinstance(args.client_runtime_context, dict):
            raise SystemExit("--client-runtime-context must decode to a JSON object")
    else:
        args.client_runtime_context = None

    if not args.model:
        # A caller that already selected the model should not trigger a probe
        # against the evaluator's unrelated default endpoint. This matters
        # for local tunnels, where /models may be unavailable even though the
        # selected chat endpoint is healthy.
        args.model = args.selected_model or _discover_router_model(args.endpoint)
    if not args.selected_model:
        args.selected_model = args.model

    selected = {item.strip() for item in args.cases.split(",") if item.strip()}
    available_cases = list(DEFAULT_CASES)
    if args.include_web:
        available_cases.append(WEB_CASE)
    cases = [case for case in available_cases if not selected or case["case"] in selected]
    unknown = selected - {case["case"] for case in available_cases}
    if unknown:
        raise SystemExit(f"Unknown case(s): {', '.join(sorted(unknown))}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    client = httpx.Client(
        cookies={"odysseus_session": _cookie(Path(args.cookie_file))},
        follow_redirects=False,
    )
    records: list[dict[str, Any]] = []
    try:
        cleanup_notes(client, args.base_url)
        for case in cases:
            case = dict(case)
            if case.get("seed_note_title"):
                note_id = seed_note(
                    client,
                    args.base_url,
                    case["seed_note_title"],
                    case["seed_note_content"],
                )
                if case.get("message_template"):
                    case["message"] = case["message_template"].format(note_id=note_id[:8])
                case["seed_note_id"] = note_id
            try:
                with hard_timeout(args.hard_case_timeout, case["case"]):
                    record = run_case(
                        client,
                        args,
                        case["case"],
                        case["message"],
                        case["expected_tool"],
                    )
            except TimeoutError as exc:
                record = timeout_record(case, exc)
            record = annotate(record, case)
            if case.get("seed_note_id"):
                record["seed_note_id"] = case["seed_note_id"]
            records.append(record)
            write_checkpoint(output, records, args.model)
            print(
                json.dumps(
                    {
                        k: record.get(k)
                        for k in (
                            "case",
                            "expected_tool",
                            "expected_action",
                            "raw_tool",
                            "raw_action",
                            "structured_tool",
                            "structured_action",
                            "first_tool",
                            "raw_visible_exact_ok",
                            "structured_native_ok",
                            "exact_native_ok",
                            "behavior_ok",
                            "execution_ok",
                            "response_quality_ok",
                            "tool_count",
                            "input_tokens",
                            "output_tokens",
                            "elapsed_seconds",
                            "stream_errors",
                        )
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
    finally:
        try:
            cleanup_notes(client, args.base_url)
        finally:
            client.close()

    summary = {
        "model": _reported_model(args),
        "cases": len(records),
        "exact_native_success": sum(r["exact_native_ok"] for r in records),
        "structured_native_success": sum(r["structured_native_ok"] for r in records),
        "raw_visible_exact_success": sum(r["raw_visible_exact_ok"] for r in records),
        "behavior_success": sum(r["behavior_ok"] for r in records),
        "execution_success": sum(r["execution_ok"] for r in records),
        "response_present": sum(r["response_or_round_text_present"] for r in records),
        "response_quality_success": sum(r.get("response_quality_ok", True) for r in records),
        "stream_errors": sum(bool(r["stream_errors"]) for r in records),
        "records": records,
    }
    write_checkpoint(output, records, args.model)
    print("SUMMARY", json.dumps({k: v for k, v in summary.items() if k != "records"}))


if __name__ == "__main__":
    main()
