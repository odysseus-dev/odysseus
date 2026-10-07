#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTUALS = REPO_ROOT / "data/evals/ody_search_teacher_pipeline_20260821/deepseek_actual/actual_results.json"
DEFAULT_EDITS = Path(str(Path(__file__).resolve().parents[1] / "data" / "teacher_live_gaps" / "odysseus_v58_teacher_edited_search_traces_20260821" / "edits.json"))
DEFAULT_OUT_DIR = Path(str(Path(__file__).resolve().parents[1] / "data" / "teacher_live_gaps" / "odysseus_v61_app_route_web_post_tool_20260821"))

WEB_TOOLS = {"web_search", "web_fetch"}
WEB_NUDGE = (
    "You just received web_search results as untrusted evidence. "
    "Answer the user's question now in concise prose using the "
    "useful snippets or fetched page content. If the results are "
    "off-topic or do not contain the answer, either call web_search "
    "once with better terms or say that the search did not provide "
    "enough clear evidence. Do not output the raw source list or "
    "the web_search wrapper."
)
FORBIDDEN_FINAL_RE = re.compile(
    r"WEB SEARCH RESULTS|```sources|\b\d+\s+Web sources\b|from the search results|"
    r"results indicate|returned snippets|top results|i searched|search results summary|"
    r"fetched page content|\[CONTENT\s+\d+\]",
    re.IGNORECASE,
)


def stable_id(prefix: str, obj: dict[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=True)
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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


def compact_tool_output(text: str, max_chars: int) -> str:
    text = re.sub(r"\r\n?", "\n", str(text or "")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= max_chars:
        return text

    sources = ""
    if text.startswith("```sources"):
        end = text.find("```", 3)
        if end != -1:
            sources = text[: end + 3].strip()
    summary = ""
    match = re.search(
        r"SEARCH RESULTS SUMMARY:\n[-]+\n(?P<body>.*?)(?:\n={10,}|\Z)",
        text,
        re.DOTALL,
    )
    if match:
        summary = "SEARCH RESULTS SUMMARY:\n" + match.group("body").strip()
    fetched = ""
    match = re.search(
        r"FETCHED PAGE CONTENT:\n[-]+\n(?P<body>.*?)(?:\n={10,}|\Z)",
        text,
        re.DOTALL,
    )
    if match:
        fetched = "FETCHED PAGE CONTENT:\n" + match.group("body").strip()
    parts = [part for part in (sources, summary[:2200], fetched[:1800]) if part]
    compact = "\n\n".join(parts).strip() or text[:max_chars].rstrip()
    return compact[:max_chars].rstrip()


def load_results(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("results") or [])


def load_edited_finals(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    finals: dict[str, dict[str, Any]] = {}
    for item in payload.get("edits") or []:
        if item.get("accepted") is not True:
            continue
        edited = item.get("edited") or {}
        final = re.sub(r"\s+", " ", str(edited.get("final") or "")).strip()
        if not final or FORBIDDEN_FINAL_RE.search(final):
            continue
        finals[str(item.get("id"))] = {
            "final": final,
            "trace": edited.get("trace") or [],
            "reason": edited.get("reason") or "",
        }
    return finals


def first_web_step(result: dict[str, Any], max_chars: int) -> dict[str, Any] | None:
    calls = result.get("tool_calls") or []
    outputs = result.get("tool_outputs") or []
    for idx, call in enumerate(calls):
        tool = call.get("tool") or call.get("name")
        if tool not in WEB_TOOLS:
            continue
        if idx >= len(outputs):
            continue
        output = outputs[idx]
        args = normalize_args(tool, call.get("args"))
        if tool == "web_search" and not args.get("query"):
            continue
        if tool == "web_fetch" and not args.get("url"):
            continue
        content = compact_tool_output(output.get("output") or "", max_chars=max_chars)
        if not content:
            continue
        return {"tool": tool, "args": args, "output": content}
    return None


def messages_for_user(result: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": WEB_NUDGE}]
    for turn in result.get("prior_turns") or []:
        if isinstance(turn, dict) and turn.get("user"):
            messages.append({"role": "user", "content": str(turn["user"])})
            if turn.get("assistant"):
                messages.append({"role": "assistant", "content": str(turn["assistant"])})
        elif isinstance(turn, str) and turn.strip():
            messages.append({"role": "user", "content": turn.strip()})
    messages.append({"role": "user", "content": str(result.get("user") or "")})
    return messages


def append_tool_call(messages: list[dict[str, Any]], source_id: str, step: dict[str, Any], idx: int = 0) -> str:
    call_id = f"call_{source_id}_{idx}"
    messages.append({
        "role": "assistant",
        "content": None,
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
    return call_id


def build_answer_row(result: dict[str, Any], step: dict[str, Any], final: str, family: str, repeat: int) -> dict[str, Any] | None:
    final = re.sub(r"\s+", " ", final).strip()
    if not final or len(final) > 900 or FORBIDDEN_FINAL_RE.search(final):
        return None
    messages = messages_for_user(result)
    append_tool_call(messages, str(result.get("id") or "web"), step, 0)
    messages.append({"role": "assistant", "content": final})
    row = {
        "messages": messages,
        "generator": "odysseus_v61_app_route_web_post_tool",
        "metadata": {
            "source_result_id": result.get("id"),
            "family": family,
            "repeat": repeat,
            "first_tool": step["tool"],
            "first_args": step["args"],
        },
    }
    row["uuid"] = stable_id("ody_v61_app_route_web", row)
    return row


def build_retry_row(
    result: dict[str, Any],
    bad_step: dict[str, Any],
    retry_query: str,
    final: str,
    retry_output: str | None,
    repeat: int,
) -> dict[str, Any] | None:
    messages = messages_for_user(result)
    source_id = str(result.get("id") or "retry")
    append_tool_call(messages, source_id, bad_step, 0)
    messages.append({
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": f"call_{source_id}_retry",
            "type": "function",
            "function": {
                "name": "web_search",
                "arguments": json.dumps({"query": retry_query}, separators=(",", ":"), ensure_ascii=True),
            },
        }],
    })
    if retry_output:
        messages.append({
            "role": "tool",
            "tool_call_id": f"call_{source_id}_retry",
            "content": retry_output,
        })
        final = re.sub(r"\s+", " ", final).strip()
        if not final or len(final) > 900 or FORBIDDEN_FINAL_RE.search(final):
            return None
        messages.append({"role": "assistant", "content": final})
    row = {
        "messages": messages,
        "generator": "odysseus_v61_app_route_web_retry",
        "metadata": {
            "source_result_id": result.get("id"),
            "family": "retry_off_target_then_answer" if retry_output else "retry_off_target",
            "repeat": repeat,
            "bad_args": bad_step["args"],
            "retry_query": retry_query,
        },
    }
    row["uuid"] = stable_id("ody_v61_app_route_web", row)
    return row


def split_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        (val if idx % 10 == 9 else train).append(row)
    return train, val


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual", type=Path, default=DEFAULT_ACTUALS)
    parser.add_argument("--edits", type=Path, default=DEFAULT_EDITS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--max-output-chars", type=int, default=4200)
    parser.add_argument("--answer-repeat", type=int, default=4)
    parser.add_argument("--retry-repeat", type=int, default=8)
    parser.add_argument("--retry-output-json", type=Path)
    args = parser.parse_args()

    results = load_results(args.actual)
    finals = load_edited_finals(args.edits)
    retry_outputs = {}
    if args.retry_output_json and args.retry_output_json.exists():
        retry_outputs = json.loads(args.retry_output_json.read_text(encoding="utf-8"))

    rows: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    family_counts: dict[str, int] = {}
    for result in results:
        result_id = str(result.get("id") or "")
        if result.get("kind") != "web" or result_id not in finals:
            continue
        step = first_web_step(result, args.max_output_chars)
        if not step or step["tool"] != "web_search":
            continue
        final = finals[result_id]["final"]
        accepted = 0
        for rep in range(args.answer_repeat):
            row = build_answer_row(result, step, final, "answer_after_first_web_search", rep)
            if row:
                rows.append(row)
                accepted += 1
                family_counts[row["metadata"]["family"]] = family_counts.get(row["metadata"]["family"], 0) + 1
        audit.append({
            "id": result_id,
            "family": "answer_after_first_web_search",
            "accepted_rows": accepted,
            "first_args": step["args"],
            "final": final,
        })

    hard_path = REPO_ROOT / "data/evals/ody_v57_quick_live_search_cases_20260821/v60_container_final_event_run_20260821_2123/actual_results.json"
    hard_by_id = {str(item.get("id")): item for item in load_results(hard_path)} if hard_path.exists() else {}

    hard_answer_specs = [
        {
            "id": "v57_sweden_gas_price",
            "final": "Gasoline in Sweden is roughly 16.4-16.6 SEK per liter based on the latest fuel-price results. The exact price varies by station and fuel grade, but that is the current ballpark for petrol/gas per liter.",
        },
    ]
    for spec in hard_answer_specs:
        result = hard_by_id.get(spec["id"])
        if not result:
            continue
        step = first_web_step(result, args.max_output_chars)
        if not step:
            continue
        accepted = 0
        for rep in range(args.retry_repeat):
            row = build_answer_row(result, step, spec["final"], "hard_answer_after_first_web_search", rep)
            if row:
                rows.append(row)
                accepted += 1
                family_counts[row["metadata"]["family"]] = family_counts.get(row["metadata"]["family"], 0) + 1
        audit.append({
            "id": spec["id"],
            "family": "hard_answer_after_first_web_search",
            "accepted_rows": accepted,
            "first_args": step["args"],
            "final": spec["final"],
        })

    hard_retry_specs = [
        {
            "id": "v57_norway_coordinates",
            "retry_query": "Norway country geographic coordinates latitude longitude",
            "final": "Norway is in Northern Europe on the Scandinavian Peninsula. Its commonly cited country coordinates are about 62°N, 10°E.",
        },
        {
            "id": "v57_snail_touch_followup",
            "retry_query": "is it safe to touch garden snails after they foam mucus scared wash hands",
            "final": "Usually yes, it is okay to gently touch a snail, even if it is foaming from stress, but avoid your eyes or mouth and wash your hands afterward. Do not handle it roughly, and leave it alone if it keeps bubbling or retracting.",
        },
    ]
    if hard_by_id:
        for spec in hard_retry_specs:
            result = hard_by_id.get(spec["id"])
            if not result:
                continue
            step = first_web_step(result, args.max_output_chars)
            if not step:
                continue
            retry_output = retry_outputs.get(spec["retry_query"])
            for rep in range(args.retry_repeat):
                row = build_retry_row(
                    result,
                    step,
                    spec["retry_query"],
                    spec["final"],
                    retry_output,
                    rep,
                )
                if row:
                    rows.append(row)
                    family_counts[row["metadata"]["family"]] = family_counts.get(row["metadata"]["family"], 0) + 1
            audit.append({
                "id": spec["id"],
                "family": "retry_off_target_then_answer" if retry_output else "retry_off_target",
                "retry_query": spec["retry_query"],
                "has_retry_output": bool(retry_output),
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
        "source_actual": str(args.actual),
        "source_edits": str(args.edits),
        "accepted_rows": len(rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "family_counts": family_counts,
        "goal": "train Qwen to continue correctly after Odysseus app-route web_search tool output plus system nudge",
        "forbidden_final_regex": FORBIDDEN_FINAL_RE.pattern,
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
