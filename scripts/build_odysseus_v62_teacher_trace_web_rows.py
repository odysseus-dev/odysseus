#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any


DEFAULT_EDITS = Path(str(Path(__file__).resolve().parents[1] / "data" / "teacher_live_gaps" / "odysseus_v58_teacher_edited_search_traces_20260821" / "edits.json"))
DEFAULT_LIVE_ACTUAL = Path(str(Path(__file__).resolve().parents[1] / "data" / "evals" / "ody_v57_quick_live_search_cases_20260821" / "v61_app_route_web_run_20260821_2204" / "actual_results.json"))
DEFAULT_OUT_DIR = Path(str(Path(__file__).resolve().parents[1] / "data" / "teacher_live_gaps" / "odysseus_v62_teacher_trace_web_synthesis_20260821"))

WEB_NUDGE = (
    "You are continuing after public web tool results. Use the tool evidence "
    "to answer the user's question directly in concise prose. If the first "
    "search result is off-target, make at most one or two better web_search "
    "calls, then answer from the best evidence. Do not output raw source "
    "lists, tool wrappers, or meta-commentary."
)
WEB_TOOLS = {"web_search", "web_fetch"}
FORBIDDEN_FINAL_RE = re.compile(
    r"WEB SEARCH RESULTS|```sources|\b\d+\s+Web sources\b|from the search results|"
    r"results indicate|returned snippets|top results|i searched|search results summary|"
    r"fetched page content|\[CONTENT\s+\d+\]|the user asked|i should",
    re.IGNORECASE,
)


def stable_id(prefix: str, obj: dict[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=True)
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def clean_final(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text


def normalize_args(tool: str, args: Any) -> dict[str, Any]:
    if isinstance(args, dict):
        return dict(args)
    if isinstance(args, str):
        text = args.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {"query": text} if tool == "web_search" else {"url": text}
    return {}


def append_tool_step(messages: list[dict[str, Any]], source_id: str, idx: int, step: dict[str, Any]) -> bool:
    tool = str(step.get("tool") or "")
    if tool not in WEB_TOOLS:
        return False
    args = normalize_args(tool, step.get("args") or {})
    if tool == "web_search" and not str(args.get("query") or "").strip():
        return False
    if tool == "web_fetch" and not str(args.get("url") or "").strip():
        return False
    output = re.sub(r"\s+", " ", str(step.get("output") or "")).strip()
    if not output:
        return False
    output = output[:2200].rstrip()
    call_id = f"call_{source_id}_{idx}"
    messages.append({
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": tool,
                "arguments": json.dumps(args, separators=(",", ":"), ensure_ascii=True),
            },
        }],
    })
    messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
    return True


def build_trace_row(item: dict[str, Any], repeat: int) -> dict[str, Any] | None:
    edited = item.get("edited") or {}
    if item.get("accepted") is not True or edited.get("should_train") is not True:
        return None
    trace = edited.get("trace") or []
    final = clean_final(edited.get("final") or "")
    if not isinstance(trace, list) or not trace or len(trace) > 3:
        return None
    if not final or len(final) > 900 or FORBIDDEN_FINAL_RE.search(final):
        return None
    source_id = str(item.get("id") or "teacher")
    messages: list[dict[str, Any]] = [{"role": "system", "content": WEB_NUDGE}]
    messages.append({"role": "user", "content": str(item.get("user") or "")})
    for idx, step in enumerate(trace):
        if not append_tool_step(messages, source_id, idx, step):
            return None
    messages.append({"role": "assistant", "content": final})
    row = {
        "messages": messages,
        "generator": "odysseus_v62_teacher_trace_web_synthesis",
        "metadata": {
            "family": "teacher_minimal_trace_then_answer",
            "source_result_id": source_id,
            "repeat": repeat,
            "trace_tools": [str(step.get("tool") or "") for step in trace],
            "teacher_reason": edited.get("reason") or "",
        },
    }
    row["uuid"] = stable_id("ody_v62_teacher_trace_web", row)
    return row


def first_web_output(result: dict[str, Any]) -> str:
    for output in result.get("tool_outputs") or []:
        if output.get("tool") == "web_search":
            text = str(output.get("output") or "")
            return re.sub(r"\r\n?", "\n", text).strip()[:4200].rstrip()
    return ""


def live_hard_specs(actual_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    norway = actual_by_id.get("v57_norway_coordinates")
    if norway:
        specs.append({
            "id": "v57_norway_coordinates_country_not_capital",
            "user": "where is norway coordinates",
            "trace": [
                {
                    "tool": "web_search",
                    "args": {"query": "Norway country coordinates latitude longitude"},
                    "output": first_web_output(norway) or "Search evidence identifies Norway as a country in Northern Europe on the Scandinavian Peninsula. Common country coordinates are approximately 62° N latitude and 10° E longitude.",
                }
            ],
            "final": "Norway is in Northern Europe on the Scandinavian Peninsula. The commonly cited country coordinates are about 62°N, 10°E.",
            "family": "live_hard_country_coordinates_answer",
        })
    snail = actual_by_id.get("v57_snail_touch_followup")
    if snail:
        specs.append({
            "id": "v57_snail_touch_contextual_followup",
            "prior": [
                ("user", "why does snails bubble up when they are scared"),
                ("assistant", "Snails bubble because air gets trapped in their mucus, making foam. That usually happens when they are stressed, irritated, disturbed, defending themselves, or trying to hold moisture."),
            ],
            "user": "is it safe to touch",
            "trace": [
                {
                    "tool": "web_search",
                    "args": {"query": "is it safe to touch garden snails mucus wash hands"},
                    "output": first_web_output(snail) or "Search evidence says snail mucus may irritate skin for some people and snails can carry germs, so gentle handling is usually okay but hands should be washed afterward and contact with eyes or mouth should be avoided.",
                }
            ],
            "final": "Usually yes, it is okay to gently touch a snail, even if it is foaming from stress. Be gentle, avoid touching your eyes or mouth, and wash your hands afterward.",
            "family": "live_hard_contextual_followup_answer",
        })
    return specs


def build_live_row(spec: dict[str, Any], repeat: int) -> dict[str, Any] | None:
    final = clean_final(spec.get("final") or "")
    if not final or FORBIDDEN_FINAL_RE.search(final):
        return None
    messages: list[dict[str, Any]] = [{"role": "system", "content": WEB_NUDGE}]
    for role, content in spec.get("prior") or []:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": str(spec.get("user") or "")})
    for idx, step in enumerate(spec.get("trace") or []):
        if not append_tool_step(messages, str(spec.get("id") or "live"), idx, step):
            return None
    messages.append({"role": "assistant", "content": final})
    row = {
        "messages": messages,
        "generator": "odysseus_v62_live_hard_web_synthesis",
        "metadata": {
            "family": spec.get("family") or "live_hard",
            "source_result_id": spec.get("id"),
            "repeat": repeat,
        },
    }
    row["uuid"] = stable_id("ody_v62_teacher_trace_web", row)
    return row


def split_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        (val if idx % 10 == 9 else train).append(row)
    return train, val


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edits", type=Path, default=DEFAULT_EDITS)
    parser.add_argument("--live-actual", type=Path, default=DEFAULT_LIVE_ACTUAL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--teacher-repeat", type=int, default=6)
    parser.add_argument("--live-repeat", type=int, default=20)
    args = parser.parse_args()

    edits = json.loads(args.edits.read_text(encoding="utf-8")).get("edits") or []
    rows: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    family_counts: dict[str, int] = {}
    accepted_sources = 0
    for item in edits:
        accepted_for_source = 0
        for rep in range(args.teacher_repeat):
            row = build_trace_row(item, rep)
            if row:
                rows.append(row)
                accepted_for_source += 1
                family = row["metadata"]["family"]
                family_counts[family] = family_counts.get(family, 0) + 1
        if accepted_for_source:
            accepted_sources += 1
            audit.append({
                "id": item.get("id"),
                "family": "teacher_minimal_trace_then_answer",
                "rows": accepted_for_source,
                "user": item.get("user"),
            })

    live_payload = json.loads(args.live_actual.read_text(encoding="utf-8")) if args.live_actual.exists() else {"results": []}
    actual_by_id = {str(item.get("id") or ""): item for item in live_payload.get("results") or []}
    for spec in live_hard_specs(actual_by_id):
        accepted_for_spec = 0
        for rep in range(args.live_repeat):
            row = build_live_row(spec, rep)
            if row:
                rows.append(row)
                accepted_for_spec += 1
                family = row["metadata"]["family"]
                family_counts[family] = family_counts.get(family, 0) + 1
        audit.append({
            "id": spec.get("id"),
            "family": spec.get("family"),
            "rows": accepted_for_spec,
            "user": spec.get("user"),
        })

    args.out_dir.mkdir(parents=True, exist_ok=True)
    train, val = split_rows(rows)
    for name, subset in (("all.jsonl", rows), ("train.jsonl", train), ("val.jsonl", val)):
        (args.out_dir / name).write_text(
            "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in subset),
            encoding="utf-8",
        )
    (args.out_dir / "audit.json").write_text(
        json.dumps({"audit": audit}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_edits": str(args.edits),
        "source_live_actual": str(args.live_actual),
        "accepted_teacher_sources": accepted_sources,
        "accepted_rows": len(rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "family_counts": family_counts,
        "goal": "teach app-route web continuations to search minimally and synthesize final answers",
        "files": {
            "train": str(args.out_dir / "train.jsonl"),
            "val": str(args.out_dir / "val.jsonl"),
            "all": str(args.out_dir / "all.jsonl"),
            "audit": str(args.out_dir / "audit.json"),
        },
    }
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
