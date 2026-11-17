#!/usr/bin/env python3
"""Audit an Odysseus SFT JSONL corpus and use DeepSeek for semantic review."""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import random
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE = ROOT / "data" / "sft_traces" / "sft_alex_creator.jsonl"
OUT_DIR = ROOT / "data" / "audits"

LEAK_RE = re.compile(
    r"fake-(?:sender|odysseus)|synthetic (?:sft|fixture)|safe for training|"
    r"training traces?|you are a fish|prompt injection|harness (?:bug|issue|dump)",
    re.I,
)
UNAVAILABLE_RE = re.compile(
    r"(?:i (?:do not|don.t|cannot|can.t)|there(?: is|'s) no) .{0,55}"
    r"(?:tool|access|email|calendar|memory|document|browser|shell)",
    re.I,
)
RAW_DUMP_RE = re.compile(r"Here are your (?:emails|events) \(\d+\):", re.I)
FAILURE_RE = re.compile(
    r"(?:permission denied|requires? .{0,30}(?:dependency|package)|not configured|"
    r"tool calls? failed|internal server error|traceback|timed out)",
    re.I,
)


def decrypt_secret(value: str) -> str:
    if not value or not value.startswith("enc:"):
        return value or ""
    from cryptography.fernet import Fernet

    key = (ROOT / "data" / ".app_key").read_bytes()
    return Fernet(key).decrypt(value[4:].encode("ascii")).decode("utf-8")


def deepseek_endpoint(endpoint_id: str | None, model: str | None) -> dict[str, str]:
    con = sqlite3.connect(ROOT / "data" / "app.db")
    con.row_factory = sqlite3.Row
    if endpoint_id:
        row = con.execute(
            "SELECT * FROM model_endpoints WHERE id=? AND COALESCE(api_key,'') != ''",
            (endpoint_id,),
        ).fetchone()
    else:
        row = con.execute(
            """SELECT * FROM model_endpoints
               WHERE is_enabled=1 AND COALESCE(api_key,'') != ''
                 AND (lower(name) LIKE '%deepseek%' OR lower(id) LIKE '%deepseek%')
               ORDER BY CASE WHEN lower(name)='deepseek' THEN 0 ELSE 1 END LIMIT 1"""
        ).fetchone()
    if row is None:
        raise RuntimeError("No enabled DeepSeek endpoint with an API key")
    models = json.loads(row["cached_models"] or "[]")
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "api_key": decrypt_secret(row["api_key"]),
        "model": model or (models[0] if models else "deepseek-chat"),
    }


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            rows.append({"_invalid_line": line_no, "_error": str(exc), "_raw": line[:500]})
            continue
        row["_line"] = line_no
        rows.append(row)
    return rows


def live_session_ids(owner: str) -> set[str]:
    con = sqlite3.connect(ROOT / "data" / "app.db")
    try:
        return {str(row[0]) for row in con.execute("SELECT id FROM sessions WHERE owner = ?", (owner,))}
    finally:
        con.close()


def row_flags(row: dict[str, Any]) -> list[str]:
    if "_invalid_line" in row:
        return ["invalid_json"]
    flags = []
    user = str(row.get("user") or "")
    assistant = str(row.get("assistant") or "")
    thinking = str(row.get("thinking") or "")
    visible = "\n".join((user, assistant, thinking))
    events = row.get("tool_events") or []
    if not user.strip() or not assistant.strip():
        flags.append("missing_user_or_assistant")
    if LEAK_RE.search(visible):
        flags.append("fixture_or_harness_leak")
    if UNAVAILABLE_RE.search(assistant):
        flags.append("possible_false_tool_unavailability")
    if RAW_DUMP_RE.search(assistant):
        flags.append("raw_harness_style_answer")
    if any(FAILURE_RE.search(str(ev.get("output") or "")) for ev in events):
        flags.append("tool_failure_present")
    if events and not assistant.strip():
        flags.append("tool_call_without_final_answer")
    if len(row.get("round_texts") or []) > 2:
        nonempty = [str(x).strip() for x in row.get("round_texts") or [] if str(x).strip()]
        if len(nonempty) > 1 and len(set(nonempty)) < len(nonempty):
            flags.append("repeated_round_text")
    return flags


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    def clip(value: Any, size: int) -> str:
        text = str(value or "")
        return text[:size] + ("..." if len(text) > size else "")

    return {
        "line": row.get("_line"),
        "message_id": row.get("message_id"),
        "user": clip(row.get("user"), 1200),
        "assistant": clip(row.get("assistant"), 2200),
        "thinking": clip(row.get("thinking"), 1600),
        "flags": row_flags(row),
        "tools": [
            {
                "tool": ev.get("tool"),
                "command": clip(ev.get("command"), 700),
                "output": clip(ev.get("output"), 1100),
                "exit_code": ev.get("exit_code"),
            }
            for ev in (row.get("tool_events") or [])
        ],
    }


def _parse_json_message(message: dict[str, Any]) -> dict[str, Any]:
    content = str(message.get("content") or message.get("reasoning_content") or "").strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I | re.S).strip()
    if not content.startswith("{"):
        match = re.search(r"\{.*\}", content, flags=re.S)
        if match:
            content = match.group(0)
    if not content:
        raise ValueError("DeepSeek returned empty content and reasoning_content")
    return json.loads(content)


def judge(endpoint: dict[str, str], sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    system = """You are a strict SFT corpus auditor for a general tool-using agent.
Return JSON only as {"results":[...]}. Return exactly one result per session.
Each result: session_id, verdict (keep|repair|delete), score (0-100), issues (strings), repairs (specific strings), and coverage_notes.

Judge the complete behavior and whether the response is a good speaking-style target. Keep only when intent, reasoning, tool selection, arguments, tool outputs, state changes, follow-ups, and final answers agree, and the visible answer is concise, natural, and synthesized for the user. Repair means a coherent trace can be fixed by removing/replacing specific turns or text. Delete means the trajectory teaches a materially wrong strategy or is too corrupted.

Flag false tool-unavailability claims, repeated answers/turns, stale resend branches, missing requested actions, success claims without successful tool evidence, malformed tool arguments, raw harness dumps presented as the answer, fixture/SFT/harness/prompt-injection discussion, incorrect relative dates/timezones, unsafe destructive actions, needless tools, tool loops, and thinking that contradicts the final action. Also mark repair when the final answer mechanically echoes tool output, repeats metadata the user did not request, narrates internal routing, asks needless follow-up questions, or is substantially more verbose than needed. A failed tool call is acceptable only when the assistant handles it correctly and does not teach a bad workaround. Do not penalize raw formatting that exists only inside tool output. For multi-intent prompts, every requested part must be handled. Be conservative because these traces train both tool strategy and response style."""
    payload = {
        "model": endpoint["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"audit_date": "2026-08-30", "timezone": "UTC", "sessions": sessions}, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": 12000,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        endpoint["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {endpoint['api_key']}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        result = json.loads(response.read().decode())
    results = _parse_json_message(result["choices"][0]["message"])["results"]
    expected_ids = [str(session.get("session_id") or "") for session in sessions]
    actual_ids = [str(item.get("session_id") or "") for item in results]
    if len(results) != len(sessions) or sorted(actual_ids) != sorted(expected_ids):
        raise ValueError(
            f"DeepSeek verdict IDs do not match batch: expected={expected_ids!r} actual={actual_ids!r}"
        )
    by_id = {str(item["session_id"]): item for item in results}
    return [by_id[session_id] for session_id in expected_ids]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--live-owner", help="Only audit traced sessions still present in app.db for this owner")
    parser.add_argument("--endpoint-id")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--sample-per-tool", type=int, default=2)
    parser.add_argument("--max-sessions", type=int, default=260)
    parser.add_argument("--all-sessions", action="store_true", help="Semantically review every session in scope")
    parser.add_argument("--exclude-verdicts", type=Path, help="Skip session IDs already present in this verdict JSONL")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--skip-deepseek", action="store_true")
    args = parser.parse_args()

    rows = load_rows(args.trace)
    if args.live_owner:
        live_ids = live_session_ids(args.live_owner)
        rows = [row for row in rows if str(row.get("session_id") or "") in live_ids]
    sessions: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    tools: collections.Counter[str] = collections.Counter()
    models: collections.Counter[str] = collections.Counter()
    flag_counts: collections.Counter[str] = collections.Counter()
    duplicate_ids: collections.Counter[str] = collections.Counter()
    content_hashes: collections.defaultdict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        sid = str(row.get("session_id") or f"invalid-line-{row.get('_invalid_line')}")
        sessions[sid].append(row)
        models[str((row.get("metadata") or {}).get("model") or "unknown")] += 1
        duplicate_ids[str(row.get("message_id") or "missing")] += 1
        digest = hashlib.sha256(json.dumps([row.get("user"), row.get("assistant"), row.get("tool_events")], sort_keys=True, default=str).encode()).hexdigest()
        content_hashes[digest].append(row)
        for flag in row_flags(row):
            flag_counts[flag] += 1
        for event in row.get("tool_events") or []:
            tools[str(event.get("tool") or "unknown")] += 1

    suspicious = {sid for sid, turns in sessions.items() if any(row_flags(row) for row in turns)}
    by_tool: dict[str, list[str]] = collections.defaultdict(list)
    for sid, turns in sessions.items():
        for tool in {str(e.get("tool")) for row in turns for e in row.get("tool_events") or [] if e.get("tool")}:
            by_tool[tool].append(sid)
    rng = random.Random(args.seed)
    if args.all_sessions:
        selected = set(sessions)
    else:
        selected = set(suspicious)
        for tool, candidates in sorted(by_tool.items()):
            pool = sorted(set(candidates) - selected)
            selected.update(rng.sample(pool, min(args.sample_per_tool, len(pool))))
        selected = set(sorted(selected)[: args.max_sessions])
    if args.exclude_verdicts:
        reviewed = {
            str(json.loads(line).get("session_id") or "")
            for line in args.exclude_verdicts.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        selected.difference_update(reviewed)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"sft_corpus_deepseek_audit_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    deterministic = {
        "trace": str(args.trace),
        "live_owner": args.live_owner,
        "turns": len(rows),
        "sessions": len(sessions),
        "tool_counts": dict(tools.most_common()),
        "model_counts": dict(models.most_common()),
        "flag_counts": dict(flag_counts.most_common()),
        "suspicious_sessions": len(suspicious),
        "duplicate_message_ids": {k: v for k, v in duplicate_ids.items() if v > 1},
        "exact_duplicate_rows": sum(len(v) - 1 for v in content_hashes.values() if len(v) > 1),
        "deepseek_selected_sessions": len(selected),
        "all_sessions": args.all_sessions,
        "excluded_verdicts": str(args.exclude_verdicts) if args.exclude_verdicts else None,
    }
    (out / "coverage.json").write_text(json.dumps(deterministic, indent=2), encoding="utf-8")
    with (out / "deterministic_repair_queue.jsonl").open("w", encoding="utf-8") as handle:
        for sid in sorted(suspicious):
            handle.write(json.dumps({"session_id": sid, "flags": sorted({f for r in sessions[sid] for f in row_flags(r)}), "lines": [r.get("_line") for r in sessions[sid]]}) + "\n")

    judged: list[dict[str, Any]] = []
    if not args.skip_deepseek:
        endpoint = deepseek_endpoint(args.endpoint_id, args.model)
        chosen = sorted(selected)
        batches = []
        for start in range(0, len(chosen), args.batch_size):
            ids = chosen[start : start + args.batch_size]
            batch = [{"session_id": sid, "name": sessions[sid][0].get("session_name"), "turns": [compact_row(r) for r in sessions[sid]]} for sid in ids]
            batches.append((start, batch))

        def run_batch(item: tuple[int, list[dict[str, Any]]]) -> tuple[int, list[dict[str, Any]]]:
            start, batch = item
            for attempt in range(3):
                try:
                    results = judge(endpoint, batch)
                    return start, results
                except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
                    if attempt == 2:
                        raise RuntimeError(f"DeepSeek batch failed at {start}: {exc}") from exc
                    time.sleep(3 + attempt * 4)
            raise AssertionError("unreachable")

        completed = 0
        ordered: dict[int, list[dict[str, Any]]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(run_batch, item) for item in batches]
            for future in concurrent.futures.as_completed(futures):
                start, results = future.result()
                ordered[start] = results
                completed += len(results)
                print(f"deepseek {completed}/{len(chosen)}", flush=True)
        for start in sorted(ordered):
            judged.extend(ordered[start])
        with (out / "deepseek_verdicts.jsonl").open("w", encoding="utf-8") as handle:
            for result in judged:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")

    verdicts = collections.Counter(str(row.get("verdict") or "unknown") for row in judged)
    report = [
        "# SFT Corpus Audit", "",
        f"- Trace: `{args.trace}`", f"- Turns: {len(rows)}", f"- Sessions: {len(sessions)}",
        f"- Tools represented: {len(tools)}", f"- Suspicious sessions (deterministic): {len(suspicious)}",
        f"- Exact duplicate rows: {deterministic['exact_duplicate_rows']}",
        f"- DeepSeek sessions reviewed: {len(judged)}", f"- DeepSeek verdicts: `{dict(verdicts)}`", "",
        "## Deterministic Flags", "",
    ]
    report.extend(f"- {name}: {count}" for name, count in flag_counts.most_common())
    report.extend(["", "## Lowest-Coverage Tools", ""])
    report.extend(f"- `{tool}`: {count}" for tool, count in sorted(tools.items(), key=lambda x: (x[1], x[0]))[:20])
    report.extend(["", "## DeepSeek Repair/Delete Queue", ""])
    for row in judged:
        if row.get("verdict") == "keep":
            continue
        report.append(f"- `{row.get('session_id')}` **{row.get('verdict')}** score={row.get('score')}: {'; '.join(row.get('issues') or [])}")
    (out / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"output={out}")


if __name__ == "__main__":
    main()
