#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib import request


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTUALS = [
    REPO_ROOT / "data/evals/ody_search_teacher_pipeline_20260821/deepseek_actual/actual_results.json",
    REPO_ROOT / "data/evals/ody_v57_quick_live_search_cases_20260821/v59_run_20260821_2042/actual_results.json",
]
DEFAULT_OUT_DIR = Path(str(Path(__file__).resolve().parents[1] / "data" / "teacher_live_gaps" / "odysseus_v60_realistic_verbose_web_synthesis_20260821"))

WEB_TOOLS = {"web_search", "web_fetch"}
FORBIDDEN_FINAL_RE = re.compile(
    r"WEB SEARCH RESULTS|```sources|\b\d+\s+Web sources\b|from the search results|results indicate|returned snippets|top results|i searched",
    re.IGNORECASE,
)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web for source-backed information.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a specific URL when search snippets do not contain enough evidence.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
]


def stable_id(prefix: str, obj: dict[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=True)
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_teacher_endpoint(db_path: Path, model: str | None) -> dict[str, str]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT base_url, api_key, cached_models
            FROM model_endpoints
            WHERE is_enabled = 1
              AND api_key IS NOT NULL
              AND api_key != ''
              AND (lower(name) LIKE '%deepseek%' OR lower(id) LIKE '%deepseek%')
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError("no enabled DeepSeek endpoint with API key found in app DB")
    selected_model = model
    if not selected_model:
        cached = json.loads(row["cached_models"] or "[]")
        selected_model = cached[0] if cached else "deepseek-v4-flash"
    return {"base_url": row["base_url"], "api_key": row["api_key"], "model": selected_model}


def call_json(endpoint: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    body = {
        "model": endpoint["model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return strict JSON only. You are creating SFT final answers for web tool traces. "
                    "Do not include chain-of-thought or prose outside JSON."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": 0.2,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
    }
    req = request.Request(
        endpoint["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {endpoint['api_key']}"},
        method="POST",
    )
    with request.urlopen(req, timeout=180) as resp:
        parsed = json.loads(resp.read().decode("utf-8"))
    text = str(parsed["choices"][0]["message"].get("content") or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    return json.loads(text)


def normalize_args(tool: str, args: Any) -> dict[str, Any]:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        stripped = args.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {"query": stripped} if tool == "web_search" else {"url": stripped}
    return {}


def compact_tool_output(text: str, max_chars: int = 3000) -> str:
    text = re.sub(r"\r\n?", "\n", text or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= max_chars:
        return text
    sources = ""
    if text.startswith("```sources"):
        end = text.find("```", 3)
        if end != -1:
            sources = text[: end + 3].strip()
    summary_match = re.search(r"SEARCH RESULTS SUMMARY:\n[-]+\n(?P<body>.*?)(?:\n={10,}|\Z)", text, re.DOTALL)
    summary = summary_match.group("body").strip() if summary_match else ""
    fetched_match = re.search(r"FETCHED PAGE CONTENT:\n[-]+\n(?P<body>.*?)(?:\n={10,}|\Z)", text, re.DOTALL)
    fetched = fetched_match.group("body").strip() if fetched_match else ""
    chunks = [chunk for chunk in [sources, summary[:1600], fetched[:900]] if chunk]
    compact = "\n\n".join(chunks).strip()
    if not compact:
        compact = text[:max_chars].rstrip()
    return compact[:max_chars].rstrip()


def load_results(paths: list[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for result in payload.get("results") or []:
            key = f"{path}:{result.get('id')}"
            if key in seen:
                continue
            seen.add(key)
            result = dict(result)
            result["_source_path"] = str(path)
            out.append(result)
    return out


def load_teacher_finals(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    finals: dict[str, str] = {}
    for item in payload.get("edits") or []:
        if not item.get("accepted"):
            continue
        edited = item.get("edited") or {}
        final = str(edited.get("final") or "").strip()
        if final and not FORBIDDEN_FINAL_RE.search(final):
            finals[str(item.get("id"))] = final
    return finals


def usable_web_steps(result: dict[str, Any], max_tools: int) -> list[dict[str, Any]]:
    calls = result.get("tool_calls") or []
    outputs = result.get("tool_outputs") or []
    steps: list[dict[str, Any]] = []
    for idx, call in enumerate(calls):
        tool = call.get("tool") or call.get("name")
        if tool not in WEB_TOOLS:
            continue
        if idx >= len(outputs):
            continue
        output = outputs[idx]
        if output.get("tool") and output.get("tool") not in WEB_TOOLS:
            continue
        args = normalize_args(tool, call.get("args"))
        if tool == "web_search" and not args.get("query"):
            continue
        if tool == "web_fetch" and not args.get("url"):
            continue
        content = compact_tool_output(str(output.get("output") or ""))
        if not content:
            continue
        steps.append({"tool": tool, "args": args, "output": content})
        if len(steps) >= max_tools:
            break
    return steps


def teacher_final(endpoint: dict[str, str], result: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = {
        "task": "Write the assistant's final answer after these web tool calls.",
        "current_date": "2026-08-21",
        "user": result.get("user") or "",
        "prior_turns": result.get("prior_turns") or [],
        "tool_steps": steps,
        "bad_actual_final": result.get("final_answer") or "",
        "requirements": [
            "Return JSON with should_train boolean, final string, and reason string.",
            "Use the tool evidence to answer the user's actual question directly.",
            "If snippets are insufficient for a precise value, say the best supported answer and the uncertainty briefly.",
            "Do not say 'from the search results', 'results indicate', 'snippets', 'I searched', or list sources.",
            "Do not copy raw snippets. Synthesize.",
            "Keep the final to 1-4 short sentences.",
            "If this request should not have searched, set should_train=false.",
        ],
    }
    return call_json(endpoint, prompt)


def build_row(result: dict[str, Any], steps: list[dict[str, Any]], final: str) -> dict[str, Any] | None:
    final = re.sub(r"\s+", " ", final).strip()
    if not final or len(final) > 900 or FORBIDDEN_FINAL_RE.search(final):
        return None
    messages: list[dict[str, Any]] = []
    for turn in result.get("prior_turns") or []:
        if isinstance(turn, dict) and turn.get("user"):
            messages.append({"role": "user", "content": str(turn["user"])})
            if turn.get("assistant"):
                messages.append({"role": "assistant", "content": str(turn["assistant"])})
    messages.append({"role": "user", "content": result.get("user") or ""})
    for idx, step in enumerate(steps):
        call_id = f"call_{result.get('id', 'web')}_{idx}"
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": step["tool"],
                    "arguments": json.dumps(step["args"], separators=(",", ":"), ensure_ascii=True),
                },
            }],
        })
        messages.append({"role": "tool", "tool_call_id": call_id, "content": step["output"]})
    messages.append({"role": "assistant", "content": final})
    row = {
        "messages": messages,
        "tools": TOOL_SCHEMAS,
        "generator": "odysseus_realistic_verbose_web_synthesis_teacher",
        "metadata": {
            "source_result_id": result.get("id"),
            "source_path": result.get("_source_path"),
            "source_pass": result.get("pass"),
            "actual_final": result.get("final_answer") or "",
        },
    }
    row["uuid"] = stable_id("ody_v60_realistic_web_synthesis", row)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual", type=Path, action="append", default=[])
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--db", type=Path, default=REPO_ROOT / "data/app.db")
    parser.add_argument("--teacher-model", default="")
    parser.add_argument("--teacher-edits", type=Path)
    parser.add_argument("--max-cases", type=int, default=180)
    parser.add_argument("--max-tools", type=int, default=3)
    args = parser.parse_args()

    paths = args.actual or DEFAULT_ACTUALS
    final_by_id = load_teacher_finals(args.teacher_edits)
    endpoint = None if final_by_id else load_teacher_endpoint(args.db, args.teacher_model or None)
    results = load_results(paths)
    candidates = []
    for result in results:
        if result.get("kind") != "web":
            continue
        steps = usable_web_steps(result, args.max_tools)
        if steps and steps[0]["tool"] == "web_search":
            candidates.append((result, steps))
    candidates = candidates[: args.max_cases]

    rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for result, steps in candidates:
        try:
            if result.get("id") in final_by_id:
                edited = {
                    "should_train": True,
                    "final": final_by_id[str(result.get("id"))],
                    "reason": "reused existing teacher-edited final",
                }
            else:
                assert endpoint is not None
                edited = teacher_final(endpoint, result, steps)
            row = None
            if edited.get("should_train") is True:
                row = build_row(result, steps, str(edited.get("final") or ""))
            accepted = row is not None
            if accepted:
                rows.append(row)
            audits.append({
                "id": result.get("id"),
                "source_path": result.get("_source_path"),
                "accepted": accepted,
                "tool_count": len(steps),
                "actual_final": result.get("final_answer") or "",
                "teacher": edited,
            })
        except Exception as exc:
            audits.append({"id": result.get("id"), "source_path": result.get("_source_path"), "accepted": False, "error": repr(exc)})
        print(json.dumps({"processed": len(audits), "accepted": len(rows), "id": result.get("id")}), flush=True)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        (val if idx % 10 == 9 else train).append(row)
    for name, subset in [("all.jsonl", rows), ("train.jsonl", train), ("val.jsonl", val)]:
        (args.out_dir / name).write_text("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in subset), encoding="utf-8")
    (args.out_dir / "audit.json").write_text(json.dumps({"audit": audits}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_actuals": [str(path) for path in paths],
        "candidate_cases": len(candidates),
        "accepted_sft_rows": len(rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "max_tools": args.max_tools,
        "goal": "train direct synthesis after realistic verbose web_search/web_fetch outputs",
        "files": {
            "train": str(args.out_dir / "train.jsonl"),
            "val": str(args.out_dir / "val.jsonl"),
            "all": str(args.out_dir / "all.jsonl"),
            "audit": str(args.out_dir / "audit.json"),
        },
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
