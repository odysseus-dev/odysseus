"""Read-only storage/bloat diagnostics (issue #4889, first slice).

The ``/api/db/stats`` endpoint reports only the total database file size. When
``app.db`` grows after a multimodal chat, admins need to know *where* the space
went before changing anything. This module answers that read-only question:

* database size and per-table sizes (``dbstat`` when SQLite provides it);
* the largest ``chat_messages.content`` rows;
* how many persisted rows still carry inline base64 media;
* ``chat_messages_fts`` size / row count;
* upload directory file count and byte total;
* uploads on disk that no durable row still references ("suspected orphans").

Design constraints (mirrors ``src/service_health.py``):

* **Never writes.** The SQLite connection is opened with ``mode=ro`` and the
  upload scan only ``stat``s files. Nothing is deleted, rewritten, or vacuumed
  — the issue explicitly asks for a report-only first slice.
* **Never raises.** Every section degrades to a recorded ``error`` string so a
  partially broken install still returns the sections that do work.
* **Injectable inputs.** ``db_path`` / ``upload_dir`` / ``referenced_ids`` are
  parameters so tests drive real behavior against a temp database instead of
  inspecting source text.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Iterable, Optional

from src.constants import UPLOAD_DIR
from src.upload_handler import extract_upload_ids, is_valid_upload_id

# Tables whose persisted text can hold an upload reference. Kept in step with
# the conservative scan used by the reference-safe upload cleanup so a reported
# "orphan" is never deleted-but-referenced.
_REFERENCE_SCAN_QUERIES = (
    # NOTE: the ORM maps ChatMessage.meta_data to the physical column "metadata"
    # (see core/database.py), so this must spell it out — model attribute names
    # do NOT work in raw SQL.
    ("chat_messages", 'SELECT content, "metadata" FROM chat_messages'),
    ("documents", "SELECT current_content FROM documents"),
    ("document_versions", "SELECT content FROM document_versions"),
    ("gallery_images", "SELECT filename FROM gallery_images"),
    ("notes", "SELECT image_url, color, content, items FROM notes"),
    ("calendars", "SELECT color FROM calendars"),
    ("calendar_events", "SELECT color, description, location FROM calendar_events"),
)


def _sqlite_path(db_path: Optional[str]) -> str:
    """Resolve the on-disk SQLite path, honouring an explicit override."""
    if db_path:
        return db_path
    from core.database import DATABASE_URL

    raw = DATABASE_URL.replace("sqlite:///", "", 1)
    return raw if os.path.isabs(raw) else os.path.abspath(raw)


def _connect_readonly(db_path: str) -> sqlite3.Connection:
    """Open *db_path* read-only so the diagnostic can never mutate the DB."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _table_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row count per user table (``sqlite_`` internals excluded).

    The table name is read from ``sqlite_master`` (the database's own trusted
    schema metadata) and double-quoted as a SQLite identifier — identifiers
    cannot be parameterised, and this connection is opened ``mode=ro``.
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_data' "
        "AND name NOT LIKE '%_idx' AND name NOT LIKE '%_content' "
        "AND name NOT LIKE '%_docsize' AND name NOT LIKE '%_config'"
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        name = row["name"]
        try:
            counts[name] = conn.execute(
                f'SELECT COUNT(*) AS n FROM "{name}"'
            ).fetchone()["n"]
        except sqlite3.Error:
            continue
    return counts


def _table_sizes(conn: sqlite3.Connection) -> dict[str, int]:
    """Per-table byte sizes via ``dbstat``; empty when the build lacks it.

    ``dbstat`` is a compile-time option (SQLITE_ENABLE_DBSTAT_VTAB) and is not
    present in every SQLite build, so its absence is reported as an empty map
    rather than an error — callers fall back to row counts.
    """
    try:
        rows = conn.execute(
            "SELECT name, SUM(pgsize) AS bytes FROM dbstat "
            "WHERE name NOT LIKE 'sqlite_%' GROUP BY name ORDER BY bytes DESC"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {row["name"]: int(row["bytes"] or 0) for row in rows}


def _largest_chat_messages(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """The largest persisted ``chat_messages.content`` rows, byte length first."""
    try:
        rows = conn.execute(
            "SELECT id, session_id, role, LENGTH(content) AS chars, "
            "CASE WHEN content LIKE '%base64,%' THEN 1 ELSE 0 END AS inline_media "
            "FROM chat_messages ORDER BY LENGTH(content) DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]


def _inline_media_row_count(conn: sqlite3.Connection) -> int:
    """Rows whose persisted content still contains an inline base64 payload."""
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM chat_messages "
                "WHERE content LIKE '%base64,%'"
            ).fetchone()["n"]
        )
    except sqlite3.Error:
        return 0


def _fts_summary(conn: sqlite3.Connection, sizes: dict[str, int]) -> dict:
    """``chat_messages_fts`` presence, row count, and size when available."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' "
            "AND name='chat_messages_fts'"
        ).fetchone()
        present = bool(row and row["n"])
        if not present:
            return {"present": False, "rows": 0, "size_bytes": 0}
        rows = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM chat_messages_fts"
            ).fetchone()["n"]
        )
        return {
            "present": True,
            "rows": rows,
            "size_bytes": int(sizes.get("chat_messages_fts", 0) or 0),
        }
    except sqlite3.Error as exc:
        return {"present": False, "rows": 0, "size_bytes": 0, "error": str(exc)}


def _scan_referenced_upload_ids(conn: sqlite3.Connection) -> tuple:
    """Canonical upload IDs still referenced by durable rows (read-only).

    Mirrors the reference-safe cleanup scan conservatively. Returns the ID set
    plus a list of tables whose scan failed: a failed table means the orphan
    list may *over*-report, so callers surface ``reference_scan_complete=False``
    instead of presenting an incomplete scan as authoritative.
    """
    referenced: set[str] = set()
    errors: list = []
    for label, query in _REFERENCE_SCAN_QUERIES:
        try:
            cursor = conn.execute(query)
        except sqlite3.Error as exc:
            errors.append(f"{label}: {exc}")
            continue
        for row in cursor:
            for value in tuple(row):
                if isinstance(value, (str, bytes, bytearray)):
                    referenced.update(extract_upload_ids(value))
                    continue
                if value is not None:
                    referenced.update(extract_upload_ids(str(value)))
    return referenced, errors


def _iter_upload_files(upload_dir: str) -> Iterable[tuple[str, int]]:
    """Yield ``(basename, size)`` for every regular file under *upload_dir*."""
    for root, _dirs, files in os.walk(upload_dir, followlinks=False):
        for name in files:
            path = os.path.join(root, name)
            try:
                if os.path.isfile(path):
                    yield name, os.path.getsize(path)
            except OSError:
                continue


def _uploads_summary(upload_dir: Optional[str]) -> dict:
    """File count / byte total for the upload directory."""
    root = upload_dir or UPLOAD_DIR
    if not os.path.isdir(root):
        return {"dir": root, "files": 0, "bytes": 0, "error": "upload dir not found"}
    files = 0
    total = 0
    for _name, size in _iter_upload_files(root):
        files += 1
        total += size
    return {"dir": root, "files": files, "bytes": total}


def _suspected_orphans(
    upload_dir: str,
    referenced: set[str],
    sample: int,
) -> dict:
    """Upload files whose canonical ID no durable row still references."""
    if not os.path.isdir(upload_dir):
        return {"count": 0, "bytes": 0, "sample_ids": []}
    orphans: list[str] = []
    count = 0
    total = 0
    for name, size in _iter_upload_files(upload_dir):
        # uploads.json is the index itself, not an upload payload.
        if name == "uploads.json" or not is_valid_upload_id(name):
            continue
        if name in referenced:
            continue
        count += 1
        total += size
        if len(orphans) < sample:
            orphans.append(name)
    return {"count": count, "bytes": total, "sample_ids": orphans}
def collect_storage_report(
    *,
    db_path: Optional[str] = None,
    upload_dir: Optional[str] = None,
    referenced_ids: Optional[set] = None,
    largest: int = 10,
    orphan_sample: int = 10,
) -> dict:
    """Return a read-only storage/bloat report (issue #4889 first slice).

    Args:
        db_path: SQLite file to inspect. Defaults to the configured app DB.
        upload_dir: Upload root to measure. Defaults to ``src.constants.UPLOAD_DIR``.
        referenced_ids: Pre-computed referenced upload IDs. When omitted the
            scan is performed here, so the function is self-contained.
        largest: How many large ``chat_messages.content`` rows to return.
        orphan_sample: How many suspected-orphan IDs to include.

    Returns:
        A plain dict suitable for JSON serialisation. Every section carries its
        own ``error`` key instead of raising, so a partial failure still yields
        the sections that succeeded. This function performs no writes.
    """
    resolved_db = _sqlite_path(db_path)
    resolved_uploads = upload_dir or UPLOAD_DIR
    report: dict = {
        "read_only": True,
        "database": {"path": resolved_db},
        "tables": {"sizes": {}, "rows": {}, "size_via": "row_counts"},
        "chat_messages": {"largest": [], "inline_media_rows": 0},
        "fts": {"present": False, "rows": 0, "size_bytes": 0},
        "uploads": {},
        "suspected_orphans": {
            "count": 0,
            "bytes": 0,
            "sample_ids": [],
            "reference_scan_complete": True,
            "scan_errors": [],
        },
    }

    if not os.path.exists(resolved_db):
        report["database"]["error"] = "database file not found"
        report["uploads"] = _uploads_summary(resolved_uploads)
        return report

    try:
        report["database"]["size_bytes"] = os.path.getsize(resolved_db)
        report["database"]["size_mb"] = round(
            report["database"]["size_bytes"] / (1024 * 1024), 2
        )
    except OSError as exc:
        report["database"]["error"] = str(exc)

    conn = None
    scan_errors: list = []
    try:
        conn = _connect_readonly(resolved_db)
        sizes = _table_sizes(conn)
        if sizes:
            report["tables"]["sizes"] = sizes
            report["tables"]["size_via"] = "dbstat"
        report["tables"]["rows"] = _table_row_counts(conn)
        report["chat_messages"]["largest"] = _largest_chat_messages(conn, largest)
        report["chat_messages"]["inline_media_rows"] = _inline_media_row_count(conn)
        report["fts"] = _fts_summary(conn, sizes)
        if referenced_ids is None:
            referenced_ids, scan_errors = _scan_referenced_upload_ids(conn)
    except sqlite3.Error as exc:
        report["database"]["error"] = str(exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    report["uploads"] = _uploads_summary(resolved_uploads)
    report["suspected_orphans"] = _suspected_orphans(
        resolved_uploads,
        referenced_ids if referenced_ids is not None else set(),
        int(orphan_sample),
    )
    report["suspected_orphans"]["reference_scan_complete"] = not scan_errors
    report["suspected_orphans"]["scan_errors"] = scan_errors
    return report