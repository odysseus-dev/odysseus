#!/usr/bin/env python3
"""Audit current capability routing against recorded historical tool turns.

This is intentionally read-only: it never creates sessions or executes tools.
Recorded assistant tool events provide the expected families; the current turn
contract is evaluated with the original preceding conversation as history.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from src.turn_contract import FAMILY_TOOLS, canonical_tool, requested_capabilities


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(str(Path(__file__).resolve().parents[1] / "data" / "app.db"))
DEFAULT_ANCHOR = "a37dcb3b-6864-4266-a115-f9e87aafd0eb"


def tool_family(tool: str, command: object) -> set[str]:
    name = canonical_tool(tool)
    families = {family for family, tools in FAMILY_TOOLS.items() if name in tools}
    # ui_control is a rendering/action bridge. Its command identifies the
    # product family; do not label every such turn as the generic UI family.
    if name == "ui_control":
        text = str(command or "").lower()
        if "email" in text:
            return {"email"}
        if "calendar" in text or "event" in text:
            return {"calendar"}
        if "note" in text:
            return {"notes"}
        if "document" in text or "editor" in text:
            return {"documents"}
    return families


def metadata_tools(raw: str | None) -> set[str]:
    try:
        metadata = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return set()
    expected: set[str] = set()
    for event in metadata.get("tool_events") or []:
        expected.update(tool_family(event.get("tool", ""), event.get("command")))
    return expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--anchor", default=DEFAULT_ANCHOR)
    parser.add_argument("--owner", default="sft_alex_creator")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/historical-routing-audit.json")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    anchor = con.execute("SELECT created_at FROM sessions WHERE id = ?", (args.anchor,)).fetchone()
    if anchor is None:
        raise SystemExit(f"Anchor session not found: {args.anchor}")
    sessions = con.execute(
        "SELECT id, name, created_at FROM sessions WHERE owner = ? AND created_at >= ? "
        "ORDER BY created_at, id", (args.owner, anchor[0])
    ).fetchall()

    rows: list[dict] = []
    seen: set[tuple] = set()
    for session in sessions:
        messages = con.execute(
            "SELECT id, role, content, metadata, timestamp FROM chat_messages "
            "WHERE session_id = ? ORDER BY timestamp, id", (session["id"],)
        ).fetchall()
        history: list[dict[str, str]] = []
        for index, message in enumerate(messages):
            role, content = message["role"], message["content"]
            if role != "user":
                history.append({"role": role, "content": content})
                continue
            following = next((m for m in messages[index + 1:] if m["role"] == "assistant"), None)
            expected = metadata_tools(following["metadata"] if following else None)
            if not expected:
                history.append({"role": role, "content": content})
                continue
            key = (tuple((h["role"], h["content"].strip().lower()) for h in history), content.strip().lower(), tuple(sorted(expected)))
            if key in seen:
                history.append({"role": role, "content": content})
                continue
            seen.add(key)
            actual = set(requested_capabilities(content, history))
            missing = expected - actual
            rows.append({
                "session_id": session["id"], "session_name": session["name"],
                "message_id": message["id"], "prompt": content,
                "expected": sorted(expected), "actual": sorted(actual),
                "missing": sorted(missing), "passed": not missing,
            })
            history.append({"role": role, "content": content})

    failures = [row for row in rows if not row["passed"]]
    report = {
        "source_db": str(args.db), "anchor": args.anchor, "owner": args.owner,
        "sessions_scanned": len(sessions), "labeled_unique_turns": len(rows),
        "passed": len(rows) - len(failures), "failed": len(failures),
        "accuracy": round((len(rows) - len(failures)) / len(rows), 6) if rows else None,
        "missing_family_counts": dict(sorted(Counter(f for row in failures for f in row["missing"]).items())),
        "failures": failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("sessions_scanned", "labeled_unique_turns", "passed", "failed", "accuracy", "missing_family_counts")}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
