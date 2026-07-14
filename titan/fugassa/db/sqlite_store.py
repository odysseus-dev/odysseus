"""Per-save SQLite helpers — M1 schema, migrations, campaign meta."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa.db import migrations
from titan.fugassa.db.migrations import SCHEMA_VERSION

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_schema() -> str:
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return f.read()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_db(db_path: str) -> int:
    """Apply schema + pending migrations; return schema version."""
    if not os.path.isfile(db_path):
        return 0
    conn = connect(db_path)
    try:
        conn.executescript(_read_schema())
        version = migrations.apply_migrations(conn, target_version=SCHEMA_VERSION)
        conn.commit()
        return version
    finally:
        conn.close()


def init_game_db(db_path: str, campaign_name: str, *, theme: str = "fantasy") -> None:
    """Create game.db with M1 schema and default campaign_settings row."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(_read_schema())
        migrations.apply_migrations(conn, target_version=SCHEMA_VERSION)
        now = _utc_now()
        conn.execute(
            """
            INSERT OR REPLACE INTO campaign_settings (
                id, campaign_name, theme, save_version, turn_number, created_at, updated_at
            ) VALUES (1, ?, ?, ?, 0, ?, ?)
            """,
            (campaign_name, theme, SCHEMA_VERSION, now, now),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO save_meta (key, value, updated_at)
            VALUES ('schema_version', ?, ?)
            """,
            (str(SCHEMA_VERSION), now),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO save_meta (key, value, updated_at)
            VALUES ('pipeline_model', 'v2', ?)
            """,
            (now,),
        )
        conn.commit()
    finally:
        conn.close()


def read_campaign_meta(db_path: str) -> dict[str, Any] | None:
    if not os.path.isfile(db_path):
        return None
    ensure_db(db_path)
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM campaign_settings WHERE id = 1").fetchone()
        if not row:
            return None
        meta = dict(row)
        meta["schema_version"] = migrations.get_schema_version(conn)
        return meta
    finally:
        conn.close()


def db_exists(db_path: str) -> bool:
    return os.path.isfile(db_path)


def update_campaign_name(db_path: str, campaign_name: str) -> None:
    if not os.path.isfile(db_path):
        return
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE campaign_settings SET campaign_name = ?, updated_at = ? WHERE id = 1",
            (campaign_name, _utc_now()),
        )
        conn.commit()
    finally:
        conn.close()


def update_campaign_from_wizard(db_path: str, *, draft: dict[str, Any], theme: str) -> None:
    if not os.path.isfile(db_path):
        return
    ensure_db(db_path)
    world_summary = str(draft.get("world_information") or "").strip() or None
    campaign_length = str(draft.get("campaign_length") or "medium")
    conn = connect(db_path)
    try:
        conn.execute(
            """
            UPDATE campaign_settings
            SET theme = ?, campaign_length = ?, world_summary = ?, updated_at = ?
            WHERE id = 1
            """,
            (theme, campaign_length, world_summary, _utc_now()),
        )
        conn.commit()
    finally:
        conn.close()


def update_turn_number(db_path: str, turn_number: int) -> None:
    if not os.path.isfile(db_path):
        return
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE campaign_settings SET turn_number = ?, updated_at = ? WHERE id = 1",
            (int(turn_number), _utc_now()),
        )
        conn.commit()
    finally:
        conn.close()
