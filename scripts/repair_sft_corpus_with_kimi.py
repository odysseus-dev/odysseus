#!/usr/bin/env python3
"""Produce turn-addressed Kimi repairs for audited Odysseus SFT sessions."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sqlite3
import time
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def decrypt(value: str) -> str:
    if not value.startswith("enc:"):
        return value
    key = (ROOT / "data" / ".app_key").read_bytes()
    return Fernet(key).decrypt(value[4:].encode()).decode()


def endpoint(endpoint_id: str, model: str) -> dict[str, str]:
    # Honor the same configured data directory as the live Odysseus service.
    # Eval worktrees commonly keep only source under ROOT while 7011 points at
    # the canonical shared database via ODYSSEUS_DATA_DIR.
    from src.constants import DATA_DIR

    data_dir = Path(DATA_DIR)
    con = sqlite3.connect(data_dir / "app.db")
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT base_url,api_key FROM model_endpoints WHERE id=? AND is_enabled=1",
        (endpoint_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Enabled endpoint not found: {endpoint_id}")
    value = str(row["api_key"] or "")
    if value.startswith("enc:"):
        value = Fernet((data_dir / ".app_key").read_bytes()).decrypt(value[4:].encode()).decode()
    return {"base_url": row["base_url"], "api_key": value, "model": model}


def parse_json(text: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S).strip()
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            text = match.group(0)
    return json.loads(text)


def compact_turn(row: dict[str, Any]) -> dict[str, Any]:
    def clip(value: Any, limit: int) -> str:
        text = str(value or "")
        return text[:limit] + ("..." if len(text) > limit else "")

    return {
        "message_id": row.get("message_id"),
        "user": clip(row.get("user"), 1800),
        "assistant": clip(row.get("assistant"), 3000),
        "thinking": clip(row.get("thinking"), 2200),
        "tool_events": [
            {
                "tool": event.get("tool"),
                "command": clip(event.get("command"), 900),
                "output": clip(event.get("output"), 1700),
                "exit_code": event.get("exit_code"),
            }
            for event in row.get("tool_events") or []
        ],
    }


def repair_prompt(verdict: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    system = """You repair tool-agent SFT traces. Return strict JSON only:
{"session_id":"...","decision":"repaired"|"exclude","summary":"...","turns":[{"message_id":"...","action":"keep"|"rewrite"|"drop","assistant":"required for rewrite","thinking":"clean reasoning for rewrite","reason":"..."}]}

Each original trace row is one user/assistant turn. Return exactly one turn decision for every supplied message_id, in the original order.

Rules:
- User text and tool events are immutable. Never invent, remove, reorder, or modify tool calls.
- `keep` preserves the entire row. Use it only when that turn is independently trainable.
- `rewrite` may replace assistant and thinking text only. It must describe exactly what the immutable tool evidence proves.
- `drop` removes the entire user/assistant turn. Drop stale resend branches, duplicate loops, false tool-unavailability turns, fixture/harness meta turns, and unsupported success claims that cannot truthfully satisfy the user.
- Set decision=exclude if dropping bad turns leaves an incoherent trajectory, if a requested state change has no successful tool evidence and cannot be honestly reframed, if a wrong destructive action occurred, or if tool arguments/results teach a materially wrong strategy.
- Do not preserve or introduce references to SFT, fixtures, harness internals, injected context, untrusted blocks, hidden schemas, or training.
- Do not expose raw tool dumps as assistant prose. Summarize useful results cleanly.
- Clean thinking should identify intent, required evidence, chosen tool, and result. Do not discuss system prompts or tool availability internals.
- Visible answers should sound like a capable personal assistant: lead with the answer or completed action, synthesize tool results, retain useful deep links, and omit raw field dumps, internal routing narration, repeated metadata, and needless offers to do more.
- Match detail to the request. Simple confirmations should usually be one sentence. Lists should include only fields that help the user distinguish or act on items.
- Multi-intent requests must have every part fulfilled. Relative dates must agree with explicit tool bounds and the trace date context.
- Prefer exclusion over fabricating evidence. Concision matters, but correctness matters more."""
    user = {
        "current_date": "2026-08-30",
        "timezone": "UTC",
        "deepseek_audit": verdict,
        "session": {
            "session_id": rows[0].get("session_id"),
            "session_name": rows[0].get("session_name"),
            "turns": [compact_turn(row) for row in rows],
        },
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


def call_kimi(ep: dict[str, str], verdict: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    body = {
        "model": ep["model"],
        "messages": repair_prompt(verdict, rows),
        "temperature": 0,
        "max_tokens": 10000,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        ep["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {ep['api_key']}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode())
    message = payload["choices"][0]["message"]
    return parse_json(str(message.get("content") or message.get("reasoning_content") or ""))


def validate_and_apply(rows: list[dict[str, Any]], repair: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    errors = []
    decisions = repair.get("turns")
    if not isinstance(decisions, list):
        return [], ["turns is not a list"]
    original_ids = [str(row.get("message_id") or "") for row in rows]
    decision_ids = [str(item.get("message_id") or "") for item in decisions]
    if decision_ids != original_ids:
        return [], ["turn decisions do not exactly match original message IDs/order"]
    output = []
    for row, item in zip(rows, decisions):
        action = item.get("action")
        if action == "drop":
            continue
        if action == "keep":
            output.append(dict(row))
            continue
        if action != "rewrite":
            errors.append(f"{row.get('message_id')}: invalid action {action!r}")
            continue
        assistant = str(item.get("assistant") or "").strip()
        thinking = str(item.get("thinking") or "").strip()
        if not assistant:
            errors.append(f"{row.get('message_id')}: rewrite missing assistant")
            continue
        updated = dict(row)
        updated["assistant"] = assistant
        updated["thinking"] = thinking
        updated["round_texts"] = [assistant]
        metadata = dict(updated.get("metadata") or {})
        metadata["sft_repair"] = {
            "model": "moonshotai/kimi-k3",
            "reason": item.get("reason") or "",
            "repaired_at": "2026-08-30",
        }
        updated["metadata"] = metadata
        output.append(updated)
    if not output and repair.get("decision") == "repaired":
        errors.append("repaired decision produced no turns")
    return output, errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--endpoint-id", default="f3904562")
    parser.add_argument("--model", default="moonshotai/kimi-k3")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    trace = [json.loads(line) for line in args.trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    sessions: dict[str, list[dict[str, Any]]] = {}
    for row in trace:
        sessions.setdefault(str(row.get("session_id") or ""), []).append(row)
    verdicts = [json.loads(line) for line in args.audit.read_text(encoding="utf-8").splitlines() if line.strip()]
    targets = [row for row in verdicts if row.get("verdict") == "repair" and row.get("session_id") in sessions]
    if args.limit:
        targets = targets[: args.limit]
    ep = endpoint(args.endpoint_id, args.model)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    def process(verdict: dict[str, Any]) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[str]]:
        sid = verdict["session_id"]
        last_error = ""
        for attempt in range(3):
            try:
                repair = call_kimi(ep, verdict, sessions[sid])
                repaired, errors = validate_and_apply(sessions[sid], repair)
                return sid, repair, repaired, errors
            except Exception as exc:
                last_error = repr(exc)
                if attempt < 2:
                    time.sleep(3 + attempt * 4)
        return sid, {"session_id": sid, "decision": "exclude", "summary": last_error, "turns": []}, [], [last_error]

    results: dict[str, tuple[dict[str, Any], list[dict[str, Any]], list[str]]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(process, verdict) for verdict in targets]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            sid, repair, repaired, errors = future.result()
            results[sid] = (repair, repaired, errors)
            print(f"kimi {index}/{len(targets)} {sid} {repair.get('decision')} errors={len(errors)}", flush=True)

    decisions_path = args.out_dir / "kimi_repair_decisions.jsonl"
    candidate_path = args.out_dir / "repaired_sessions_candidate.jsonl"
    excluded_path = args.out_dir / "excluded_or_invalid.jsonl"
    with decisions_path.open("w", encoding="utf-8") as decisions_file, candidate_path.open("w", encoding="utf-8") as candidate_file, excluded_path.open("w", encoding="utf-8") as excluded_file:
        for verdict in targets:
            sid = verdict["session_id"]
            repair, repaired, errors = results[sid]
            record = {"session_id": sid, "repair": repair, "validation_errors": errors, "source_verdict": verdict}
            decisions_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            if repair.get("decision") == "repaired" and not errors:
                for row in repaired:
                    candidate_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            else:
                excluded_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps({"targets": len(targets), "candidate_sessions": sum(1 for sid in results if results[sid][0].get('decision') == 'repaired' and not results[sid][2]), "excluded_or_invalid": sum(1 for sid in results if results[sid][0].get('decision') != 'repaired' or results[sid][2]), "out_dir": str(args.out_dir)}, indent=2))


if __name__ == "__main__":
    main()
