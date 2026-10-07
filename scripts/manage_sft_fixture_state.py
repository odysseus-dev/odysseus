#!/usr/bin/env python3
"""Snapshot or restore durable state for one Odysseus SFT fixture owner.

Sessions and chat messages are intentionally excluded so replay evidence keeps
working. Only owner-scoped tool data and its dependent rows are managed.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any


DIRECT_TABLES = (
    "notes",
    "memories",
    "scheduled_tasks",
    "documents",
    "calendars",
    "editor_drafts",
    "notification_logs",
    "caldav_deleted_events",
)
CHILD_TABLES = {
    "document_versions": ("documents", "document_id", "id"),
    "task_runs": ("scheduled_tasks", "task_id", "id"),
    "calendar_events": ("calendars", "calendar_id", "id"),
}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.fixture-state.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _skill_owner(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    match = re.search(r'^owner:\s*["\']?([^"\'\n#]+)', text, re.M)
    return match.group(1).strip() if match else ""


def _snapshot_external(data_dir: Path, owner: str) -> dict[str, Any]:
    prefs = _read_json(data_dir / "user_prefs.json", {"_users": {}})
    blocked = _read_json(data_dir / "email_blocked_senders.json", {"owners": {}})
    email_payload = _read_json(data_dir / "fixture_email_messages.json", {"messages": []})
    email_rows = email_payload.get("messages", []) if isinstance(email_payload, dict) else email_payload
    skills_root = data_dir / "skills"
    skill_files: list[dict[str, str]] = []
    skill_dirs: list[str] = []
    if skills_root.exists():
        for skill_md in skills_root.rglob("SKILL.md"):
            if _skill_owner(skill_md) != owner:
                continue
            directory = skill_md.parent
            skill_dirs.append(str(directory.relative_to(skills_root)))
            for path in directory.rglob("*"):
                if path.is_file():
                    skill_files.append({
                        "path": str(path.relative_to(skills_root)),
                        "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                    })
    usage = _read_json(skills_root / "_usage.json", {})
    return {
        "prefs_present": owner in ((prefs.get("_users") or {}) if isinstance(prefs, dict) else {}),
        "prefs": ((prefs.get("_users") or {}).get(owner) if isinstance(prefs, dict) else None),
        "blocked_present": owner in ((blocked.get("owners") or {}) if isinstance(blocked, dict) else {}),
        "blocked_senders": ((blocked.get("owners") or {}).get(owner) if isinstance(blocked, dict) else None),
        "email_rows": [
            row for row in (email_rows if isinstance(email_rows, list) else [])
            if isinstance(row, dict) and str(row.get("owner") or "") == owner
        ],
        "skill_dirs": sorted(set(skill_dirs)),
        "skill_files": skill_files,
        "skill_usage": {
            key: value for key, value in (usage.items() if isinstance(usage, dict) else [])
            if str(key).startswith(f"{owner}::")
        },
    }


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone() is not None


def _columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in db.execute(f'PRAGMA table_info("{table}")')]


def _rows(db: sqlite3.Connection, table: str, where: str, values: tuple[Any, ...]) -> list[dict[str, Any]]:
    db.row_factory = sqlite3.Row
    return [dict(row) for row in db.execute(f'SELECT * FROM "{table}" WHERE {where}', values)]


def snapshot_owner(db_path: Path, owner: str, data_dir: Path | None = None) -> dict[str, Any]:
    db = sqlite3.connect(db_path)
    try:
        tables: dict[str, list[dict[str, Any]]] = {}
        for table in DIRECT_TABLES:
            if _table_exists(db, table) and "owner" in _columns(db, table):
                tables[table] = _rows(db, table, '"owner"=?', (owner,))
        for table, (parent, foreign_key, parent_key) in CHILD_TABLES.items():
            if not _table_exists(db, table):
                continue
            parent_ids = [row[parent_key] for row in tables.get(parent, [])]
            if not parent_ids:
                tables[table] = []
                continue
            placeholders = ",".join("?" for _ in parent_ids)
            tables[table] = _rows(
                db, table, f'"{foreign_key}" IN ({placeholders})', tuple(parent_ids),
            )
        return {
            "format": "odysseus-owner-fixture-v1",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_db": str(db_path),
            "owner": owner,
            "tables": tables,
            "counts": {table: len(rows) for table, rows in tables.items()},
            "external": _snapshot_external(data_dir or db_path.parent, owner),
        }
    finally:
        db.close()


def _delete_owner_rows(db: sqlite3.Connection, owner: str) -> None:
    for table, (parent, foreign_key, parent_key) in CHILD_TABLES.items():
        if not (_table_exists(db, table) and _table_exists(db, parent)):
            continue
        db.execute(
            f'DELETE FROM "{table}" WHERE "{foreign_key}" IN '
            f'(SELECT "{parent_key}" FROM "{parent}" WHERE "owner"=?)',
            (owner,),
        )
    for table in DIRECT_TABLES:
        if _table_exists(db, table) and "owner" in _columns(db, table):
            db.execute(f'DELETE FROM "{table}" WHERE "owner"=?', (owner,))


def _restore_external(data_dir: Path, external: dict[str, Any], owner: str) -> None:
    prefs_path = data_dir / "user_prefs.json"
    prefs = _read_json(prefs_path, {"_users": {}})
    users = prefs.setdefault("_users", {})
    if external.get("prefs_present"):
        users[owner] = external.get("prefs")
    else:
        users.pop(owner, None)
    _atomic_json(prefs_path, prefs)

    blocked_path = data_dir / "email_blocked_senders.json"
    blocked = _read_json(blocked_path, {"owners": {}})
    blocked_owners = blocked.setdefault("owners", {})
    if external.get("blocked_present"):
        blocked_owners[owner] = external.get("blocked_senders")
    else:
        blocked_owners.pop(owner, None)
    _atomic_json(blocked_path, blocked)

    email_path = data_dir / "fixture_email_messages.json"
    email_payload = _read_json(email_path, {"messages": []})
    email_rows = email_payload.get("messages", []) if isinstance(email_payload, dict) else email_payload
    retained = [
        row for row in (email_rows if isinstance(email_rows, list) else [])
        if not (isinstance(row, dict) and str(row.get("owner") or "") == owner)
    ]
    restored_rows = retained + list(external.get("email_rows") or [])
    if isinstance(email_payload, dict):
        email_payload["messages"] = restored_rows
    else:
        email_payload = restored_rows
    _atomic_json(email_path, email_payload)

    skills_root = data_dir / "skills"
    if skills_root.exists():
        for skill_md in list(skills_root.rglob("SKILL.md")):
            if _skill_owner(skill_md) == owner:
                shutil.rmtree(skill_md.parent, ignore_errors=True)
    for entry in external.get("skill_files") or []:
        relative = Path(str(entry.get("path") or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe skill path in fixture snapshot")
        destination = skills_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(base64.b64decode(entry.get("base64") or ""))
    usage_path = skills_root / "_usage.json"
    usage = _read_json(usage_path, {})
    usage = usage if isinstance(usage, dict) else {}
    usage = {key: value for key, value in usage.items() if not str(key).startswith(f"{owner}::")}
    usage.update(external.get("skill_usage") or {})
    _atomic_json(usage_path, usage)


def restore_owner(target_db: Path, snapshot: dict[str, Any], owner: str,
                  data_dir: Path | None = None) -> None:
    if snapshot.get("format") != "odysseus-owner-fixture-v1":
        raise ValueError("unsupported fixture snapshot format")
    if str(snapshot.get("owner") or "") != owner:
        raise ValueError("snapshot owner does not match requested owner")
    tables = snapshot.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("snapshot has no tables")

    db = sqlite3.connect(target_db, timeout=60)
    try:
        db.execute("BEGIN IMMEDIATE")
        _delete_owner_rows(db, owner)
        insertion_order = (*DIRECT_TABLES, *CHILD_TABLES)
        for table in insertion_order:
            rows = tables.get(table) or []
            if not rows or not _table_exists(db, table):
                continue
            target_columns = set(_columns(db, table))
            columns = [column for column in rows[0] if column in target_columns]
            quoted = ",".join(f'"{column}"' for column in columns)
            placeholders = ",".join("?" for _ in columns)
            db.executemany(
                f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
                [[row.get(column) for column in columns] for row in rows],
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    external = snapshot.get("external")
    if isinstance(external, dict):
        _restore_external(data_dir or target_db.parent, external, owner)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Live target app.db")
    parser.add_argument("--data-dir", type=Path, help="External fixture state directory; defaults to DB parent")
    parser.add_argument("--owner", default="sft_alex_creator")
    parser.add_argument("--snapshot-out", type=Path)
    parser.add_argument("--restore-json", type=Path)
    parser.add_argument("--restore-from-db", type=Path)
    args = parser.parse_args()
    operations = sum(bool(value) for value in (
        args.snapshot_out, args.restore_json, args.restore_from_db,
    ))
    if operations != 1:
        parser.error("choose exactly one of --snapshot-out, --restore-json, or --restore-from-db")

    if args.snapshot_out:
        payload = snapshot_owner(args.db, args.owner, args.data_dir)
        args.snapshot_out.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"snapshot": str(args.snapshot_out), "counts": payload["counts"]}, indent=2))
        return

    if args.restore_json:
        payload = json.loads(args.restore_json.read_text(encoding="utf-8"))
    else:
        payload = snapshot_owner(args.restore_from_db, args.owner, args.data_dir)
    restore_owner(args.db, payload, args.owner, args.data_dir)
    print(json.dumps({"restored_owner": args.owner, "counts": payload["counts"]}, indent=2))


if __name__ == "__main__":
    main()
