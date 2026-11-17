#!/usr/bin/env python3
"""Build an accountable harness/SFT seed corpus from historical SFT sessions."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXCLUDED_PREFIXES = ("[harness-qa]",)
FAMILY_ALIASES = {
    "cookbook": "cookbook_admin",
    "shell_files": "shell_files",
    "search": "search_browser",
    "search_ai": "search_browser",
}
CANONICAL_FAMILIES = {
    "calendar", "notes", "email", "memory", "documents", "tasks", "skills",
    "search_browser", "cookbook_admin", "shell_files", "research", "ui", "switching",
}


def case_name(session_name: str) -> str:
    return session_name.split("]", 1)[-1].strip()


def infer_family(name: str) -> str:
    value = case_name(name).casefold()
    value = re.sub(r"^(?:typo|ambiguous|related)[-_]", "", value)
    if "_to_" in value or value.startswith("greeting_to_"):
        return "switching"
    if value.startswith(("browser", "news_followup", "search_")):
        return "search_browser"
    stem = re.split(r"[-_]\d", value, maxsplit=1)[0]
    if stem in CANONICAL_FAMILIES:
        return stem
    for alias, family in FAMILY_ALIASES.items():
        if stem == alias or value.startswith(alias + "-"):
            return family
    return "unknown"


def infer_text_family(text: str) -> str:
    value = re.sub(r"\s+", " ", text).casefold()
    groups = (
        ("calendar", ("calendar", "event", "schedule", "appointment", "meeting")),
        ("notes", ("note", "checklist")),
        ("email", ("email", "inbox", "sender", "unsubscribe", "spam")),
        ("memory", ("memory", "remember", "forget")),
        ("documents", ("document", "write reply", "write this", "editor")),
        ("tasks", ("task", "scheduled job", "cron")),
        ("skills", ("skill",)),
        ("research", ("research",)),
        ("cookbook_admin", ("model server", "endpoint", "runpod", "served model", "cookbook")),
        ("shell_files", ("workspace", "file", "folder", "directory", "bash", "python", "ssh")),
        ("ui", ("open gallery", "open panel", "theme")),
        ("search_browser", ("http://", "https://", "search", "look up", "browse", "website", "latest", "weather", "news")),
    )
    matched = [family for family, words in groups if any(word in value for word in words)]
    if len(set(matched)) > 1:
        return "switching"
    return matched[0] if matched else "general"


def infer_turn_family(session_family: str, turn: dict[str, Any]) -> str:
    """Prefer observed tool/contract evidence over unreliable session titles."""
    metadata = turn.get("metadata") or {}
    names = {
        str(event.get("tool") or "")
        for event in (metadata.get("tool_events") or [])
        if isinstance(event, dict)
    }
    contract = metadata.get("turn_contract") or {}
    capabilities = contract.get("capabilities") or metadata.get("capabilities") or []
    hints = " ".join(sorted(names | {str(value) for value in capabilities})).casefold()
    mappings = (
        (("calendar", "manage_calendar"), "calendar"),
        (("notes", "manage_notes"), "notes"),
        (("email", "inbox", "draft_email"), "email"),
        (("memory", "manage_memory"), "memory"),
        (("document", "manage_documents"), "documents"),
        (("task", "manage_tasks"), "tasks"),
        (("skill", "manage_skills"), "skills"),
        (("research", "trigger_research"), "research"),
        (("browser", "web_search", "web_fetch", "youtube"), "search_browser"),
        (("cookbook", "served_model", "cached_model", "endpoint"), "cookbook_admin"),
        (("shell", "bash", "read_file", "write_file", "\bls\b"), "shell_files"),
        (("ui_control",), "ui"),
    )
    matched = [family for needles, family in mappings if any(needle in hints for needle in needles)]
    if len(set(matched)) > 1:
        return "switching"
    if matched:
        return matched[0]
    if session_family != "unknown":
        return session_family
    return infer_text_family(str(turn.get("user") or ""))


def normalized_flow_key(turns: list[dict[str, Any]]) -> str:
    texts = []
    for turn in turns:
        text = re.sub(r"\s+", " ", str(turn.get("user") or "")).strip().casefold()
        texts.append(text)
    return "\n".join(texts)


def event_failed(event: dict[str, Any]) -> bool:
    return bool(event.get("error") or event.get("exit_code") not in (None, 0))


def classify(turns: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Conservative historical triage; replay resolves everything uncertain."""
    reasons: list[str] = []
    backend = False
    harness = False
    model_sft = False
    successful_tool = False
    for index, turn in enumerate(turns):
        assistant = str(turn.get("assistant") or "")
        metadata = turn.get("metadata") or {}
        events = metadata.get("tool_events") or []
        successful_tool |= any(not event_failed(event) for event in events)
        combined_errors = "\n".join(
            str(event.get("error") or "") + "\n" + str(event.get("output") or "")
            for event in events if event_failed(event)
        )
        if re.search(r"connection refused|timed? out|backend unavailable|service unavailable", combined_errors, re.I):
            backend = True
            reasons.append(f"turn {index + 1}: tool/backend transport failed")
        denied = any(
            isinstance(decision, dict) and decision.get("allowed") is False
            for decision in (metadata.get("policy_decisions") or [])
        )
        if metadata.get("required_operation_succeeded") is False or denied:
            harness = True
            reasons.append(f"turn {index + 1}: harness policy or required operation blocked execution")
        if index and re.search(r"no preceding (?:answer|message)|not in this conversation", assistant, re.I):
            harness = True
            reasons.append(f"turn {index + 1}: prior conversation state was lost")
        if successful_tool and re.search(
            r"(?:cannot|can't|unable to) (?:access|view|open|read|use).{0,40}(?:notes?|calendar|emails?|tasks?|documents?)",
            assistant,
            re.I,
        ):
            model_sft = True
            reasons.append(f"turn {index + 1}: response contradicted successful tool evidence")
        if any(event_failed(event) and re.search(
            r"placeholder|not returned by|invalid arguments?|validation|must be an exact",
            str(event.get("error") or "") + str(event.get("output") or ""), re.I,
        ) for event in events):
            model_sft = True
            reasons.append(f"turn {index + 1}: model proposed invalid or ungrounded arguments")
    if backend:
        return "backend", sorted(set(reasons))
    if harness:
        return "harness", sorted(set(reasons))
    if model_sft:
        return "model_sft", sorted(set(reasons))
    return "replay_first", ["historical result is not sufficient for a reliable owner classification"]


def load_sessions(db_path: Path, owner: str) -> list[dict[str, Any]]:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    sessions = db.execute(
        "SELECT id, name, created_at FROM sessions WHERE owner=? ORDER BY created_at DESC",
        (owner,),
    ).fetchall()
    output = []
    for session in sessions:
        if any(str(session["name"] or "").startswith(prefix) for prefix in EXCLUDED_PREFIXES):
            continue
        rows = db.execute(
            "SELECT role, content, metadata FROM chat_messages WHERE session_id=? ORDER BY timestamp, rowid",
            (session["id"],),
        ).fetchall()
        turns = []
        pending = None
        for row in rows:
            if row["role"] == "user":
                pending = {"user": row["content"], "assistant": "", "metadata": {}}
                turns.append(pending)
            elif row["role"] == "assistant" and pending is not None:
                pending["assistant"] = row["content"]
                try:
                    pending["metadata"] = json.loads(row["metadata"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    pending["metadata"] = {}
                pending = None
        if turns:
            session_family = infer_family(session["name"])
            for turn in turns:
                turn["family"] = infer_turn_family(session_family, turn)
            output.append({
                "source_session_id": session["id"],
                "source_name": session["name"],
                "created_at": session["created_at"],
                "family": session_family,
                "turns": turns,
            })
    db.close()
    return output


def build_seeds(sessions: list[dict[str, Any]], context_turns: int = 3) -> list[dict[str, Any]]:
    """Create exactly one teacher seed for every historical user turn.

    A seed retains preceding user context so ambiguous follow-ups remain
    ambiguous in the same useful way. Repeated source runs are intentionally
    retained; they measure stability instead of disappearing via deduplication.
    """
    seeds: list[dict[str, Any]] = []
    for session in sessions:
        turns = session["turns"]
        for index, turn in enumerate(turns):
            start = max(0, index - context_turns)
            context = [
                {"user": item["user"]}
                for item in turns[start:index + 1]
            ]
            seeds.append({
                "seed_id": f"{session['source_session_id']}:{index + 1}",
                "source_session_id": session["source_session_id"],
                "source_name": session["source_name"],
                "source_turn": index + 1,
                "family": turn.get("family") or session["family"],
                "context": context,
                "target_user": turn["user"],
            })
    return seeds


def build_queue(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    seeds = build_seeds(sessions)
    unique: dict[str, dict[str, Any]] = {}
    duplicate_counts = Counter()
    for session in sessions:
        key = normalized_flow_key(session["turns"])
        duplicate_counts[key] += 1
        if key not in unique:  # sessions arrive newest first
            unique[key] = session
    workstreams = {name: [] for name in ("harness", "model_sft", "backend", "replay_first")}
    replay_flows = []
    for number, (key, session) in enumerate(unique.items(), 1):
        bucket, reasons = classify(session["turns"])
        row = {
            "id": f"historical-{number:04d}",
            "family": session["family"],
            "case": case_name(session["source_name"]),
            "source_session_id": session["source_session_id"],
            "duplicate_runs": duplicate_counts[key],
            "reasons": reasons,
            "turns": [
                {
                    "user": turn["user"],
                    "assistant": turn["assistant"],
                    "tools": [event.get("tool") for event in (turn["metadata"].get("tool_events") or [])],
                }
                for turn in session["turns"]
            ],
        }
        workstreams[bucket].append(row)
        replay_flows.append({
            "id": row["id"],
            "family": row["family"],
            "purpose": f"Replay historical contract case {row['case']}",
            "turns": [{
                "user": turn["user"],
                "expect": "Honor the request and conversation context; use the correct tool only when needed and rely on successful tool evidence.",
            } for turn in session["turns"]],
        })
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_sessions": len(sessions),
        "source_user_turns": sum(len(session["turns"]) for session in sessions),
        "seed_count": len(seeds),
        "unique_flows": len(unique),
        "counts": {name: len(rows) for name, rows in workstreams.items()},
        "families": dict(sorted(Counter(row["family"] for row in unique.values()).items())),
        "seed_families": dict(sorted(Counter(row["family"] for row in seeds).items())),
        "workstreams": workstreams,
        "flows": replay_flows,
        "seeds": seeds,
    }


def render_summary(queue: dict[str, Any]) -> str:
    lines = [
        "# Historical Odysseus QA Queue", "",
        f"- Source sessions: {queue['source_sessions']}",
        f"- Source user turns / teacher seeds: {queue['seed_count']}",
        f"- Unique conversation flows: {queue['unique_flows']}",
        "- Historical labels are conservative; `replay_first` must be replayed before assigning ownership.",
        "", "## Workstreams", "",
    ]
    for name, count in queue["counts"].items():
        lines.append(f"- `{name}`: {count}")
    lines.extend(["", "## Families", ""])
    for family, count in queue["seed_families"].items():
        lines.append(f"- `{family}`: {count}")
    lines.extend([
        "", "## Workflow", "",
        "1. Cook one fresh conversation from every seed using the complete tool catalog.",
        "2. Replay safe cooked cases on the current 7011 Agent runtime.",
        "3. Judge, classify ownership, and patch recurring behavior classes.",
        "4. Retain duplicate source runs as stability evidence; account for quarantined cases explicitly.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--owner", default="sft_alex_creator")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    queue = build_queue(load_sessions(args.db, args.owner))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.summary.write_text(render_summary(queue), encoding="utf-8")
    print(json.dumps({key: queue[key] for key in ("source_sessions", "unique_flows", "counts", "families")}, indent=2))


if __name__ == "__main__":
    main()
