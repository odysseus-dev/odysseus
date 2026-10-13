#!/usr/bin/env python3
"""Use Kimi to produce repaired SFT transcripts for audited email sessions.

The script does not mutate chat history. It writes a repair artifact that can be
reviewed and fed into an exporter.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "app.db"
AUDIT_DIR = ROOT / "data" / "audits"


def decrypt_secret(value: str) -> str:
    if not value or not value.startswith("enc:"):
        return value or ""
    key = (ROOT / "data" / ".app_key").read_bytes()
    return Fernet(key).decrypt(value[len("enc:") :].encode("ascii")).decode("utf-8")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def endpoint(con: sqlite3.Connection, endpoint_id: str, model: str) -> dict[str, str]:
    row = con.execute(
        """
        SELECT id, name, base_url, api_key
        FROM model_endpoints
        WHERE id = ? AND COALESCE(api_key, '') != ''
        """,
        (endpoint_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"missing endpoint {endpoint_id}")
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "api_key": decrypt_secret(row["api_key"]),
        "model": model,
    }


def compact_tool_event(ev: dict[str, Any]) -> dict[str, Any]:
    out = str(ev.get("output") or "")
    return {
        "tool": ev.get("tool"),
        "command": ev.get("command"),
        "output": out[:1600] + ("..." if len(out) > 1600 else ""),
        "exit_code": ev.get("exit_code"),
    }


def session_payload(con: sqlite3.Connection, sid: str) -> dict[str, Any]:
    s = con.execute(
        "SELECT id, name, created_at, updated_at FROM sessions WHERE id = ?",
        (sid,),
    ).fetchone()
    messages = []
    for m in con.execute(
        "SELECT id, role, content, metadata, timestamp FROM chat_messages WHERE session_id = ? ORDER BY timestamp, id",
        (sid,),
    ):
        meta: dict[str, Any] = {}
        if m["metadata"]:
            try:
                meta = json.loads(m["metadata"])
            except json.JSONDecodeError:
                meta = {}
        thinking = meta.get("thinking")
        if isinstance(thinking, str):
            thinking = thinking[:1200] + ("..." if len(thinking) > 1200 else "")
        messages.append(
            {
                "message_id": m["id"],
                "role": m["role"],
                "timestamp": m["timestamp"],
                "content": (m["content"] or "")[:3000],
                "thinking": thinking,
                "tool_events": [compact_tool_event(ev) for ev in meta.get("tool_events") or []],
            }
        )
    return {"session": dict(s), "messages": messages}


def latest_audit(pattern: str = "email_sft_deepseek_audit_*.jsonl") -> Path:
    paths = sorted(AUDIT_DIR.glob(pattern))
    if not paths:
        raise RuntimeError(f"no DeepSeek audit JSONL found for {pattern}")
    return paths[-1]


def load_targets(path: Path, verdicts: set[str], limit: int) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    targets = [r for r in rows if r.get("verdict") in verdicts]
    targets.sort(key=lambda r: (r.get("trainable_score") or 999, r.get("session_name") or ""))
    return targets[:limit]


def load_existing_repair_sessions(paths: list[Path]) -> set[str]:
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = row.get("session_id")
            if isinstance(sid, str) and sid:
                seen.add(sid)
    return seen


def prompt(target: dict[str, Any], session: dict[str, Any]) -> list[dict[str, str]]:
    system = """You repair Odysseus email-agent SFT traces.
Return strict JSON only with this shape:
{
  "session_id": "...",
  "repair_decision": "repair" | "exclude",
  "sft_quality_after_repair": 0-100,
  "repair_summary": "...",
  "messages": [
    {"role":"user"|"assistant"|"tool", "content":"...", "thinking":"optional short clean rationale", "tool_events":[... optional existing/corrected tool events ...]}
  ],
  "export_notes": ["..."]
}

Rules:
- Do not invent tool events that contradict the provided tool outputs.
- If an action was claimed but no tool event exists and you cannot repair by changing the assistant wording, set repair_decision="exclude".
- Prefer deleting bad branches, duplicate resend turns, stale-loop turns, and false tool-unavailable turns.
- Preserve useful successful tool-use turns.
- Assistant content must match the tool events exactly.
- Relative dates must include explicit current-date context or explicit tool date bounds.
- Clean thinking traces are allowed, but remove references to fake fixtures, harness bugs, injected/untrusted source data, or false tool unavailability.
- If user asks to send and only a draft exists, either rewrite assistant to say draft only, or exclude if that would fail the user request.
- Keep the repaired transcript concise and trainable."""
    user = {
        "current_date": "2026-08-24",
        "timezone": "UTC",
        "audit_verdict": target,
        "original_session": session,
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


def call_kimi(ep: dict[str, str], target: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "model": ep["model"],
        "messages": prompt(target, session),
        "temperature": 0,
        "max_tokens": 7000,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        ep["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {ep['api_key']}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"]
    return json.loads(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--verdict", action="append", choices=["repair", "delete"], default=None)
    ap.add_argument("--session-id", action="append", default=None)
    ap.add_argument("--endpoint-id", default="f3904562")
    ap.add_argument("--model", default="moonshotai/kimi-k3")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    con = db()
    ep = endpoint(con, args.endpoint_id, args.model)
    audit = args.audit or latest_audit()
    verdicts = set(args.verdict or ["repair"])
    targets = load_targets(audit, verdicts, args.limit)
    if args.session_id:
        wanted = set(args.session_id)
        targets = [target for target in targets if target.get("session_id") in wanted]
    if args.skip_existing:
        existing = load_existing_repair_sessions(sorted(AUDIT_DIR.glob("email_sft_kimi_repairs_*.jsonl")))
        targets = [target for target in targets if target.get("session_id") not in existing]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = AUDIT_DIR / f"email_sft_kimi_repairs_{stamp}.jsonl"

    for idx, target in enumerate(targets, 1):
        sid = target["session_id"]
        session = session_payload(con, sid)
        for attempt in range(3):
            try:
                repaired = call_kimi(ep, target, session)
                break
            except Exception as exc:
                if attempt == 2:
                    repaired = {
                        "session_id": sid,
                        "repair_decision": "exclude",
                        "sft_quality_after_repair": 0,
                        "repair_summary": f"Kimi repair failed: {exc}",
                        "messages": [],
                        "export_notes": ["Repair call failed; exclude until manually reviewed."],
                    }
                else:
                    time.sleep(3 + attempt * 5)
        repaired.setdefault("session_id", sid)
        repaired["source_audit"] = target
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(repaired, ensure_ascii=False) + "\n")
        print(f"repaired {idx}/{len(targets)} {sid} -> {repaired.get('repair_decision')}")

    print(out)


if __name__ == "__main__":
    main()
