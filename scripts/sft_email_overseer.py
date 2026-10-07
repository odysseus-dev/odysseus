#!/usr/bin/env python3
"""Email SFT overseer: expand curated seed traces across coherent fixture envs.

This script is intentionally conservative:
- it can enrich target users' fixture mailboxes from Alex's richer mailbox;
- it builds a run plan from audited keep rows plus Kimi/manual repairs;
- it does not mutate chat history or run the harness unless a future run
  subcommand is added explicitly.
"""

from __future__ import annotations
import os

import argparse
import copy
import json
import re
import sqlite3
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "app.db"
FIXTURE = ROOT / "data" / "fixture_email_messages.json"
AUDIT_DIR = ROOT / "data" / "audits"
OUT_DIR = ROOT / "data" / "evals"
DEFAULT_BASE_URL = "http://127.0.0.1:7011"
DEFAULT_PASSWORD = os.environ["ODYSSEUS_QA_PASSWORD"]
DEFAULT_ENDPOINT_ID = "f3904562"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "moonshotai/kimi-k3"


SOURCE_OWNER = "sft_alex_creator"
TARGET_OWNERS = ["sft_maya_ops", "sft_jules_research", "sft_nora_design", "sft_omar_finance"]


PROFILES: dict[str, dict[str, str]] = {
    "sft_alex_creator": {
        "name": "Alex Rowan",
        "first": "Alex",
        "primary": "fixture-06@example.test",
        "secondary": "fixture-02@example.test",
        "primary_account": "Primary Inbox",
        "secondary_account": "Research Mail",
        "topic": "creator operations",
        "org": "Rowan Studio",
        "domain": "rowan.studio",
        "secondary_domain": "northstar-research.co",
    },
    "sft_maya_ops": {
        "name": "Maya Chen",
        "first": "Maya",
        "primary": "fixture-07@example.test",
        "secondary": "fixture-04@example.test",
        "primary_account": "Primary Inbox",
        "secondary_account": "Ops Research",
        "topic": "operations planning",
        "org": "Northstar Ops",
        "domain": "northstar-ops.co",
        "secondary_domain": "northstar-research.co",
    },
    "sft_jules_research": {
        "name": "Jules Rivera",
        "first": "Jules",
        "primary": "fixture-09@example.test",
        "secondary": "fixture-08@example.test",
        "primary_account": "Primary Inbox",
        "secondary_account": "Research Mail",
        "topic": "research synthesis",
        "org": "Rivera Lab",
        "domain": "rivera-lab.org",
        "secondary_domain": "northstar-research.co",
    },
    "sft_nora_design": {
        "name": "Nora Patel",
        "first": "Nora",
        "primary": "fixture-10@example.test",
        "secondary": "fixture-01@example.test",
        "primary_account": "Primary Inbox",
        "secondary_account": "Design Research",
        "topic": "product design",
        "org": "Northpier Design",
        "domain": "northpier.design",
        "secondary_domain": "northstar-research.co",
    },
    "sft_omar_finance": {
        "name": "Omar Singh",
        "first": "Omar",
        "primary": "fixture-03@example.test",
        "secondary": "fixture-11@example.test",
        "primary_account": "Primary Inbox",
        "secondary_account": "Finance Research",
        "topic": "finance analysis",
        "org": "Bayledger Finance",
        "domain": "bayledger.finance",
        "secondary_domain": "northstar-research.co",
    },
}


SENDER_DOMAIN_MAP = {
    "collab.rowan.studio": "collab.{domain}",
    "metrics.rowan.studio": "metrics.{domain}",
    "rowan.studio": "{domain}",
    "mail.rowan.studio": "mail.{domain}",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def latest_deepseek_audit() -> Path:
    paths = sorted(AUDIT_DIR.glob("email_sft_deepseek_audit_sft_alex_creator_*.jsonl"))
    if not paths:
        raise RuntimeError("No DeepSeek email audit found")
    return paths[-1]


def repair_artifact_paths() -> list[Path]:
    return sorted(AUDIT_DIR.glob("email_sft_kimi_repairs_*.jsonl")) + sorted(
        AUDIT_DIR.glob("email_sft_kimi_repairs_manual_date_*.jsonl")
    )


def fixture_rows() -> list[dict[str, Any]]:
    payload = read_json(FIXTURE)
    rows = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected fixture shape: {type(payload).__name__}")
    return rows


def save_fixture_rows(rows: list[dict[str, Any]]) -> None:
    write_json(FIXTURE, {"messages": rows})


def owner_counts(rows: list[dict[str, Any]]) -> Counter:
    return Counter(str(row.get("owner") or "") for row in rows)


def account_counts(rows: list[dict[str, Any]]) -> dict[str, Counter]:
    out: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        owner = str(row.get("owner") or "")
        account = str(row.get("account") or row.get("account_id") or "Primary Inbox")
        out[owner][account] += 1
    return out


def transform_text(text: str, target_owner: str) -> str:
    src = PROFILES[SOURCE_OWNER]
    tgt = PROFILES[target_owner]
    replacements = {
        src["name"]: tgt["name"],
        src["first"]: tgt["first"],
        src["primary"]: tgt["primary"],
        src["secondary"]: tgt["secondary"],
        src["topic"]: tgt["topic"],
        src["org"]: tgt["org"],
        "creator operations": tgt["topic"],
        "creator ops": tgt["topic"],
        "creator cohort": "workstream cohort",
        "creator": "workstream",
        "Rowan Studio": tgt["org"],
        "rowan.studio": tgt["domain"],
        "alex-rowan": f"{tgt['first'].lower()}-{tgt['name'].split()[-1].lower()}",
    }
    out = text
    for old, new in replacements.items():
        out = out.replace(old, new)
    return out


def transform_email_address(addr: str, target_owner: str) -> str:
    tgt = PROFILES[target_owner]
    out = addr
    for old_domain, new_template in SENDER_DOMAIN_MAP.items():
        out = out.replace(old_domain, new_template.format(domain=tgt["domain"]))
    return out


def retarget_row(row: dict[str, Any], target_owner: str, uid_offset: int) -> dict[str, Any]:
    tgt = PROFILES[target_owner]
    cloned = copy.deepcopy(row)
    source_uid = str(row.get("uid") or "")
    try:
        new_uid = str(uid_offset + int(source_uid))
    except ValueError:
        source_uid_suffix = re.sub(r"\W+", "", source_uid)[:8]
        new_uid = f"{uid_offset}{source_uid_suffix}"

    cloned["owner"] = target_owner
    cloned["uid"] = new_uid
    cloned["source_seed_owner"] = SOURCE_OWNER
    cloned["source_seed_uid"] = source_uid
    cloned["overseer_generated"] = True
    cloned["overseer_version"] = 1

    account_id = str(row.get("account_id") or "primary-inbox")
    if account_id == "research-mail":
        cloned["account_id"] = "research-mail"
        cloned["account"] = tgt["secondary_account"]
        cloned["account_email"] = tgt["secondary"]
        cloned["to"] = f"{tgt['name']} <{tgt['secondary']}>"
    else:
        cloned["account_id"] = "primary-inbox"
        cloned["account"] = tgt["primary_account"]
        cloned["account_email"] = tgt["primary"]
        cloned["to"] = f"{tgt['name']} <{tgt['primary']}>"

    for key in ["subject", "summary", "body", "message_id", "references"]:
        if isinstance(cloned.get(key), str):
            cloned[key] = transform_text(cloned[key], target_owner)
    for key in ["from", "sender"]:
        if isinstance(cloned.get(key), str):
            cloned[key] = transform_email_address(transform_text(cloned[key], target_owner), target_owner)

    if cloned.get("message_id"):
        cloned["message_id"] = f"<sft-overseer-{target_owner}-{new_uid}@mail.{tgt['domain']}>"

    for att in cloned.get("attachments") or []:
        if isinstance(att, dict):
            for key in ["filename", "content"]:
                if isinstance(att.get(key), str):
                    att[key] = transform_text(att[key], target_owner)

    return cloned


def seed_target_fixtures(targets: list[str], *, dry_run: bool = False) -> dict[str, Any]:
    rows = fixture_rows()
    source_rows = [
        row for row in rows
        if row.get("owner") == SOURCE_OWNER and not row.get("overseer_generated")
    ]
    before = owner_counts(rows)
    kept = [
        row for row in rows
        if not (row.get("owner") in targets and row.get("overseer_generated"))
    ]
    generated: list[dict[str, Any]] = []
    for idx, target in enumerate(targets, start=1):
        offset = 1000 * idx
        generated.extend(retarget_row(row, target, offset) for row in source_rows)
    after_rows = kept + generated
    after = owner_counts(after_rows)
    summary = {
        "source_owner": SOURCE_OWNER,
        "source_rows": len(source_rows),
        "targets": targets,
        "removed_old_generated": len(rows) - len(kept),
        "generated_rows": len(generated),
        "before_counts": dict(sorted(before.items())),
        "after_counts": dict(sorted(after.items())),
        "dry_run": dry_run,
    }
    if not dry_run:
        backup = FIXTURE.with_suffix(f".json.bak-{time.strftime('%Y%m%d_%H%M%S')}")
        backup.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        save_fixture_rows(after_rows)
        summary["backup"] = str(backup)
    return summary


def load_audit_rows() -> list[dict[str, Any]]:
    return [json.loads(line) for line in latest_deepseek_audit().read_text(encoding="utf-8").splitlines() if line.strip()]


def load_repair_rows() -> dict[str, dict[str, Any]]:
    repairs: dict[str, dict[str, Any]] = {}
    for path in repair_artifact_paths():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            sid = str(row.get("session_id") or "")
            if sid:
                repairs[sid] = row
    return repairs


def session_user_messages(session_id: str) -> list[str]:
    con = db()
    try:
        return [
            str(row["content"] or "")
            for row in con.execute(
                "SELECT content FROM chat_messages WHERE session_id = ? AND role = 'user' ORDER BY timestamp, id",
                (session_id,),
            )
            if str(row["content"] or "").strip()
        ]
    finally:
        con.close()


def usable_seed_records(min_keep_score: int = 0) -> list[dict[str, Any]]:
    audit_rows = load_audit_rows()
    repairs = load_repair_rows()
    seeds: list[dict[str, Any]] = []
    for row in audit_rows:
        sid = str(row.get("session_id") or "")
        verdict = row.get("verdict")
        score = int(row.get("trainable_score") or 0)
        if verdict == "keep" and score >= min_keep_score:
            users = session_user_messages(sid)
            seeds.append({
                "session_id": sid,
                "source": "keep",
                "score": score,
                "session_name": row.get("session_name"),
                "user_messages": users,
                "first_user": users[0] if users else "",
            })
        elif verdict == "repair":
            repair = repairs.get(sid)
            if repair and repair.get("repair_decision") == "repair":
                users = [
                    str(m.get("content") or "")
                    for m in repair.get("messages") or []
                    if m.get("role") == "user" and str(m.get("content") or "").strip()
                ]
                seeds.append({
                    "session_id": sid,
                    "source": "repair",
                    "score": int(repair.get("sft_quality_after_repair") or score),
                    "session_name": row.get("session_name"),
                    "user_messages": users,
                    "first_user": users[0] if users else "",
                })
    seeds.sort(key=lambda item: (-int(item["score"]), str(item["session_name"] or "")))
    return seeds


CONTEXTLESS_FIRST_TURN_RE = re.compile(
    r"^\s*(?:"
    r"yes\b|yeah\b|ok\b|okay\b|open (?:it|the att|the attachment)\b|"
    r"read (?:it|the att|the attachment)\b|"
    r"reply\b|draft reply\b|"
    r".*\bthis email\b|.*\bthat email\b|.*\bopen it\b|.*\bthe attachment\b"
    r")",
    re.IGNORECASE,
)


def seed_is_standalone(seed: dict[str, Any]) -> bool:
    first = str(seed.get("first_user") or "").strip()
    if not first:
        return False
    if CONTEXTLESS_FIRST_TURN_RE.search(first):
        return False
    return True


def retarget_prompt(text: str, target_owner: str) -> str:
    out = transform_text(text, target_owner)
    target = PROFILES[target_owner]
    # Keep prompts natural: "Alex" references inside user text should become the
    # target user, but sender names such as Casey/Priya/Dana remain stable because
    # matching fixture rows are generated for those senders.
    out = out.replace(PROFILES[SOURCE_OWNER]["first"], target["first"])
    return out


def build_plan(targets: list[str], per_target: int, min_keep_score: int) -> dict[str, Any]:
    all_seeds = usable_seed_records(min_keep_score=min_keep_score)
    seeds = [seed for seed in all_seeds if seed_is_standalone(seed)]
    if not seeds:
        raise RuntimeError("No usable seeds found. Run audit/repair first.")
    cases: list[dict[str, Any]] = []
    for target in targets:
        for idx, seed in enumerate(seeds[:per_target], start=1):
            turns = [retarget_prompt(msg, target) for msg in seed["user_messages"]]
            cases.append({
                "id": f"email_overseer_{target}_{idx:03d}_{seed['session_id'][:8]}",
                "domain": "email",
                "owner": target,
                "source_owner": SOURCE_OWNER,
                "source_session_id": seed["session_id"],
                "source_type": seed["source"],
                "source_score": seed["score"],
                "session_name": seed["session_name"],
                "turns": turns,
                "current_date": "2026-08-24",
                "timezone": "UTC",
                "fixture_requirements": {
                    "mailbox_seeded_from": SOURCE_OWNER,
                    "target_primary": PROFILES[target]["primary"],
                    "target_secondary": PROFILES[target]["secondary"],
                },
                "acceptance": {
                    "must_use_email_tool": True,
                    "reject_bad_unavailable_answer": True,
                    "reject_claimed_action_without_tool": True,
                    "judge_with_deepseek": True,
                    "repair_with_kimi": True,
                },
            })
    return {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_owner": SOURCE_OWNER,
        "targets": targets,
        "per_target": per_target,
        "seed_count_available": len(seeds),
        "seed_count_before_standalone_filter": len(all_seeds),
        "seed_count_skipped_contextual_first_turn": len(all_seeds) - len(seeds),
        "case_count": len(cases),
        "cases": cases,
    }


def write_plan(plan: dict[str, Any]) -> Path:
    path = OUT_DIR / f"sft_email_overseer_plan_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.json"
    write_json(path, plan)
    return path


def login(client: httpx.Client, base_url: str, username: str, password: str) -> None:
    res = client.post(
        base_url.rstrip("/") + "/api/auth/login",
        json={"username": username, "password": password, "remember": True},
        timeout=30,
    )
    res.raise_for_status()
    if not res.json().get("ok"):
        raise RuntimeError(f"login failed for {username}: {res.text[:300]}")


def create_session(
    client: httpx.Client,
    *,
    base_url: str,
    owner: str,
    case_id: str,
    endpoint: str,
    endpoint_id: str,
    model: str,
) -> str:
    res = client.post(
        base_url.rstrip("/") + "/api/session",
        data={
            "name": f"SFT email overseer {owner} {case_id}",
            "endpoint_url": endpoint,
            "endpoint_id": endpoint_id,
            "model": model,
            "skip_validation": "true",
            "rag": "false",
        },
        timeout=30,
    )
    res.raise_for_status()
    return str(res.json()["id"])


def sse_events(response: httpx.Response) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_name = "message"
    data_lines: list[str] = []
    for raw in response.iter_lines():
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        if line == "":
            if data_lines:
                raw_data = "\n".join(data_lines)
                try:
                    payload = json.loads(raw_data)
                except json.JSONDecodeError:
                    payload = {"type": event_name, "raw": raw_data}
                events.append(payload)
            event_name = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    if data_lines:
        raw_data = "\n".join(data_lines)
        try:
            events.append(json.loads(raw_data))
        except json.JSONDecodeError:
            events.append({"type": event_name, "raw": raw_data})
    return events


def event_text(event: dict[str, Any]) -> str:
    for key in ("content", "text", "response", "message", "output"):
        value = event.get(key)
        if isinstance(value, str):
            return value
    return ""


def stream_turn(
    client: httpx.Client,
    *,
    base_url: str,
    session_id: str,
    message: str,
    endpoint: str,
    endpoint_id: str,
    model: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], str]:
    form = {
        "message": message,
        "session": session_id,
        "mode": "agent",
        "agent_prompt_mode": "auto",
        "selected_endpoint_id": endpoint_id,
        "selected_endpoint_url": endpoint,
        "selected_model": model,
        "client_runtime_context": json.dumps({"timezone": "UTC", "tz_offset_min": 0}, separators=(",", ":")),
    }
    with client.stream(
        "POST",
        base_url.rstrip("/") + "/api/chat_stream",
        data=form,
        headers={"Accept": "text/event-stream", "X-Tz-Name": "UTC", "X-Tz-Offset": "0"},
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        events = sse_events(response)
    final = ""
    parts: list[str] = []
    for event in events:
        typ = str(event.get("type") or "")
        text = event_text(event)
        if not text:
            continue
        if typ == "final_response":
            final = text
        elif typ in {"token", "content", "assistant_delta", "message"}:
            parts.append(text)
    return events, (final or "".join(parts)).strip()


BAD_ANSWER_RE = re.compile(
    r"\b(?:can't|cannot|don't have|do not have|not available|no .*tool|enable .*integration|setup .*integration|"
    r"invalid credentials|not authenticated|i can only|i'm unable)\b",
    re.IGNORECASE,
)


def tool_names(events: list[dict[str, Any]]) -> list[str]:
    names = []
    for event in events:
        if event.get("type") == "tool_start" and event.get("tool"):
            names.append(str(event["tool"]))
        elif event.get("tool") and str(event.get("type") or "").startswith("tool"):
            names.append(str(event["tool"]))
    return names


def assistant_count(session_id: str) -> int:
    con = db()
    try:
        return int(con.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ? AND role = 'assistant'",
            (session_id,),
        ).fetchone()[0])
    finally:
        con.close()


def latest_assistant_from_db(session_id: str, min_count: int) -> dict[str, Any]:
    con = db()
    try:
        rows = list(con.execute(
            """
            SELECT content, metadata, timestamp
            FROM chat_messages
            WHERE session_id = ? AND role = 'assistant'
            ORDER BY timestamp, id
            """,
            (session_id,),
        ))
    finally:
        con.close()
    if len(rows) <= min_count:
        return {"content": "", "tool_events": [], "thinking": ""}
    row = rows[-1]
    meta: dict[str, Any] = {}
    if row["metadata"]:
        try:
            meta = json.loads(row["metadata"])
        except json.JSONDecodeError:
            meta = {}
    return {
        "content": str(row["content"] or ""),
        "tool_events": list(meta.get("tool_events") or []),
        "thinking": str(meta.get("thinking") or ""),
    }


def persisted_tool_names(tool_events: list[dict[str, Any]]) -> list[str]:
    return [str(ev.get("tool") or "") for ev in tool_events if ev.get("tool")]


def score_run(case: dict[str, Any], turns: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    all_events = [event for turn in turns for event in turn.get("events", [])]
    all_tools = [
        name
        for turn in turns
        for name in (turn.get("persisted_tool_names") or turn.get("tool_names") or [])
    ]
    combined_answer = "\n".join(str(turn.get("answer") or "") for turn in turns)
    if any(str(event.get("type") or "") in {"error", "parse_error"} for event in all_events):
        failures.append("stream_error")
    if BAD_ANSWER_RE.search(combined_answer):
        failures.append("bad_unavailable_answer")
    if case.get("acceptance", {}).get("must_use_email_tool") and not any("email" in name for name in all_tools):
        failures.append(f"missing_email_tool tools={all_tools}")
    return not failures, failures


def run_plan(args: argparse.Namespace) -> dict[str, Any]:
    plan = read_json(Path(args.plan))
    cases = list(plan.get("cases") or [])
    if args.owner:
        owners = set(parse_targets(args.owner))
        cases = [case for case in cases if case.get("owner") in owners]
    cases = cases[args.offset : args.offset + args.limit]
    results: list[dict[str, Any]] = []
    clients: dict[str, httpx.Client] = {}
    try:
        for case in cases:
            owner = str(case["owner"])
            client = clients.get(owner)
            if client is None:
                client = httpx.Client(follow_redirects=True)
                login(client, args.base_url, owner, args.password)
                clients[owner] = client
            session_id = create_session(
                client,
                base_url=args.base_url,
                owner=owner,
                case_id=case["id"],
                endpoint=args.endpoint,
                endpoint_id=args.endpoint_id,
                model=args.model,
            )
            turn_results: list[dict[str, Any]] = []
            started = time.time()
            error = ""
            try:
                for message in case.get("turns") or []:
                    before = assistant_count(session_id)
                    events, streamed_answer = stream_turn(
                        client,
                        base_url=args.base_url,
                        session_id=session_id,
                        message=message,
                        endpoint=args.endpoint,
                        endpoint_id=args.endpoint_id,
                        model=args.model,
                        timeout=args.timeout,
                    )
                    persisted = latest_assistant_from_db(session_id, before)
                    answer = persisted["content"] or streamed_answer
                    ptools = persisted_tool_names(persisted["tool_events"])
                    turn_results.append({
                        "user": message,
                        "answer": answer,
                        "events": events,
                        "tool_names": tool_names(events),
                        "persisted_tool_names": ptools,
                        "persisted_tool_events": persisted["tool_events"],
                    })
                passed, failures = score_run(case, turn_results)
            except Exception as exc:
                error = repr(exc)
                passed = False
                failures = [f"exception: {error}"]
            results.append({
                "id": case["id"],
                "owner": owner,
                "source_session_id": case.get("source_session_id"),
                "session_id": session_id,
                "pass": passed,
                "failures": failures,
                "turns": [
                    {
                        "user": turn["user"],
                        "answer": turn["answer"],
                        "tool_names": turn.get("persisted_tool_names") or turn["tool_names"],
                    }
                    for turn in turn_results
                ],
                "elapsed_seconds": round(time.time() - started, 3),
                "error": error,
            })
    finally:
        for client in clients.values():
            client.close()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "plan": str(args.plan),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": results,
        "summary": dict(Counter("pass" if row["pass"] else "fail" for row in results)),
    }
    write_json(out_dir / "actual_results.json", payload)
    return payload


def status() -> dict[str, Any]:
    rows = fixture_rows()
    audit_rows = load_audit_rows() if latest_deepseek_audit().exists() else []
    repairs = load_repair_rows()
    repair_counts = Counter(row.get("repair_decision") for row in repairs.values())
    usable = usable_seed_records()
    return {
        "fixture_counts": dict(sorted(owner_counts(rows).items())),
        "fixture_accounts": {owner: dict(counter) for owner, counter in sorted(account_counts(rows).items())},
        "audit_counts": dict(Counter(row.get("verdict") for row in audit_rows)),
        "repair_artifact_counts": dict(repair_counts),
        "usable_seed_count": len(usable),
        "target_owners": TARGET_OWNERS,
    }


def parse_targets(raw: str) -> list[str]:
    if raw == "all":
        return list(TARGET_OWNERS)
    targets = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [target for target in targets if target not in PROFILES or target == SOURCE_OWNER]
    if unknown:
        raise SystemExit(f"Unknown/non-target owners: {unknown}")
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description="Oversee email SFT fixture expansion and plan generation.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    seed = sub.add_parser("seed-fixtures")
    seed.add_argument("--targets", default="all", help="Comma list of target owners or 'all'")
    seed.add_argument("--dry-run", action="store_true")

    plan = sub.add_parser("build-plan")
    plan.add_argument("--targets", default="all", help="Comma list of target owners or 'all'")
    plan.add_argument("--per-target", type=int, default=95)
    plan.add_argument("--min-keep-score", type=int, default=0)

    run = sub.add_parser("run-plan")
    run.add_argument("--plan", required=True)
    run.add_argument("--owner", default="", help="Optional comma list of owners to run")
    run.add_argument("--offset", type=int, default=0)
    run.add_argument("--limit", type=int, default=4)
    run.add_argument("--base-url", default=DEFAULT_BASE_URL)
    run.add_argument("--password", default=DEFAULT_PASSWORD)
    run.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    run.add_argument("--endpoint-id", default=DEFAULT_ENDPOINT_ID)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=90)
    run.add_argument("--out-dir", default=str(OUT_DIR / f"sft_email_overseer_run_{time.strftime('%Y%m%d_%H%M%S')}"))

    args = parser.parse_args()
    if args.cmd == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=True))
        return 0
    if args.cmd == "seed-fixtures":
        summary = seed_target_fixtures(parse_targets(args.targets), dry_run=args.dry_run)
        print(json.dumps(summary, indent=2, ensure_ascii=True))
        return 0
    if args.cmd == "build-plan":
        built = build_plan(parse_targets(args.targets), args.per_target, args.min_keep_score)
        path = write_plan(built)
        print(json.dumps({"plan": str(path), "case_count": built["case_count"], "targets": built["targets"]}, indent=2))
        return 0
    if args.cmd == "run-plan":
        payload = run_plan(args)
        print(json.dumps({"summary": payload["summary"], "out": str(Path(args.out_dir) / "actual_results.json")}, indent=2))
        return 0 if payload["summary"].get("fail", 0) == 0 else 1
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
