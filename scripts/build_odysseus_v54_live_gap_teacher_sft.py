#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from urllib import request


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path(str(Path(__file__).resolve().parents[1] / "data" / "teacher_live_gaps" / "odysseus_v54_live_gap_teacher_20260821"))
DEFAULT_EVAL_OUT = REPO_ROOT / "data/evals/ody_v54_live_gap_teacher_heldout_20260821/cases.json"


WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current or source-backed information.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}


CALENDAR_TOOL = {
    "type": "function",
    "function": {
        "name": "manage_calendar",
        "description": "Create, update, list, and delete calendar events.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "summary": {"type": "string"},
                "dtstart": {"type": "string"},
                "dtend": {"type": "string"},
            },
            "required": ["action"],
        },
    },
}


FAMILIES: list[dict[str, Any]] = [
    {
        "name": "web_synthesis_animal_foam",
        "train_count": 48,
        "heldout_count": 12,
        "instruction": (
            "Public web lookup questions about animals producing foam, bubbles, froth, or mucus. "
            "The assistant must search once with specific biological terms and then synthesize a concise cause/explanation. "
            "Rows should include snails often, but also a few other small animal examples. Final answers must mention the relevant mechanism, "
            "not dump links or say evidence is insufficient when the simulated evidence is enough."
        ),
    },
    {
        "name": "web_retry_after_weak_results",
        "train_count": 24,
        "heldout_count": 8,
        "instruction": (
            "The first web_search result is weak, dictionary-like, or off-topic. The assistant should make one improved web_search "
            "with better scientific/current terms, then synthesize the answer. Focus on failures where a generic query found dictionary/noise."
        ),
    },
    {
        "name": "calendar_ambiguous_time_boundary",
        "train_count": 16,
        "heldout_count": 6,
        "instruction": (
            "Calendar requests with relative dates and ambiguous times. If the user says 8pm/8 PM/evening at 8, create or update 20:00. "
            "If the user only says 'at 8' without AM/PM or context, ask a short clarification instead of guessing 8pm."
        ),
    },
]


def stable_id(prefix: str, obj: dict[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=True)
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_terms(value: Any) -> list[str]:
    if isinstance(value, str):
        text = clean_text(value)
        return [text] if text else []
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    return []


def tool_call(name: str, arguments: dict[str, Any], suffix: str) -> dict[str, Any]:
    return {
        "id": f"call_{suffix}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, separators=(",", ":"), ensure_ascii=True),
        },
    }


def deepseek_endpoint() -> dict[str, str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if api_key:
        return {
            "name": "env-deepseek",
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "api_key": api_key,
            "cached_models": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        }
    db_path = REPO_ROOT / "data/app.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT name, base_url, api_key, cached_models
            FROM model_endpoints
            WHERE lower(name) LIKE '%deepseek%'
              AND COALESCE(is_enabled, 0) = 1
              AND COALESCE(api_key, '') != ''
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            raise RuntimeError("no enabled DeepSeek endpoint with API key")
        return {
            "name": row["name"],
            "base_url": row["base_url"],
            "api_key": row["api_key"],
            "cached_models": row["cached_models"] or "",
        }
    finally:
        conn.close()


def call_deepseek(endpoint: dict[str, str], prompt: dict[str, Any], max_tokens: int = 8000) -> dict[str, Any]:
    model = "deepseek-chat"
    try:
        cached = json.loads(endpoint.get("cached_models") or "[]")
        if cached:
            model = cached[0]
    except json.JSONDecodeError:
        if endpoint.get("cached_models"):
            model = endpoint["cached_models"]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return strict JSON only. No markdown or commentary."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0.65,
        "max_tokens": max_tokens,
    }
    req = request.Request(
        endpoint["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {endpoint['api_key']}"},
        method="POST",
    )
    with request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip(), flags=re.I | re.S)
    if not cleaned.startswith("{"):
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if match:
            cleaned = match.group(0)
    return {"model": model, "content": json.loads(cleaned)}


def teacher_prompt(family: dict[str, Any], count: int, batch: int) -> dict[str, Any]:
    return {
        "task": "Generate Odysseus SFT specs for live tool-use gaps.",
        "current_state": {
            "model": "qwen35-9b-tool-router-v53-web-repair",
            "live_gap_eval": "DeepSeek-heldout v3 rescored 38/41",
            "real_failures": [
                "Web search often searches but returns a weak snippet dump instead of a concise explanation.",
                "If search evidence is weak/noisy, the route should search again with better terms instead of giving up or dumping links.",
                "Calendar generated heldout contained an ambiguous 'tomorrow at 8' case; do not teach that bare 8 means 8pm.",
            ],
        },
        "family": family["name"],
        "count": count,
        "batch": batch,
        "family_instruction": family["instruction"],
        "requirements": [
            "Return JSON object with key rows: list.",
            "Return exactly count rows.",
            "Every row must have user and final.",
            "Web rows need ideal_query, evidence, query_must_include, answer_must_include.",
            "Retry rows also need bad_query and bad_evidence.",
            "Calendar rows need calendar_args for tool rows or no_tool=true for clarification rows.",
            "Use varied casual wording and typos, but do not include private names, email addresses, or secrets.",
            "Final answers must be concise and user-facing.",
            "Never include raw source blocks, WEB SEARCH RESULTS, or link dumps in final.",
        ],
        "target_examples_not_to_copy": [
            "Look up why snails produce foam and give me a short explanation.",
            "Why do snails make foam? Check online and explain briefly.",
            "Search the web for the reason snails bubble up, then summarize it concisely.",
            "Move EVENT to tomorrow at 8 PM.",
            "Move EVENT to tomorrow at 8.",
        ],
    }


def valid_spec(family: str, item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    user = clean_text(item.get("user"))
    final = clean_text(item.get("final"))
    if len(user.split()) < 4 or len(user) > 240 or not final:
        return False
    if any(bad in final for bad in ("WEB SEARCH RESULTS", "```sources", "Here are links")):
        return False
    if family.startswith("web_"):
        if not clean_text(item.get("ideal_query")):
            return False
        if family == "web_retry_after_weak_results" and not clean_text(item.get("bad_query")):
            return False
    if family == "calendar_ambiguous_time_boundary":
        if item.get("no_tool"):
            return bool(re.search(r"\b(?:am|pm|morning|evening|clarify|which)\b", final, re.I))
        args = item.get("calendar_args")
        if not isinstance(args, dict):
            return False
        action = str(args.get("action") or "").lower()
        if action not in {"create_event", "update_event"}:
            return False
        return bool(args.get("summary") and args.get("dtstart") and args.get("dtend"))
    return True


def deterministic_calendar_specs() -> list[dict[str, Any]]:
    tool_specs = [
        ("move the meeting to tomorrow at 8 PM", "update_event", "meeting", "2026-08-23T20:00:00", "Done. The meeting is moved to tomorrow at 8:00 PM."),
        ("reschedule dinner to tomorrow at 8 in the evening", "update_event", "dinner", "2026-08-23T20:00:00", "Done. Dinner is rescheduled to tomorrow at 8:00 PM."),
        ("shift the appointment to tomorrow at 8 PM", "update_event", "appointment", "2026-08-23T20:00:00", "Done. The appointment is moved to tomorrow at 8:00 PM."),
        ("schedule a call for Friday at 8 PM", "create_event", "Call", "2026-08-28T20:00:00", "Scheduled the call for Friday at 8:00 PM."),
        ("add lunch with Sam next Monday at 8pm", "create_event", "Lunch with Sam", "2026-08-24T20:00:00", "Scheduled lunch with Sam for next Monday at 8:00 PM."),
        ("book dinner Friday evening at 8", "create_event", "Dinner", "2026-08-28T20:00:00", "Scheduled dinner for Friday at 8:00 PM."),
        ("move the party to tomorrow evening at 8", "update_event", "party", "2026-08-23T20:00:00", "Done. The party is moved to tomorrow at 8:00 PM."),
        ("change my workout event to tomorrow at 8pm", "update_event", "workout", "2026-08-23T20:00:00", "Done. The workout is moved to tomorrow at 8:00 PM."),
    ]
    specs: list[dict[str, Any]] = []
    for user, action, summary, start, final in tool_specs:
        hour = int(start[11:13]) + 1
        specs.append({
            "user": user,
            "calendar_args": {
                "action": action,
                "summary": summary,
                "dtstart": start,
                "dtend": start[:11] + f"{hour:02d}" + start[13:],
            },
            "tool_result": "AI: Calendar updated.",
            "final": final,
        })
    for user in [
        "move meeting to tomorrow at 8",
        "can u move my workout to tmrw at 8?",
        "book dinner for Friday at 8?",
        "shift the appointment to tomorrow at 8",
        "move the event to tomorrow at 8",
        "reschedule lunch next Monday at 8",
        "change the appointment to the day after tomorrow at 8",
        "push the call to Friday at 8",
        "put the dentist appointment tomorrow at 8",
        "move my calendar event to 8 tomorrow",
        "schedule dinner at 8",
        "set the meeting for 8 tomorrow",
        "can we do the appointment at 8",
        "change it to 8",
    ]:
        specs.append({
            "user": user,
            "no_tool": True,
            "final": "Do you mean 8 AM or 8 PM?",
        })
    return specs


def build_sft_row(family: str, idx: int, spec: dict[str, Any], split: str) -> dict[str, Any]:
    user = clean_text(spec["user"])
    final = clean_text(spec["final"])
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    tools: list[dict[str, Any]] = []
    expected_calls = 0
    if family == "web_retry_after_weak_results":
        bad = tool_call("web_search", {"query": clean_text(spec["bad_query"])}, f"{family}_{idx}_bad")
        good = tool_call("web_search", {"query": clean_text(spec["ideal_query"])}, f"{family}_{idx}_good")
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [bad]},
            {"role": "tool", "tool_call_id": bad["id"], "content": clean_text(spec.get("bad_evidence"))},
            {"role": "assistant", "content": "", "tool_calls": [good]},
            {"role": "tool", "tool_call_id": good["id"], "content": clean_text(spec.get("evidence"))},
            {"role": "assistant", "content": final},
        ])
        tools = [WEB_SEARCH_TOOL]
        expected_calls = 2
    elif family.startswith("web_"):
        call = tool_call("web_search", {"query": clean_text(spec["ideal_query"])}, f"{family}_{idx}")
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "content": clean_text(spec.get("evidence"))},
            {"role": "assistant", "content": final},
        ])
        tools = [WEB_SEARCH_TOOL]
        expected_calls = 1
    elif family == "calendar_ambiguous_time_boundary" and spec.get("no_tool"):
        messages.append({"role": "assistant", "content": final})
    else:
        args = dict(spec["calendar_args"])
        call = tool_call("manage_calendar", args, f"{family}_{idx}")
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "content": clean_text(spec.get("tool_result")) or "AI: Calendar updated."},
            {"role": "assistant", "content": final},
        ])
        tools = [CALENDAR_TOOL]
        expected_calls = 1

    row = {
        "messages": messages,
        "tools": tools,
        "generator": "deepseek_teacher_v54_live_gap",
        "metadata": {
            "category": family,
            "split": split,
            "expected_tool_calls": expected_calls,
            "query_must_include": clean_terms(spec.get("query_must_include")),
            "answer_must_include": clean_terms(spec.get("answer_must_include")),
            "source_failures": [
                "deepseek_v3_web_synthesis_00",
                "deepseek_v3_web_synthesis_03",
                "deepseek_v3_calendar_move_02",
            ],
        },
    }
    row["uuid"] = stable_id("ody_v54_live_gap", row)
    return row


def build_eval_case(family: str, idx: int, spec: dict[str, Any]) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": f"v54_live_gap_{family}_{idx:02d}",
        "kind": "calendar" if family == "calendar_ambiguous_time_boundary" else "web",
        "user": clean_text(spec["user"]),
        "deepseek_family": family,
        "forbidden_final": ["WEB SEARCH RESULTS", "```sources", "Here are links for that topic"],
    }
    if family.startswith("web_"):
        user_lower = case["user"].lower()
        if re.search(r"\b(?:search|look\s+up|check\s+online|web|find\s+out|google)\b", user_lower):
            case["expect_first_tool"] = "web_search"
        if family != "web_retry_after_weak_results":
            case["max_web_searches"] = 1
        if family == "web_synthesis_animal_foam":
            case["must_answer_any"] = ["mucus", "foam", "bubble", "froth", "slime"]
            case["must_answer_any_2"] = [
                "stress",
                "defense",
                "irritat",
                "moisture",
                "predator",
                "protect",
                "osmosis",
                "salt",
            ]
        else:
            terms = clean_terms(spec.get("answer_must_include"))
            expanded: list[str] = []
            for term in terms:
                expanded.extend(part.strip() for part in re.split(r"[,/]| or ", term) if part.strip())
            if expanded:
                case["must_answer_any"] = expanded[:8]
    elif spec.get("no_tool"):
        case["expect_no_tool"] = True
        case["forbidden_tools"] = ["manage_calendar"]
        case["must_answer_any"] = ["AM", "PM", "morning", "evening", "clarify", "which"]
    else:
        case["expect_first_tool"] = "manage_calendar"
    return case


def split_rows(rows: list[dict[str, Any]], val_every: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        (val if idx % val_every == val_every - 1 else train).append(row)
    return train, val


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows), encoding="utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--eval-out", type=Path, default=DEFAULT_EVAL_OUT)
    parser.add_argument("--val-every", type=int, default=6)
    args = parser.parse_args()

    endpoint = deepseek_endpoint()
    started = time.time()
    previous_manifest = args.out_dir / "manifest.json"
    model = ""
    if previous_manifest.exists():
        with contextlib.suppress(Exception):
            model = str(json.loads(previous_manifest.read_text(encoding="utf-8")).get("model") or "")
    if not model:
        model = "deepseek-chat"
    raw: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    heldout: list[dict[str, Any]] = []
    seen_users: set[str] = set()

    for family in FAMILIES:
        needed = family["train_count"] + family["heldout_count"]
        generated: list[dict[str, Any]] = []
        valid: list[dict[str, Any]] = []
        cache_path = args.out_dir / f"raw_{family['name']}.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if family["name"] == "calendar_ambiguous_time_boundary":
            generated = deterministic_calendar_specs()
            valid = [item for item in generated if valid_spec(family["name"], item)]
            cache_path.write_text(
                json.dumps({"family": family["name"], "rows": generated, "source": "deterministic_schema_valid"}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        elif cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            generated = cached.get("rows", []) if isinstance(cached, dict) else []
            valid = [item for item in generated if valid_spec(family["name"], item)]
        for batch in range(1, 16):
            if len(valid) >= needed:
                break
            response = call_deepseek(endpoint, teacher_prompt(family, min(18, needed + 4), batch))
            model = response["model"]
            batch_rows = response["content"].get("rows", [])
            if isinstance(batch_rows, list):
                generated.extend(batch_rows)
                valid = [item for item in generated if valid_spec(family["name"], item)]
                cache_path.write_text(
                    json.dumps({"family": family["name"], "rows": generated}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        raw[family["name"]] = generated
        train_count = 0
        heldout_count = 0
        for item in valid:
            key = clean_text(item["user"]).lower()
            if key in seen_users:
                continue
            seen_users.add(key)
            if train_count < family["train_count"]:
                rows.append(build_sft_row(family["name"], train_count, item, "train_or_val"))
                train_count += 1
            elif heldout_count < family["heldout_count"]:
                heldout.append(build_eval_case(family["name"], heldout_count, item))
                heldout_count += 1
            if train_count >= family["train_count"] and heldout_count >= family["heldout_count"]:
                break
        if train_count < family["train_count"] or heldout_count < family["heldout_count"]:
            raise RuntimeError(
                f"{family['name']} valid rows short: train {train_count}/{family['train_count']}, "
                f"heldout {heldout_count}/{family['heldout_count']}"
            )

    train, val = split_rows(rows, args.val_every)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out_dir / "train.jsonl", train)
    write_jsonl(args.out_dir / "val.jsonl", val)
    write_jsonl(args.out_dir / "all.jsonl", rows)
    (args.out_dir / "raw_teacher.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    args.eval_out.parent.mkdir(parents=True, exist_ok=True)
    eval_payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": Path(__file__).name,
        "provider": endpoint["name"],
        "model": model,
        "source": "V53 DeepSeek-heldout live-gap failures",
        "cases": heldout,
    }
    args.eval_out.write_text(json.dumps(eval_payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "name": args.out_dir.name,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": endpoint["name"],
        "model": model,
        "elapsed_seconds": round(time.time() - started, 3),
        "total_sft_rows": len(rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "heldout_cases": len(heldout),
        "categories": {family["name"]: sum(1 for row in rows if row["metadata"]["category"] == family["name"]) for family in FAMILIES},
        "heldout_categories": {family["name"]: sum(1 for case in heldout if case["deepseek_family"] == family["name"]) for family in FAMILIES},
        "source_eval": "data/evals/ody_everyday_deepseek_heldout_v53_current_20260821_1508_dynamic_calendar_rescored/actual_results.json",
        "acceptance_target": (
            "Train as a narrow V54 top-up only after reviewing rows. Promote only if V54 passes live-hard, "
            "DeepSeek-heldout rescored cases, V54 live-gap heldout, and old CRUD regression."
        ),
        "files": {
            "train": str(args.out_dir / "train.jsonl"),
            "val": str(args.out_dir / "val.jsonl"),
            "all": str(args.out_dir / "all.jsonl"),
            "raw_teacher": str(args.out_dir / "raw_teacher.json"),
            "heldout_eval": str(args.eval_out),
        },
    }
    for key, value in list(manifest["files"].items()):
        manifest[f"{key}_sha256"] = file_sha256(Path(value))
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "out_dir": str(args.out_dir),
        "eval_out": str(args.eval_out),
        "total_sft_rows": len(rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "heldout_cases": len(heldout),
        "categories": manifest["categories"],
        "heldout_categories": manifest["heldout_categories"],
        "model": model,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
