#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_OUT = Path(str(Path(__file__).resolve().parents[1] / "data" / "teacher_web_synthesis" / "odysseus_web_teacher_v1_20260821"))
DEFAULT_EVAL_OUT = REPO_ROOT / "data/evals/ody_web_teacher_heldout_v1_20260821/cases.json"

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current or source-backed information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "time_filter": {"type": "string", "enum": ["day", "week", "month", "year"]},
            },
            "required": ["query"],
        },
    },
}


FAMILIES: list[dict[str, Any]] = [
    {
        "name": "web_direct_answer",
        "train_count": 45,
        "heldout_count": 18,
        "instruction": (
            "User asks to look up a public fact, explanation, price, exchange rate, product safety issue, "
            "local cost, regulation, or simple science reason. The ideal first tool is web_search with a "
            "specific query. After tool output, assistant synthesizes a short answer, never just links."
        ),
    },
    {
        "name": "web_bad_first_search_recovery",
        "train_count": 30,
        "heldout_count": 12,
        "instruction": (
            "The first web_search result is low evidence or wrong-intent dictionary/news noise. The ideal next "
            "assistant action is a second web_search with better terms; final answer synthesizes only after useful evidence."
        ),
    },
    {
        "name": "web_unit_conversion",
        "train_count": 25,
        "heldout_count": 10,
        "instruction": (
            "User asks for a looked-up price/rate converted into another unit or currency. The answer should show "
            "the approximate calculation using evidence in the simulated search result."
        ),
    },
    {
        "name": "web_no_tool_boundary",
        "train_count": 10,
        "heldout_count": 5,
        "instruction": (
            "User explicitly says not to search, or asks a stable definition/concept. The assistant should answer directly "
            "with no tool call."
        ),
    },
    {
        "name": "web_search_failure",
        "train_count": 10,
        "heldout_count": 5,
        "instruction": (
            "Search results remain irrelevant or insufficient after reasonable query terms. The final answer should say "
            "there is not enough clear evidence, not dump source listings."
        ),
    },
]


def stable_id(prefix: str, obj: dict[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=True)
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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
    if db_path.exists():
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
            if row:
                return {
                    "name": row["name"],
                    "base_url": row["base_url"],
                    "api_key": row["api_key"],
                    "cached_models": row["cached_models"] or "",
                }
        finally:
            conn.close()

        auth_path = REPO_ROOT / "data/auth.json"
        if auth_path.exists():
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            endpoints = auth.get("model_endpoints") or auth.get("providers") or []
            for item in endpoints if isinstance(endpoints, list) else []:
                name = str(item.get("name") or item.get("provider") or "").lower()
                api_key = str(item.get("api_key") or item.get("apiKey") or "").strip()
                if "deepseek" in name and api_key:
                    return {
                        "name": name,
                        "base_url": item.get("base_url") or item.get("baseUrl") or "https://api.deepseek.com/v1",
                        "api_key": api_key,
                        "cached_models": item.get("cached_models") or item.get("model") or "deepseek-chat",
                    }

    raise RuntimeError("no enabled DeepSeek endpoint with API key and DEEPSEEK_API_KEY is unset")


def call_deepseek(endpoint: dict[str, str], prompt: dict[str, Any], max_tokens: int = 8000) -> dict[str, Any]:
    model = "deepseek-chat"
    try:
        cached = json.loads(endpoint["cached_models"] or "[]")
        if cached:
            model = cached[0]
    except json.JSONDecodeError:
        if endpoint.get("cached_models"):
            model = endpoint["cached_models"]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return strict JSON only. No markdown, no commentary."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0.7,
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
    name = family["name"]
    return {
        "task": "Generate Odysseus web-search tool-use SFT specs.",
        "current_date_context": "2026-08-21. Use Asia/Tokyo examples when a relative date matters.",
        "family": name,
        "count": count,
        "batch": batch,
        "family_instruction": family["instruction"],
        "global_requirements": [
            "Return JSON object with key rows: list.",
            "Return exactly count rows.",
            "Every row needs: user, ideal_query, evidence, final, query_must_include, answer_must_include.",
            "For web_no_tool_boundary rows, ideal_query must be empty string and evidence must be empty string.",
            "For web_bad_first_search_recovery rows, include bad_query and bad_evidence, then ideal_query/evidence/final.",
            "For web_search_failure rows, evidence should be irrelevant or insufficient and final should say not enough clear evidence.",
            "Do not include private names, private email data, or secrets.",
            "Do not copy these instructions verbatim.",
            "Use varied wording, typos, casual phrasing, and realistic user questions.",
            "Make each user prompt unique from prior batches; vary topic, country, unit, and wording.",
            "Do not make rows depend on exact live facts; simulated evidence is okay for behavior training.",
            "Final answers must synthesize evidence in 1-4 sentences, with no raw source dump and no markdown source block.",
        ],
        "examples_to_cover_without_copying": [
            "look up why a small animal is foaming/bubbling and explain",
            "current commodity price per liter converted to EUR",
            "why a device battery swells and what to do",
            "why a food starter smells like acetone",
            "latest/current exchange rate with a rough conversion",
            "bad query returns dictionary pages, then better search terms are needed",
        ],
    }


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_terms(value: Any) -> list[str]:
    if isinstance(value, str):
        text = clean_text(value)
        return [text] if text else []
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    return []


def alternatives(term: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,/|]|\bor\b", term) if part.strip()] or [term]


def valid_spec(family: str, item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    user = clean_text(item.get("user"))
    final = clean_text(item.get("final"))
    if len(user.split()) < 4 or len(user) > 220:
        return False
    if "WEB SEARCH RESULTS" in final or "```sources" in final or "Here are links" in final:
        return False
    if family == "web_no_tool_boundary":
        return bool(final) and not clean_text(item.get("ideal_query"))
    if not clean_text(item.get("ideal_query")):
        return False
    if family == "web_bad_first_search_recovery" and not clean_text(item.get("bad_query")):
        return False
    return bool(final)


def build_sft_row(family: str, idx: int, spec: dict[str, Any], split: str) -> dict[str, Any]:
    user = clean_text(spec["user"])
    final = clean_text(spec["final"])
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    expected_calls = 0

    if family == "web_no_tool_boundary":
        messages.append({"role": "assistant", "content": final})
    elif family == "web_bad_first_search_recovery":
        bad_call = tool_call("web_search", {"query": clean_text(spec["bad_query"])}, f"{family}_{idx}_bad")
        good_call = tool_call("web_search", {"query": clean_text(spec["ideal_query"])}, f"{family}_{idx}_good")
        messages.extend(
            [
                {"role": "assistant", "content": "", "tool_calls": [bad_call]},
                {
                    "role": "tool",
                    "tool_call_id": bad_call["id"],
                    "content": clean_text(spec.get("bad_evidence"))
                    or "Search results were mostly dictionary pages and did not answer the user's question.",
                },
                {"role": "assistant", "content": "", "tool_calls": [good_call]},
                {
                    "role": "tool",
                    "tool_call_id": good_call["id"],
                    "content": clean_text(spec.get("evidence")),
                },
                {"role": "assistant", "content": final},
            ]
        )
        expected_calls = 2
    else:
        call = tool_call("web_search", {"query": clean_text(spec["ideal_query"])}, f"{family}_{idx}")
        messages.extend(
            [
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {"role": "tool", "tool_call_id": call["id"], "content": clean_text(spec.get("evidence"))},
                {"role": "assistant", "content": final},
            ]
        )
        expected_calls = 1

    row = {
        "messages": messages,
        "tools": [] if family == "web_no_tool_boundary" else [WEB_SEARCH_TOOL],
        "generator": "deepseek_teacher_web_synthesis_v1",
        "metadata": {
            "category": family,
            "split": split,
            "expected_tool_calls": expected_calls,
            "query_must_include": clean_terms(spec.get("query_must_include")),
            "answer_must_include": clean_terms(spec.get("answer_must_include")),
        },
    }
    row["uuid"] = stable_id("ody_web_teacher", row)
    return row


def build_eval_case(family: str, idx: int, spec: dict[str, Any]) -> dict[str, Any]:
    user = clean_text(spec["user"])
    case: dict[str, Any] = {
        "id": f"teacher_web_{family}_{idx:02d}",
        "kind": "negative_web" if family == "web_no_tool_boundary" else "web",
        "user": user,
        "deepseek_family": family,
        "forbidden_final": ["WEB SEARCH RESULTS", "```sources", "Here are links for that topic"],
    }
    answer_terms = clean_terms(spec.get("answer_must_include"))
    query_terms = clean_terms(spec.get("query_must_include"))
    if family == "web_no_tool_boundary":
        case.update({"expect_no_tool": True, "forbidden_tools": ["web_search", "web_fetch"]})
    else:
        case.update(
            {
                "expect_first_tool": "web_search",
                "forbidden_query_any": ["official links", "dictionary", "wikipedia official", "cambridge", "merriam"],
            }
        )
        for i, term in enumerate(query_terms[:4], start=1):
            key = "must_query_any" if i == 1 else f"must_query_any_{i}"
            case[key] = alternatives(term)
        if family == "web_bad_first_search_recovery":
            case["min_web_searches"] = 2
        else:
            case["max_web_searches"] = 1
    for i, term in enumerate(answer_terms[:2], start=1):
        key = "must_answer_any" if i == 1 else f"must_answer_any_{i}"
        case[key] = alternatives(term)
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
    raw: dict[str, Any] = {}
    sft_rows: list[dict[str, Any]] = []
    eval_cases: list[dict[str, Any]] = []
    seen_users: set[str] = set()
    model = ""

    for family in FAMILIES:
        needed = family["train_count"] + family["heldout_count"]
        generated: list[dict[str, Any]] = []
        valid: list[dict[str, Any]] = []
        cache_path = args.out_dir / f"raw_{family['name']}.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            generated = cached.get("rows", []) if isinstance(cached, dict) else []
            valid = [item for item in generated if valid_spec(family["name"], item)]
        for batch in range(1, 25):
            if len(valid) >= needed + 6:
                break
            response = call_deepseek(endpoint, teacher_prompt(family, min(20, needed + 8), batch))
            model = response["model"]
            batch_rows = response["content"].get("rows", [])
            if isinstance(batch_rows, list):
                generated.extend(batch_rows)
                valid = [item for item in generated if valid_spec(family["name"], item)]
                cache_path.write_text(
                    json.dumps({"family": family["name"], "rows": generated}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            if len(valid) >= needed:
                break
        raw[family["name"]] = generated
        picked_train = 0
        picked_eval = 0
        for item in valid:
            user_key = clean_text(item["user"]).lower()
            if user_key in seen_users:
                continue
            seen_users.add(user_key)
            if picked_train < family["train_count"]:
                sft_rows.append(build_sft_row(family["name"], picked_train, item, "train_or_val"))
                picked_train += 1
            elif picked_eval < family["heldout_count"]:
                eval_cases.append(build_eval_case(family["name"], picked_eval, item))
                picked_eval += 1
            if picked_train >= family["train_count"] and picked_eval >= family["heldout_count"]:
                break
        if picked_train < family["train_count"] or picked_eval < family["heldout_count"]:
            raise RuntimeError(
                f"family {family['name']} generated only train={picked_train}/{family['train_count']} "
                f"heldout={picked_eval}/{family['heldout_count']} valid rows"
            )

    train, val = split_rows(sft_rows, args.val_every)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out_dir / "train.jsonl", train)
    write_jsonl(args.out_dir / "val.jsonl", val)
    write_jsonl(args.out_dir / "all.jsonl", sft_rows)
    (args.out_dir / "raw_teacher.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    args.eval_out.parent.mkdir(parents=True, exist_ok=True)
    eval_payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "build_odysseus_web_teacher_sft.py",
        "provider": "DeepSeek",
        "model": model,
        "source": "teacher-generated behavioral specs from user-reported web synthesis failures",
        "cases": eval_cases,
    }
    args.eval_out.write_text(json.dumps(eval_payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "name": args.out_dir.name,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": "DeepSeek",
        "model": model,
        "elapsed_seconds": round(time.time() - started, 3),
        "total_sft_rows": len(sft_rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "heldout_cases": len(eval_cases),
        "categories": {
            family["name"]: sum(1 for row in sft_rows if row["metadata"]["category"] == family["name"])
            for family in FAMILIES
        },
        "heldout_categories": {
            family["name"]: sum(1 for case in eval_cases if case["deepseek_family"] == family["name"])
            for family in FAMILIES
        },
        "acceptance_target": (
            "Promote only if teacher web heldout passes 50/50, user live web prompts synthesize answers instead of raw links, "
            "and old CRUD suites remain regression-clean."
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
        "total_sft_rows": len(sft_rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "heldout_cases": len(eval_cases),
        "model": model,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
