"""Deterministic email backend used only by the disposable fixture harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR


def _messages() -> list[dict[str, Any]]:
    path = Path(DATA_DIR) / "fixture_email_messages.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("messages", []) if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def _write_messages(messages: list[dict[str, Any]]) -> bool:
    path = Path(DATA_DIR) / "fixture_email_messages.json"
    try:
        path.write_text(json.dumps({"messages": messages}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _folder_key(folder: str | None) -> str:
    value = str(folder or "INBOX").strip().lower()
    if value in {"", "inbox"}:
        return "inbox"
    if value in {"archive", "archived", "[gmail]/all mail", "all mail"}:
        return "archive"
    if value == "all":
        return "all"
    if value in {"trash", "deleted", "bin"}:
        return "trash"
    return value


def _folder_matches(row_folder: str | None, requested: str | None) -> bool:
    req = _folder_key(requested)
    actual = _folder_key(row_folder or "INBOX")
    if req == "all":
        return actual != "trash"
    return actual == req


def _uid(index: int) -> str:
    return str(index + 1)


def _row(index: int, message: dict[str, Any]) -> dict[str, Any]:
    raw_attachments = message.get("attachments") if isinstance(message.get("attachments"), list) else []
    attachments = []
    for att_index, att in enumerate(raw_attachments):
        if not isinstance(att, dict):
            continue
        content = str(att.get("content") or "")
        attachments.append({
            "index": int(att.get("index", att_index) or att_index),
            "filename": str(att.get("filename") or f"attachment-{att_index}.txt"),
            "content_type": str(att.get("content_type") or "application/octet-stream"),
            "size": len(content.encode("utf-8")),
        })
    return {
        "uid": str(message.get("uid") or _uid(index)),
        "account": str(message.get("account") or "fixture"),
        "account_email": str(message.get("account_email") or message.get("to") or ""),
        "account_id": str(message.get("account_id") or "fixture"),
        "subject": str(message.get("subject") or ""),
        "from": str(message.get("from") or ""),
        "date": str(message.get("date") or ""),
        "owner": str(message.get("owner") or ""),
        "folder": str(message.get("folder") or "INBOX"),
        "attachments": attachments,
        "has_attachments": bool(attachments),
    }


def _matches(message: dict[str, Any], query: str) -> bool:
    query = query.strip().casefold()
    if not query:
        return True
    haystack = " ".join(
        str(message.get(key) or "")
        for key in ("subject", "from", "body", "date")
    )
    attachments = message.get("attachments") if isinstance(message.get("attachments"), list) else []
    haystack += " " + " ".join(
        f"{att.get('filename') or ''} {att.get('content') or ''}"
        for att in attachments
        if isinstance(att, dict)
    )
    haystack = haystack.casefold()
    return query in haystack


def execute_fixture_email(tool: str, args: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    """Return MCP-shaped deterministic results for fixture email calls."""
    messages = _messages()
    owner = str(owner or "").strip()
    if owner:
        messages = [m for m in messages if not m.get("owner") or m.get("owner") == owner]

    bare = tool.removeprefix("mcp__email__")
    if bare == "list_email_accounts":
        return {
            "accounts": [{"id": "fixture", "name": "Fixture mailbox", "default": True}],
            "output": "Fixture mailbox (default)",
            "exit_code": 0,
        }

    if bare in {"list_emails", "search_emails"}:
        query = str(args.get("query") or "") if bare == "search_emails" else ""
        folder = str(args.get("folder") or "INBOX")
        account = str(args.get("account") or "").strip().casefold()
        rows = [
            _row(i, m) for i, m in enumerate(messages)
            if _matches(m, query) and _folder_matches(m.get("folder"), folder)
            and (
                not account
                or account in {
                    str(m.get("account") or "").strip().casefold(),
                    str(m.get("account_email") or "").strip().casefold(),
                    str(m.get("account_id") or "").strip().casefold(),
                }
            )
        ]
        limit = args.get("max_results", args.get("limit", 20))
        try:
            rows = rows[: max(1, int(limit))]
        except (TypeError, ValueError):
            rows = rows[:20]
        if not rows:
            return {"output": "No emails found.", "emails": [], "exit_code": 0}
        output_lines = [f"Found {len(rows)} email(s):", ""]
        for index, r in enumerate(rows, start=1):
            source = next(
                (m for i, m in enumerate(messages) if _uid(i) == r["uid"]),
                {},
            )
            summary = str(source.get("summary") or source.get("body") or "").strip()
            output_lines.extend([
                f"{index}. **{r['subject']}**",
                f"   From: {r['from']}",
                f"   Date: {r['date']}",
                f"   UID: {r['uid']}",
                f"   Account: {r['account']}",
            ])
            if summary:
                output_lines.append(f"   Summary: {summary[:240]}")
            output_lines.append("")
        output = "\n".join(output_lines).rstrip()
        return {"output": output, "emails": rows, "exit_code": 0}

    if bare == "read_email":
        uid = str(args.get("uid") or "")
        folder = str(args.get("folder") or "INBOX")
        try:
            index = int(uid) - 1
        except (TypeError, ValueError):
            index = -1
        if index < 0 or index >= len(messages) or not _folder_matches(messages[index].get("folder"), folder):
            return {"error": f"Email UID {uid} not found.", "exit_code": 1}
        message = messages[index]
        row = _row(index, message)
        output = (
            f"UID: {row['uid']}\nSubject: {row['subject']}\nFrom: {row['from']}\n"
            f"Date: {row['date']}\n\n{message.get('body') or ''}"
        )
        if row.get("attachments"):
            output += "\n\nAttachments:\n" + "\n".join(
                f"- [{att['index']}] {att['filename']} ({att['content_type']}, {att['size']} bytes)"
                for att in row["attachments"]
            )
        return {"output": output, "email": {**row, "body": message.get("body") or ""}, "exit_code": 0}

    if bare in {"archive_email", "delete_email", "mark_email_read"}:
        uid = str(args.get("uid") or "")
        folder = str(args.get("folder") or "INBOX")
        all_messages = _messages()
        owner_value = str(owner or "").strip()
        visible_index = -1
        for original in all_messages:
            if owner_value and original.get("owner") and original.get("owner") != owner_value:
                continue
            visible_index += 1
            if str(original.get("uid") or _uid(visible_index)) != uid:
                continue
            if not _folder_matches(original.get("folder"), folder):
                continue
            if bare == "archive_email":
                original["folder"] = "Archive"
                action = "Archived"
            elif bare == "delete_email":
                original["folder"] = "Trash"
                action = "Deleted"
            else:
                original["read"] = bool(args.get("read", True))
                action = "Marked"
            if not _write_messages(all_messages):
                return {"error": "Failed to update fixture mailbox.", "exit_code": 1}
            suffix = f" UID {uid}" if bare != "mark_email_read" else f" UID {uid} as {'read' if original.get('read') else 'unread'}"
            return {"output": action + suffix, "exit_code": 0}
        return {"error": f"Email UID {uid} not found.", "exit_code": 1}

    return {"error": f"Fixture email tool '{bare}' is not implemented.", "exit_code": 1}
