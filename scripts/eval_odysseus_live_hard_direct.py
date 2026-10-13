#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import time
from pathlib import Path
from typing import Any

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_LOOP_SOURCE = REPO_ROOT / "src/agent_loop.py"
TOOLS_FILE = Path(str(Path(__file__).resolve().parents[1] / "data" / "odysseus_unified_tools.json"))


def runtime_system_prompt() -> str:
    tree = ast.parse(AGENT_LOOP_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "_QWEN38_TOOL_ROUTER_PROMPT" for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if isinstance(value, str) and value.strip():
            return value
    raise RuntimeError("could not find _QWEN38_TOOL_ROUTER_PROMPT")


def load_tools(names: set[str]) -> list[dict[str, Any]]:
    payload = json.loads(TOOLS_FILE.read_text(encoding="utf-8"))
    tools = [item for item in payload["tools"] if item.get("function", {}).get("name") in names]
    found = {item["function"]["name"] for item in tools}
    missing = names - found
    if missing:
        raise RuntimeError(f"missing tool schemas: {sorted(missing)}")
    return tools


def load_all_tools() -> list[dict[str, Any]]:
    payload = json.loads(TOOLS_FILE.read_text(encoding="utf-8"))
    tools = payload.get("tools")
    if not isinstance(tools, list):
        raise RuntimeError(f"invalid tools file: {TOOLS_FILE}")
    return tools


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


def first_call(message: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    calls = message.get("tool_calls") or []
    if not calls:
        return None, {}
    fn = calls[0].get("function") or {}
    return str(fn.get("name") or ""), parse_args(fn.get("arguments"))


def call_chat(client: httpx.Client, base_url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    started = time.time()
    response = client.post(base_url.rstrip("/") + "/chat/completions", json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    data["elapsed_seconds"] = round(time.time() - started, 3)
    return data


def message_from(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices") or []
    if not choices:
        return {}
    return choices[0].get("message") or {}


def tool_call_message(call: dict[str, Any]) -> dict[str, Any]:
    return {"role": "assistant", "content": "", "tool_calls": [call]}


def score_contains(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def cases() -> list[dict[str, Any]]:
    active_doc = (
        "To: test@example.com\n"
        "Subject: Re: Test manual draft\n"
        "In-Reply-To: <manual@example.com>\n"
        "References: <manual@example.com>\n"
        "X-Source-UID: 999999\n"
        "---\n\n"
        "---------- Previous message ----------\n"
        "Can you confirm the meeting time?\n"
    )
    return [
        {
            "case_id": "calendar_tomorrow_8am",
            "user": "Add event tomorrow for meeting 8am",
            "tools": {"manage_calendar"},
            "expected_first_tool": "manage_calendar",
            "expected_args": {"action": "create_event", "dtstart": "2026-08-22T08:00:00"},
        },
        {
            "case_id": "latest_emails_personal_domain",
            "user": "What's my latest emails",
            "tools": {"mcp__email__list_emails"},
            "expected_first_tool": "mcp__email__list_emails",
            "expected_args": {"folder": "INBOX", "max_results": 1, "unread_only": False},
            "tool_output": "Found 1 email(s):\n1. **Save up to 20% off car rentals**\n   From: Booking.com (email.campaign@sg.booking.com)\n   Date: Fri, 21 Aug 2026 06:43:57 +0200\n   UID: 91040",
            "final_needles": ["Booking.com", "UID", "latest email"],
        },
        {
            "case_id": "web_snails_synthesis",
            "user": "Look up why snails bubble up sometimes",
            "tools": {"web_search"},
            "expected_first_tool": "web_search",
            "tool_output": "Search result text: Snails bubble when air gets trapped in mucus foam. It is often caused by stress, predators, salt or chemical irritants, dehydration, and dry conditions. The foam protects the soft body and helps retain moisture.",
            "final_needles": ["mucus", "stress", "moisture"],
            "forbidden_final": ["Here are links for that topic"],
        },
        {
            "case_id": "active_email_draft_update",
            "user": "Write a response to it saying 8am works for me",
            "tools": {"update_document", "edit_document"},
            "system_suffix": "\n\nActive document:\n" + active_doc,
            "expected_first_tool": ["update_document", "edit_document"],
            "expected_args_contains": ["8am works"],
        },
    ]


def run_case(
    client: httpx.Client,
    base_url: str,
    model: str,
    system: str,
    case: dict[str, Any],
    timeout: float,
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": system + str(case.get("system_suffix") or "")},
        {"role": "user", "content": case["user"]},
    ]
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools if tools is not None else load_tools(set(case["tools"])),
        "temperature": 0,
        "top_p": 1,
        "max_tokens": 384,
        "stream": False,
    }
    first_data = call_chat(client, base_url, payload, timeout)
    first_message = message_from(first_data)
    first_tool, first_args = first_call(first_message)
    failures: list[str] = []
    expected_first_tool = case["expected_first_tool"]
    expected_tools = expected_first_tool if isinstance(expected_first_tool, list) else [expected_first_tool]
    if first_tool not in expected_tools:
        failures.append(f"expected first tool {expected_tools}, got {first_tool}")
    for key, expected in (case.get("expected_args") or {}).items():
        if first_args.get(key) != expected:
            failures.append(f"arg {key} expected {expected!r}, got {first_args.get(key)!r}")
    for needle in case.get("expected_args_contains") or []:
        if needle.lower() not in json.dumps(first_args, ensure_ascii=False).lower():
            failures.append(f"args missing {needle!r}")

    final_text = str(first_message.get("content") or "")
    second_tool: str | None = None
    second_args: dict[str, Any] = {}
    if case.get("tool_output") and first_message.get("tool_calls"):
        call = first_message["tool_calls"][0]
        messages = [
            *messages,
            tool_call_message(call),
            {
                "role": "tool",
                "tool_call_id": call.get("id") or "call_direct",
                "name": first_tool or case["expected_first_tool"],
                "content": case["tool_output"],
            },
        ]
        second_payload = {
            **payload,
            "messages": messages,
            "max_tokens": 384,
        }
        second_data = call_chat(client, base_url, second_payload, timeout)
        second_message = message_from(second_data)
        second_tool, second_args = first_call(second_message)
        final_text = str(second_message.get("content") or "")
        for needle in case.get("final_needles") or []:
            if needle.lower() not in final_text.lower():
                failures.append(f"final missing {needle!r}")
        for forbidden in case.get("forbidden_final") or []:
            if forbidden.lower() in final_text.lower():
                failures.append(f"final includes forbidden {forbidden!r}")

    return {
        "case_id": case["case_id"],
        "user": case["user"],
        "first_tool": first_tool,
        "first_args": first_args,
        "second_tool": second_tool,
        "second_args": second_args,
        "final_text": final_text,
        "passed": not failures,
        "failures": failures,
        "first_elapsed_seconds": first_data.get("elapsed_seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--all-tools", action="store_true", help="Expose the full unified Odysseus tool schema to every case.")
    args = parser.parse_args()

    system = (
        runtime_system_prompt()
        + "\n\nCurrent date and time: 2026-08-21 17:20 Asia/Tokyo. Tomorrow is 2026-08-22."
    )
    results = []
    selected_tools = load_all_tools() if args.all_tools else None
    with httpx.Client() as client:
        for case in cases():
            record = run_case(client, args.base_url, args.model, system, case, args.timeout, selected_tools)
            results.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "model": args.model,
        "base_url": args.base_url,
        "total": len(results),
        "passed": sum(1 for record in results if record["passed"]),
        "results": results,
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("SUMMARY", json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False))
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
