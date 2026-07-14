"""Shared world/quest flag helpers — ADR §H8.1 `wait_event` (engine-only writers).

Flags must only ever be set by deterministic engine code (combat, quest, movement),
never by the GM or archivist from prose. Callers that already hold an open write
connection MUST use the `_conn` variants to avoid a second connection blocking on
the same file mid-transaction; the `db_path` variants are for callers (wizard,
scripted world events) that don't already have one open.

Built-in flag vocabulary emitted automatically by the engine (usable as
`target_code` on a `wait_event` quest objective without any extra wiring):
  - `quest_complete:<quest_code>` — set when that quest's objectives all complete.
  - `quest_failed:<quest_code>` — set when that quest fails (giver_dead / player_choice /
    event_flag / time_expired — ADR §H8.3).
  - `npc_dead:<npc_code>` — set when that NPC is killed in combat.
  - `npc_betrayed:<npc_code>` — set when a hidden `npc_agenda` is revealed (ADR §B5c).
  - `location_discovered:<location_code>` — set the first time a grid cell is visited.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def set_flag_conn(conn: sqlite3.Connection, key: str, value: str = "1") -> None:
    """Set a flag using an already-open connection (same transaction — no lock risk)."""
    conn.execute(
        """
        INSERT INTO world_flags (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, _utc_now()),
    )


def get_flag_conn(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM world_flags WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_flag(db_path: str, key: str, value: str = "1") -> None:
    """Set a flag by opening a fresh connection — only for callers with no open transaction."""
    if not db_path or not os.path.isfile(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        set_flag_conn(conn, key, value)
        conn.commit()
    finally:
        conn.close()


def get_flag(db_path: str, key: str) -> str | None:
    if not db_path or not os.path.isfile(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return get_flag_conn(conn, key)
    finally:
        conn.close()
