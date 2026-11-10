#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib import request


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTUAL = REPO_ROOT / "data/evals/ody_search_teacher_pipeline_20260821/deepseek_actual/actual_results.json"
DEFAULT_OUT_DIR = Path(str(Path(__file__).resolve().parents[1] / "data" / "teacher_live_gaps" / "odysseus_v58_teacher_edited_search_traces_20260821"))

WEB_TOOLS = {"web_search", "web_fetch"}
SOURCE_DUMP_RE = re.compile(r"WEB SEARCH RESULTS|```sources|\b\d+\s+Web sources\b", re.IGNORECASE)
META_FINAL_RE = re.compile(r"\b(the user asked|the user is asking|tool evidence|i should answer)\b", re.IGNORECASE)

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


def call_json(base_url: str, api_key: str, model: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return strict JSON only. You are editing tool-use traces for SFT. "
                    "Do not include chain-of-thought or prose outside JSON."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": 0.25,
        "max_tokens": 2200,
        "response_format": {"type": "json_object"},
    }
    req = request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with request.urlopen(req, timeout=180) as resp:
        parsed = json.loads(resp.read().decode("utf-8"))
    text = str(parsed["choices"][0]["message"].get("content") or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    return json.loads(text)


def summarize_outputs(result: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = []
    for idx, output in enumerate(result.get("tool_outputs") or []):
        text = str(output.get("output") or "")
        outputs.append({
            "tool": output.get("tool"),
            "output_head": text[:1800],
            "output_tail": text[-800:] if len(text) > 1800 else "",
            "exit_code": output.get("exit_code"),
            "call_args": (result.get("tool_calls") or [{}])[idx].get("args") if idx < len(result.get("tool_calls") or []) else None,
        })
    return outputs


def needs_teacher_edit(result: dict[str, Any]) -> bool:
    final = str(result.get("final_answer") or "")
    tools = result.get("tool_names") or []
    failures = result.get("failures") or []
    if result.get("kind") != "web":
        return False
    if not tools or tools[0] != "web_search":
        return True
    if any(tool not in WEB_TOOLS for tool in tools):
        return True
    if len(tools) > 3:
        return True
    if SOURCE_DUMP_RE.search(final) or META_FINAL_RE.search(final):
        return True
    if len(final.split()) < 8:
        return True
    if failures:
        return True
    return False


def teacher_edit(endpoint: dict[str, str], result: dict[str, Any]) -> dict[str, Any]:
    prompt = {
        "task": "Edit this failed/weak Odysseus web tool trace into one minimal correct SFT trace.",
        "current_date": "2026-08-21",
        "user": result.get("user"),
        "prior_turns": result.get("prior_turns") or [],
        "actual_tool_calls": result.get("tool_calls") or [],
        "actual_tool_outputs": summarize_outputs(result),
        "actual_final": result.get("final_answer") or "",
        "failures": result.get("failures") or [],
        "requirements": [
            "Return JSON with should_train boolean, reason string, trace array, and final string.",
            "If the user request is evergreen/simple and should not search, set should_train=false.",
            "For search-worthy requests, trace must contain 1 to 3 tool steps.",
            "Each trace step must have tool, args, and output.",
            "Allowed tools are only web_search and web_fetch.",
            "web_search args must be an object like {\"query\":\"...\"}. The query must preserve the important nouns, requested property, location, time, and follow-up context.",
            "Use web_fetch only after a search when snippets are insufficient and include a plausible URL from the search evidence.",
            "The output field should be concise synthetic tool evidence, not a huge raw dump. It must contain enough evidence to justify the final.",
            "The final must answer directly in 1-4 sentences. No source dumps. No 'the user asked'.",
            "Do not hardcode this exact test; infer the general correct behavior from the request.",
        ],
    }
    return call_json(endpoint["base_url"], endpoint["api_key"], endpoint["model"], prompt)


def build_row(result: dict[str, Any], edited: dict[str, Any]) -> dict[str, Any] | None:
    if edited.get("should_train") is not True:
        return None
    trace = edited.get("trace")
    final = re.sub(r"\s+", " ", str(edited.get("final") or "")).strip()
    if not isinstance(trace, list) or not trace or len(trace) > 3:
        return None
    if not final or SOURCE_DUMP_RE.search(final) or META_FINAL_RE.search(final) or len(final) > 1200:
        return None
    messages: list[dict[str, Any]] = [{"role": "user", "content": result.get("user") or ""}]
    for idx, step in enumerate(trace):
        if not isinstance(step, dict):
            return None
        tool = str(step.get("tool") or "")
        if tool not in WEB_TOOLS:
            return None
        args = step.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"query": args} if tool == "web_search" else {"url": args}
        if tool == "web_search" and not str(args.get("query") or "").strip():
            return None
        if tool == "web_fetch" and not str(args.get("url") or "").strip():
            return None
        output = str(step.get("output") or "").strip()
        if not output or len(output) > 1800:
            output = output[:1800].rstrip()
        call_id = f"call_{result.get('id', 'trace')}_{idx}"
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args, separators=(",", ":"), ensure_ascii=True)},
            }],
        })
        messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
    messages.append({"role": "assistant", "content": final})
    row = {
        "messages": messages,
        "tools": TOOL_SCHEMAS,
        "generator": "odysseus_deepseek_teacher_edited_search_trace",
        "metadata": {
            "source_result_id": result.get("id"),
            "source_pass": result.get("pass"),
            "actual_tool_names": result.get("tool_names") or [],
            "teacher_reason": edited.get("reason") or "",
        },
    }
    row["uuid"] = stable_id("ody_v58_teacher_edited_search", row)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual", type=Path, default=DEFAULT_ACTUAL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_TEACHER_MODEL", "deepseek-chat"))
    parser.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    parser.add_argument("--max-cases", type=int, default=120)
    args = parser.parse_args()
    if not args.api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    payload = json.loads(args.actual.read_text(encoding="utf-8"))
    endpoint = {"base_url": args.base_url, "api_key": args.api_key, "model": args.model}
    candidates = [result for result in payload.get("results") or [] if needs_teacher_edit(result)]
    candidates = candidates[: args.max_cases]
    rows: list[dict[str, Any]] = []
    edits: list[dict[str, Any]] = []
    for result in candidates:
        try:
            edited = teacher_edit(endpoint, result)
            row = build_row(result, edited)
            accepted = row is not None
            if accepted:
                rows.append(row)
            edits.append({
                "id": result.get("id"),
                "user": result.get("user"),
                "accepted": accepted,
                "actual_tool_names": result.get("tool_names") or [],
                "actual_final": result.get("final_answer") or "",
                "edited": edited,
            })
        except Exception as exc:
            edits.append({"id": result.get("id"), "user": result.get("user"), "accepted": False, "error": repr(exc)})
        print(json.dumps({"processed": len(edits), "accepted": len(rows), "id": result.get("id")}), flush=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        (val if idx % 8 == 7 else train).append(row)
    for name, subset in [("all.jsonl", rows), ("train.jsonl", train), ("val.jsonl", val)]:
        (args.out_dir / name).write_text("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in subset), encoding="utf-8")
    (args.out_dir / "edits.json").write_text(json.dumps({"edits": edits}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_actual_results": str(args.actual),
        "candidate_cases": len(candidates),
        "accepted_sft_rows": len(rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "allowed_tools": sorted(WEB_TOOLS),
        "files": {
            "train": str(args.out_dir / "train.jsonl"),
            "val": str(args.out_dir / "val.jsonl"),
            "all": str(args.out_dir / "all.jsonl"),
            "edits": str(args.out_dir / "edits.json"),
        },
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
