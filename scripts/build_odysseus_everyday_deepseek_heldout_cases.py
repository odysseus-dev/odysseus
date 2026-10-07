#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib import request


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.database import ModelEndpoint, SessionLocal

DEFAULT_OUT = REPO_ROOT / "data/evals/ody_everyday_deepseek_heldout_v1_20260821/cases.json"


FAMILIES: dict[str, dict[str, Any]] = {
    "notes_create": {
        "count": 3,
        "instruction": "Personal note creation requests. The prompt must ask to add/create/save a note with the exact marker as the note title and a short body.",
        "case": {
            "kind": "note",
            "marker": "__MARKER__",
            "expect_first_tool": "manage_notes",
            "must_mutate": "note_created",
        },
        "default_user": "Add a note titled __MARKER__ saying buy oats after school pickup",
    },
    "tasks_recurring": {
        "count": 3,
        "instruction": "Recurring reminder/automation requests involving email/search words. The correct behavior is to create a scheduled task, not run the inner action now. Include exact marker as the task name.",
        "case": {
            "kind": "task",
            "marker": "__MARKER__",
            "expect_first_tool": "manage_tasks",
            "must_mutate": "task_created",
        },
        "default_user": "Every morning at 7:30, remind me to review the latest inbox email. Name it __MARKER__",
    },
    "calendar_create": {
        "count": 2,
        "instruction": "Calendar create requests for tomorrow at 7pm, with exact marker as title. Keep tomorrow/7pm so the existing state check applies.",
        "case": {
            "kind": "calendar",
            "marker": "__MARKER__",
            "expect_first_tool": "manage_calendar",
            "must_mutate": "calendar_created_2026_08_22_19",
        },
        "default_user": "Add dinner tomorrow at 7pm titled __MARKER__",
    },
    "calendar_move": {
        "count": 2,
        "instruction": "Calendar move requests. Ask to move the event with exact marker to 8pm tomorrow.",
        "case": {
            "kind": "calendar",
            "marker": "__MARKER__",
            "precreate_calendar_event": {
                "summary": "__MARKER__",
                "dtstart": "2026-08-22T19:00:00",
                "dtend": "2026-08-22T20:00:00",
            },
            "expect_first_tool": "manage_calendar",
            "must_mutate": "calendar_moved_2026_08_22_20",
        },
        "default_user": "Move my calendar event __MARKER__ to 8pm tomorrow",
    },
    "calendar_delete": {
        "count": 2,
        "instruction": "Calendar delete requests. Ask to delete/remove/cancel the existing event with exact marker as the name.",
        "case": {
            "kind": "calendar",
            "marker": "__MARKER__",
            "precreate_calendar_event": {
                "summary": "__MARKER__",
                "dtstart": "2026-08-22T13:00:00",
                "dtend": "2026-08-22T14:00:00",
            },
            "expect_first_tool": "manage_calendar",
            "must_mutate": "calendar_deleted",
        },
        "default_user": "Delete the calendar event named __MARKER__",
    },
    "email_latest": {
        "count": 3,
        "instruction": "Personal inbox/latest email requests. They must clearly refer to the user's own email, not public web search.",
        "case": {
            "kind": "email",
            "expect_first_tool_any": ["mcp__email__list_emails", "list_emails"],
            "forbidden_tools": ["web_search", "web_fetch"],
            "must_answer_any": ["From:", "UID", "Booking.com", "latest email"],
        },
        "default_user": "What's my latest emails",
    },
    "web_synthesis": {
        "count": 3,
        "instruction": "Public web lookup requests about why snails bubble/foam. The prompt should require lookup and explanation, not just links.",
        "case": {
            "kind": "web",
            "expect_first_tool": "web_search",
            "forbidden_repeat_tools": ["web_search"],
            "must_answer_any": ["mucus", "foam", "bubble"],
            "must_answer_any_2": ["stress", "irritant", "predator", "moisture", "defense"],
            "forbidden_final": ["Here are links for that topic", "WEB SEARCH RESULTS", "```sources"],
        },
        "default_user": "Look up why snails bubble up sometimes",
    },
    "draft_active_email": {
        "count": 3,
        "instruction": "Active email compose draft edit requests. The prompt must ask to write/update the open draft and include the phrase '8am works'.",
        "case": {
            "kind": "draft",
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
        "default_user": "Write a response to it saying 8am works for me",
    },
}


def deepseek_endpoint() -> dict[str, str]:
    db = SessionLocal()
    try:
        row = (
            db.query(ModelEndpoint)
            .filter(ModelEndpoint.name.ilike("%deepseek%"), ModelEndpoint.is_enabled == True)  # noqa: E712
            .order_by(ModelEndpoint.updated_at.desc())
            .first()
        )
        if row is None or not row.api_key:
            raise RuntimeError("no enabled DeepSeek endpoint with API key")
        return {
            "name": row.name,
            "base_url": row.base_url,
            "api_key": row.api_key,
            "cached_models": row.cached_models or "",
        }
    finally:
        db.close()


def call_deepseek(endpoint: dict[str, str], prompt: str) -> dict[str, Any]:
    model = "deepseek-chat"
    try:
        cached = json.loads(endpoint["cached_models"] or "[]")
        if cached:
            model = cached[0]
    except json.JSONDecodeError:
        pass
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return strict JSON only. No markdown."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 3000,
    }
    req = request.Request(
        endpoint["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {endpoint['api_key']}"},
        method="POST",
    )
    with request.urlopen(req, timeout=90) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I | re.S)
    parsed = json.loads(content)
    return {"model": model, "content": parsed}


def valid_user(family: str, text: Any) -> bool:
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    if family in {"notes_create", "tasks_recurring", "calendar_create", "calendar_move", "calendar_delete"} and "__MARKER__" not in text:
        return False
    if family == "calendar_create" and ("tomorrow" not in lowered or "7" not in lowered):
        return False
    if family == "calendar_move" and ("tomorrow" not in lowered or "8" not in lowered):
        return False
    if family == "draft_active_email" and "8am works" not in lowered:
        return False
    return 6 <= len(text.split()) <= 32


def build_cases(generated: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for family, spec in FAMILIES.items():
        prompts = generated.get(family, [])
        if not isinstance(prompts, list):
            prompts = []
        prompts = [item for item in prompts if valid_user(family, item)]
        prompts.append(spec["default_user"])
        chosen: list[str] = []
        for prompt in prompts:
            key = prompt.lower()
            if key in seen:
                continue
            seen.add(key)
            chosen.append(prompt)
            if len(chosen) >= spec["count"]:
                break
        while len(chosen) < spec["count"]:
            chosen.append(spec["default_user"])
        for idx, user in enumerate(chosen):
            case = dict(spec["case"])
            case.update({"id": f"deepseek_{family}_{idx:02d}", "user": user, "deepseek_family": family})
            cases.append(case)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    prompt = {
        "task": "Generate held-out everyday Odysseus tool-use eval prompts.",
        "date_context": "Current date is 2026-08-21 Asia/Tokyo; tomorrow is 2026-08-22.",
        "requirements": [
            "Return JSON object only.",
            "Keys must be exactly the family names provided.",
            "Each value is a list of natural user prompts.",
            "For marker families, include the literal placeholder __MARKER__ exactly once.",
            "Do not copy the default prompt; produce paraphrases.",
            "Keep prompts short and realistic.",
        ],
        "families": {name: {"count": spec["count"], "instruction": spec["instruction"], "default": spec["default_user"]} for name, spec in FAMILIES.items()},
    }
    endpoint = deepseek_endpoint()
    started = time.time()
    response = call_deepseek(endpoint, json.dumps(prompt, ensure_ascii=False))
    cases = build_cases(response["content"])
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "build_odysseus_everyday_deepseek_heldout_cases.py",
        "provider": "DeepSeek",
        "model": response["model"],
        "elapsed_seconds": round(time.time() - started, 3),
        "families": {name: spec["count"] for name, spec in FAMILIES.items()},
        "raw_generated": response["content"],
        "cases": cases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "cases": len(cases), "model": response["model"], "elapsed_seconds": payload["elapsed_seconds"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
