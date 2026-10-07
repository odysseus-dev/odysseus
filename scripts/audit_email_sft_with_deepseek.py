#!/usr/bin/env python3
"""Audit recent Odysseus email SFT conversations with a DeepSeek judge."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "app.db"
OUT_DIR = ROOT / "data" / "audits"


EMAIL_RE = re.compile(
    r"\b(email|emails|inbox|mailbox|attachment|attachments|draft|reply|archive|"
    r"delete|spam|blocked|unblock|read|unread|favorite|done|contact)\b",
    re.I,
)


def decrypt_secret(value: str) -> str:
    if not value or not value.startswith("enc:"):
        return value or ""
    from cryptography.fernet import Fernet

    key = (ROOT / "data" / ".app_key").read_bytes()
    return Fernet(key).decrypt(value[len("enc:") :].encode("ascii")).decode("utf-8")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def deepseek_endpoint(con: sqlite3.Connection, endpoint_id: str | None = None, model: str | None = None) -> dict[str, str]:
    if endpoint_id:
        row = con.execute(
            """
            SELECT id, name, base_url, api_key, cached_models
            FROM model_endpoints
            WHERE id = ?
              AND COALESCE(api_key, '') != ''
            """,
            (endpoint_id,),
        ).fetchone()
    else:
        row = con.execute(
        """
        SELECT id, name, base_url, api_key, cached_models
        FROM model_endpoints
        WHERE is_enabled = 1
          AND COALESCE(api_key, '') != ''
          AND (lower(name) LIKE '%deepseek%' OR lower(id) LIKE '%deepseek%')
        ORDER BY CASE WHEN lower(name) = 'deepseek' THEN 0 ELSE 1 END
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("No enabled DeepSeek endpoint with an API key found in model_endpoints")
    models = json.loads(row["cached_models"] or "[]")
    selected = model or (models[0] if models else "deepseek-chat")
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "api_key": decrypt_secret(row["api_key"] or ""),
        "model": selected,
    }


def compact_tool_event(ev: dict[str, Any]) -> dict[str, Any]:
    out = str(ev.get("output") or "")
    return {
        "tool": ev.get("tool"),
        "command": ev.get("command"),
        "output": out[:1200] + ("..." if len(out) > 1200 else ""),
        "exit_code": ev.get("exit_code"),
    }


def session_payload(con: sqlite3.Connection, sid: str) -> dict[str, Any]:
    s = con.execute(
        "SELECT id, name, created_at, updated_at, message_count FROM sessions WHERE id = ?",
        (sid,),
    ).fetchone()
    messages = []
    for m in con.execute(
        "SELECT role, content, metadata, timestamp FROM chat_messages WHERE session_id = ? ORDER BY timestamp, id",
        (sid,),
    ):
        meta: dict[str, Any] = {}
        if m["metadata"]:
            try:
                meta = json.loads(m["metadata"])
            except json.JSONDecodeError:
                meta = {}
        content = m["content"] or ""
        thinking = meta.get("thinking")
        if isinstance(thinking, str) and len(thinking) > 1000:
            thinking = thinking[:1000] + "..."
        messages.append(
            {
                "role": m["role"],
                "timestamp": m["timestamp"],
                "content": content[:2500] + ("..." if len(content) > 2500 else ""),
                "thinking": thinking,
                "tool_events": [compact_tool_event(ev) for ev in meta.get("tool_events") or []],
            }
        )
    docs = []
    for d in con.execute(
        """
        SELECT id, title, language, current_content, source_email_uid, updated_at
        FROM documents
        WHERE session_id = ?
        ORDER BY updated_at DESC
        LIMIT 3
        """,
        (sid,),
    ):
        content = d["current_content"] or ""
        docs.append(
            {
                "id": d["id"],
                "title": d["title"],
                "language": d["language"],
                "source_email_uid": d["source_email_uid"],
                "content": content[:1800] + ("..." if len(content) > 1800 else ""),
            }
        )
    return {
        "session": dict(s),
        "messages": messages,
        "open_documents": docs,
    }


def recent_email_sessions(con: sqlite3.Connection, owner: str, limit: int) -> list[str]:
    rows = con.execute(
        """
        SELECT id
        FROM sessions
        WHERE owner = ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (owner, limit),
    ).fetchall()
    keep = []
    for row in rows:
        text = "\n".join(
            r["content"] or ""
            for r in con.execute("SELECT content FROM chat_messages WHERE session_id = ?", (row["id"],))
        )
        tools = "\n".join(
            r["metadata"] or ""
            for r in con.execute("SELECT metadata FROM chat_messages WHERE session_id = ?", (row["id"],))
        )
        if EMAIL_RE.search(text) or "mcp__email" in tools or "list_email" in tools:
            keep.append(row["id"])
    return keep


def session_ids_from_results(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise RuntimeError(f"Expected results list in {path}")
    out: list[str] = []
    for row in rows:
        sid = str(row.get("session_id") or "").strip()
        if sid and sid not in out:
            out.append(sid)
    return out


def judge_prompt(batch: list[dict[str, Any]]) -> list[dict[str, str]]:
    system = """You are auditing Odysseus email-agent conversations for SFT training quality.
Return strict JSON only: {"results":[...]}.
For every session, decide and copy back `session_id` and `session_name` from `session`.
- verdict: keep, repair, or delete.
- trainable_score: 0-100.
- issues: short strings.
- repairs: concrete edits needed, or [].
- date_risk: none, low, medium, high.
- thinking_trace_risk: none, low, medium, high.
- rationale: one concise sentence.

Important audit rules:
- Keep only traces where user intent, tool calls, tool outputs, and final answer align.
- Repair/delete if assistant claimed an email action without a corresponding tool event.
- Repair/delete if it says tools are unavailable when email tools were actually needed/available.
- Repair/delete repeated resend/stale-loop traces unless the bad branch is removed.
- Repair/delete visible raw harness dumps, unpolished tool output, or synthetic/fake/SFT leaks in assistant/user message `content`.
- Do not penalize raw text inside `tool_events.output` by itself. Tool outputs are allowed to be raw; only flag them when the assistant-facing final content also exposed the dump or when the tool result is semantically wrong.
- Date-relative tasks are safe only if the trace includes a clear current date/timezone context or a tool query using explicit date bounds. Otherwise flag date_risk.
- Thinking traces are usable only if they reflect correct tool choice and do not mention fake fixtures, harness bugs, stale injected data, or false tool unavailability.
- Multi-intent user requests must satisfy all parts or be repair/delete.
- Be strict: these are for training a model, not UI QA."""
    user = json.dumps({"current_date": "2026-08-24", "timezone": "UTC", "sessions": batch}, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_judge(endpoint: dict[str, str], batch: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "model": endpoint["model"],
        "messages": judge_prompt(batch),
        "temperature": 0,
        "max_tokens": 3500,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        endpoint["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {endpoint['api_key']}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=75) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Judge returned empty message content")
    return json.loads(content)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", default="sft_alex_creator")
    ap.add_argument("--limit", type=int, default=140)
    ap.add_argument("--batch-size", type=int, default=5)
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--endpoint-id")
    ap.add_argument("--model")
    ap.add_argument("--results-file", type=Path, default=None, help="Audit exact session_ids from an overseer/eval actual_results.json")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = db()
    endpoint = deepseek_endpoint(con, endpoint_id=args.endpoint_id, model=args.model)
    if args.results_file:
        sids = session_ids_from_results(args.results_file)
    else:
        sids = recent_email_sessions(con, args.owner, args.limit)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_jsonl = OUT_DIR / f"email_sft_deepseek_audit_{args.owner}_{stamp}.jsonl"
    out_md = OUT_DIR / f"email_sft_deepseek_audit_{args.owner}_{stamp}.md"

    all_results: list[dict[str, Any]] = []
    for i in range(0, len(sids), args.batch_size):
        batch_sids = sids[i : i + args.batch_size]
        batch = [session_payload(con, sid) for sid in batch_sids]
        for attempt in range(3):
            try:
                judged = call_judge(endpoint, batch)
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if attempt == 2:
                    raise
                time.sleep(2 + attempt * 3)
        results = judged.get("results", [])
        for j, result in enumerate(results):
            if j < len(batch):
                result.setdefault("session_id", batch[j]["session"]["id"])
                result.setdefault("session_name", batch[j]["session"]["name"])
        with out_jsonl.open("a", encoding="utf-8") as f:
            for result in results:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
        all_results.extend(results)
        print(f"judged {min(i + args.batch_size, len(sids))}/{len(sids)}")
        time.sleep(args.sleep)

    counts: dict[str, int] = {}
    for r in all_results:
        counts[r.get("verdict", "unknown")] = counts.get(r.get("verdict", "unknown"), 0) + 1

    lines = [
        f"# Email SFT DeepSeek Audit: {args.owner}",
        "",
        f"- Sessions judged: {len(all_results)}",
        f"- Source recent limit: {args.limit}",
        f"- Endpoint: {endpoint.get('name')} ({endpoint.get('id')})",
        f"- Model: {endpoint['model']}",
        f"- Verdict counts: {json.dumps(counts, sort_keys=True)}",
        "",
        "## Repair/Delete Queue",
        "",
    ]
    for r in all_results:
        if r.get("verdict") == "keep":
            continue
        sid = r.get("session_id") or r.get("id") or r.get("session", {}).get("id")
        name = r.get("session_name") or r.get("name") or ""
        issues = ", ".join(r.get("issues") or [])
        repairs = "; ".join(
            item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True)
            for item in (r.get("repairs") or [])
        )
        lines.append(f"- `{sid}` {name} -- **{r.get('verdict')}** score={r.get('trainable_score')} issues={issues} repairs={repairs}")
    lines.extend(["", "## Keep Candidates", ""])
    for r in all_results:
        if r.get("verdict") != "keep":
            continue
        sid = r.get("session_id") or r.get("id") or r.get("session", {}).get("id")
        name = r.get("session_name") or r.get("name") or ""
        lines.append(f"- `{sid}` {name} -- score={r.get('trainable_score')} date={r.get('date_risk')} thinking={r.get('thinking_trace_risk')}")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"jsonl={out_jsonl}")
    print(f"markdown={out_md}")


if __name__ == "__main__":
    main()
