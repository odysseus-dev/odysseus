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


DEFAULT_OUT = REPO_ROOT / "data/evals/ody_everyday_deepseek_heldout_v3_20260821/cases.json"


FAMILIES: dict[str, dict[str, Any]] = {
    "negative_email_concept": {
        "count": 3,
        "instruction": "Text-only questions about what email/inbox/reply concepts mean. Do not ask to access the user's mailbox.",
        "case": {
            "kind": "negative_email",
            "expect_no_tool": True,
            "must_answer_any": ["email", "message", "reply", "inbox"],
            "forbidden_tools": ["web_search", "mcp__email__list_emails", "mcp__email__read_email"],
        },
        "default_user": "What does replying to an email mean? Don't open my inbox.",
    },
    "negative_calendar_concept": {
        "count": 3,
        "instruction": "Text-only calendar questions that explicitly do not ask to create/update/delete events.",
        "case": {
            "kind": "negative_calendar",
            "expect_no_tool": True,
            "must_answer_any": ["calendar", "event", "invite", "schedule"],
            "forbidden_tools": ["manage_calendar"],
        },
        "default_user": "What is a calendar invite? Don't add anything.",
    },
    "negative_web_no_lookup": {
        "count": 3,
        "instruction": "Text-only web/search concept prompts that explicitly say not to search or look anything up.",
        "case": {
            "kind": "negative_web",
            "expect_no_tool": True,
            "must_answer_any": ["search", "web", "pages", "results"],
            "forbidden_tools": ["web_search"],
        },
        "default_user": "Explain what search results are without searching.",
    },
    "notes_create": {
        "count": 4,
        "instruction": "Personal note creation requests. Include literal __MARKER__ exactly once as the note title and a short body.",
        "case": {
            "kind": "note",
            "marker": "__MARKER__",
            "expect_first_tool": "manage_notes",
            "must_mutate": "note_created",
        },
        "default_user": "Save a note titled __MARKER__ with body pick up dry cleaning",
    },
    "tasks_recurring": {
        "count": 4,
        "instruction": "Recurring reminder/automation requests that mention email/search/web/inbox words. Correct behavior is scheduled task creation, not doing the inner action immediately. Include __MARKER__ exactly once as task name.",
        "case": {
            "kind": "task",
            "marker": "__MARKER__",
            "expect_first_tool": "manage_tasks",
            "forbidden_tools": ["web_search", "mcp__email__list_emails"],
            "must_mutate": "task_created",
        },
        "default_user": "Create a recurring task named __MARKER__ to check my inbox every morning at 7:30",
    },
    "calendar_create": {
        "count": 4,
        "instruction": "Calendar create requests for tomorrow at 7pm. Include __MARKER__ exactly once as title/name.",
        "case": {
            "kind": "calendar",
            "marker": "__MARKER__",
            "expect_first_tool": "manage_calendar",
            "must_mutate": "calendar_created_2026_08_22_19",
        },
        "default_user": "Put __MARKER__ on my calendar tomorrow at 7pm",
    },
    "calendar_move": {
        "count": 4,
        "instruction": "Calendar move/reschedule requests for an existing event. Include __MARKER__ exactly once and move it to 8pm tomorrow.",
        "case": {
            "kind": "calendar",
            "marker": "__MARKER__",
            "precreate_calendar_event": {
                "summary": "__MARKER__",
                "dtstart": "2026-08-22T19:00:00",
                "dtend": "2026-08-22T20:00:00",
            },
            "expect_first_tool": "manage_calendar",
            "forbidden_tools": ["manage_tasks"],
            "must_mutate": "calendar_moved_2026_08_22_20",
        },
        "default_user": "Reschedule __MARKER__ to tomorrow at 8pm",
    },
    "calendar_delete": {
        "count": 4,
        "instruction": "Calendar delete/remove/cancel requests for an existing event by title/name. Include __MARKER__ exactly once.",
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
        "default_user": "Cancel the calendar event titled __MARKER__",
    },
    "email_latest": {
        "count": 4,
        "instruction": "Personal latest/recent inbox requests. They must refer to the user's own email and must not sound like public web search.",
        "case": {
            "kind": "email",
            "expect_first_tool_any": ["mcp__email__list_emails", "list_emails"],
            "forbidden_tools": ["web_search", "web_fetch"],
            "must_answer_any": ["From:", "UID", "latest email", "email"],
        },
        "default_user": "Show me the latest thing in my inbox.",
    },
    "web_synthesis": {
        "count": 4,
        "instruction": "Public web lookup requests about why snails bubble/foam. Must require lookup plus a concise explanation, not just links.",
        "case": {
            "kind": "web",
            "expect_first_tool": "web_search",
            "forbidden_repeat_tools": ["web_search"],
            "must_answer_any": ["mucus", "foam", "bubble"],
            "must_answer_any_2": ["stress", "irritant", "predator", "moisture", "defense"],
            "forbidden_final": ["Here are links for that topic", "WEB SEARCH RESULTS", "```sources"],
        },
        "default_user": "Find out why snails foam up and explain the reason.",
    },
    "draft_active_email": {
        "count": 4,
        "instruction": "Active email compose draft edit requests. Ask to write/update the open/current/active draft, and include phrase '8am works'. Do not ask to send.",
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
        "default_user": "In the active email draft, write that 8am works for me.",
        "fallback_users": [
            "In the active email draft, write that 8am works for me.",
            "Update the open email draft to say 8am works.",
            "Add to the current draft that 8am works for me.",
            "Write back in the active draft that 8am works.",
        ],
    },
}


def deepseek_endpoint() -> dict[str, str]:
    db = SessionLocal()
    try:
        row = (
            db.query(ModelEndpoint)
            .filter(
                ModelEndpoint.name.ilike("%deepseek%"),
                ModelEndpoint.is_enabled == True,  # noqa: E712
                ModelEndpoint.api_key.isnot(None),
                ModelEndpoint.api_key != "",
            )
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
            {"role": "system", "content": "Return strict JSON only. No markdown or commentary."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.85,
        "max_tokens": 5000,
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
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip(), flags=re.I | re.S)
    if not cleaned.startswith("{"):
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if match:
            cleaned = match.group(0)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DeepSeek response was not JSON: {cleaned[:1000]!r}") from exc
    return {"model": model, "content": parsed}


def valid_user(family: str, text: Any) -> bool:
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    marker_family = family in {
        "notes_create",
        "tasks_recurring",
        "calendar_create",
        "calendar_move",
        "calendar_delete",
    }
    if marker_family and text.count("__MARKER__") != 1:
        return False
    if family == "calendar_create" and ("tomorrow" not in lowered or "7" not in lowered):
        return False
    if family == "calendar_move" and ("tomorrow" not in lowered or "8" not in lowered):
        return False
    if family == "draft_active_email" and "8am works" not in lowered:
        return False
    if family.startswith("negative_") and any(word in lowered for word in ("open my", "show me my", "latest", "create", "delete", "remove", "schedule it")):
        return False
    return 5 <= len(text.split()) <= 34


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
        fallback_users = spec.get("fallback_users") or [spec["default_user"]]
        fallback_idx = 0
        while len(chosen) < spec["count"]:
            fallback = fallback_users[fallback_idx % len(fallback_users)]
            fallback_idx += 1
            key = fallback.lower()
            if key in seen and len(fallback_users) > 1:
                continue
            seen.add(key)
            chosen.append(fallback)
        for idx, user in enumerate(chosen):
            case = dict(spec["case"])
            case.update({"id": f"deepseek_v3_{family}_{idx:02d}", "user": user, "deepseek_family": family})
            cases.append(case)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    prompt = {
        "task": "Generate broader held-out everyday Odysseus tool-use eval prompts.",
        "date_context": "Current date is 2026-08-21 Asia/Tokyo; tomorrow is 2026-08-22.",
        "requirements": [
            "Return JSON object only.",
            "Keys must be exactly the family names provided.",
            "Each value is a list of natural user prompts.",
            "Generate at least count+3 prompts per family so validation can discard weak ones.",
            "For marker families, include literal placeholder __MARKER__ exactly once.",
            "Do not copy the default prompt; produce realistic paraphrases with varied syntax.",
            "Avoid multi-intent prompts; each prompt should test one requested action.",
        ],
        "families": {
            name: {
                "count": spec["count"],
                "instruction": spec["instruction"],
                "default": spec["default_user"],
            }
            for name, spec in FAMILIES.items()
        },
    }
    endpoint = deepseek_endpoint()
    started = time.time()
    response = call_deepseek(endpoint, json.dumps(prompt, ensure_ascii=False))
    cases = build_cases(response["content"])
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "build_odysseus_everyday_deepseek_heldout_v3_cases.py",
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
