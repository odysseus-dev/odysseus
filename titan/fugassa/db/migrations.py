"""Apply incremental DB migrations for existing saves."""

from __future__ import annotations

import os
import sqlite3

SCHEMA_VERSION = 13


def _migrations_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "migrations")


def get_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM save_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row:
            return int(row[0])
    except sqlite3.OperationalError:
        pass
    return 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        """
        INSERT INTO save_meta (key, value, updated_at)
        VALUES ('schema_version', ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (str(version),),
    )


def _ensure_npc_staff_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(npcs)").fetchall()}
    if "assigned_property_id" not in cols:
        conn.execute(
            "ALTER TABLE npcs ADD COLUMN assigned_property_id INTEGER "
            "REFERENCES property_holdings(id) ON DELETE SET NULL"
        )
    if "assigned_role" not in cols:
        conn.execute("ALTER TABLE npcs ADD COLUMN assigned_role TEXT")


def apply_migrations(conn: sqlite3.Connection, *, target_version: int = SCHEMA_VERSION) -> int:
    """Run pending migrations; return new schema version."""
    current = get_schema_version(conn)
    if current >= target_version:
        return current

    mig_dir = _migrations_dir()
    if os.path.isdir(mig_dir):
        files = sorted(f for f in os.listdir(mig_dir) if f.endswith(".sql"))
        for name in files:
            # migration files named like 002_description.sql — version from prefix
            prefix = name.split("_", 1)[0]
            try:
                mig_version = int(prefix)
            except ValueError:
                continue
            if mig_version <= current or mig_version > target_version:
                continue
            path = os.path.join(mig_dir, name)
            with open(path, encoding="utf-8") as f:
                conn.executescript(f.read())
            if mig_version == 13:
                _ensure_npc_staff_columns(conn)
            current = mig_version
            _set_schema_version(conn, current)

    if current < target_version:
        _set_schema_version(conn, target_version)
        current = target_version
    return current
