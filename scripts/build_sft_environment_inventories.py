#!/usr/bin/env python3
"""Snapshot non-sensitive fixture inventories for SFT expansion owners."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import CalendarCal, CalendarEvent, Document, Memory, Note, ScheduledTask, Session, SessionLocal, UserTool  # noqa: E402
from src.constants import DATA_DIR  # noqa: E402
from scripts.sft_email_overseer import PROFILES  # noqa: E402
OWNERS = ["sft_maya_ops", "sft_jules_research", "sft_nora_design", "sft_omar_finance"]


def clip(value: Any, limit: int = 180) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def email_inventory() -> dict[str, list[dict[str, Any]]]:
    payload = json.loads((Path(DATA_DIR) / "fixture_email_messages.json").read_text(encoding="utf-8"))
    rows = payload.get("messages") if isinstance(payload, dict) else payload
    out = {owner: [] for owner in OWNERS}
    for row in rows or []:
        owner = str(row.get("owner") or "")
        if owner not in out:
            continue
        out[owner].append({
            "uid": str(row.get("uid") or ""),
            "account": row.get("account") or row.get("account_id"),
            "from": clip(row.get("from") or row.get("sender")),
            "subject": clip(row.get("subject")),
            "date": row.get("date"),
            "attachments": [att.get("filename") for att in (row.get("attachments") or []) if isinstance(att, dict)],
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sample-limit", type=int, default=30)
    args = parser.parse_args()
    mail = email_inventory()
    db = SessionLocal()
    try:
        inventories = []
        for owner in OWNERS:
            calendars = db.query(CalendarCal).filter(CalendarCal.owner == owner).all()
            calendar_ids = [cal.id for cal in calendars]
            events = db.query(CalendarEvent).filter(CalendarEvent.calendar_id.in_(calendar_ids)).order_by(CalendarEvent.dtstart).all() if calendar_ids else []
            notes = db.query(Note).filter(Note.owner == owner, Note.archived.is_(False)).order_by(Note.updated_at.desc()).all()
            memories = db.query(Memory).filter(Memory.owner == owner).order_by(Memory.timestamp.desc()).all()
            documents = db.query(Document).filter(Document.owner == owner, Document.archived.is_(False)).order_by(Document.updated_at.desc()).all()
            tasks = db.query(ScheduledTask).filter(ScheduledTask.owner == owner).order_by(ScheduledTask.updated_at.desc()).all()
            sessions = db.query(Session).filter(Session.owner == owner, Session.archived.is_(False)).order_by(Session.updated_at.desc()).all()
            disabled_tools = [row.name for row in db.query(UserTool).filter(UserTool.owner == owner, UserTool.is_active.is_(False)).all()]
            emails = mail.get(owner, [])
            inventories.append({
                "owner": owner,
                "profile": PROFILES[owner],
                "counts": {
                    "emails": len(emails), "notes": len(notes), "memories": len(memories),
                    "documents": len(documents), "tasks": len(tasks), "calendars": len(calendars),
                    "calendar_events": len(events), "sessions": len(sessions),
                },
                "email_accounts": dict(Counter(str(row.get("account") or "unknown") for row in emails)),
                "emails": emails[: args.sample_limit],
                "notes": [{"id": row.id, "title": clip(row.title), "content": clip(row.content), "type": row.note_type, "label": row.label} for row in notes[: args.sample_limit]],
                "memories": [{"id": row.id, "text": clip(row.text), "category": row.category} for row in memories[: args.sample_limit]],
                "documents": [{"id": row.id, "title": clip(row.title), "language": row.language, "content": clip(row.current_content)} for row in documents[: args.sample_limit]],
                "tasks": [{"id": row.id, "name": clip(row.name), "status": row.status, "schedule": row.schedule} for row in tasks[: args.sample_limit]],
                "calendars": [{"id": row.id, "name": row.name, "source": row.source} for row in calendars],
                "events": [{"uid": row.uid, "summary": clip(row.summary), "start": row.dtstart.isoformat(), "all_day": row.all_day} for row in events[: args.sample_limit]],
                "sessions": [{"id": row.id, "name": clip(row.name), "mode": row.mode} for row in sessions[: args.sample_limit]],
                "disabled_tools": disabled_tools,
            })
    finally:
        db.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"environments": inventories}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({row["owner"]: row["counts"] for row in inventories}, indent=2))


if __name__ == "__main__":
    main()
