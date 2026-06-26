"""Read-only storage bloat diagnostics for SQLite chat data and uploads."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, unquote

from src.constants import UPLOAD_DIR


DEFAULT_TABLES = (
    "sessions",
    "chat_messages",
    "chat_messages_fts",
    "documents",
    "document_versions",
    "gallery_images",
)
MAX_LARGEST_ROWS = 20
MAX_INLINE_ROWS = 20
MAX_ORPHAN_ROWS = 50
INLINE_SCAN_LIMIT = 200
MAX_REFERENCE_SCAN_ROWS = 1000
MAX_REFERENCE_CONTENT_CHARS = 20000
MAX_UPLOAD_TRAVERSAL_FILES = 10000
MAX_UPLOAD_TRAVERSAL_DIRS = 2000
DATA_URL_RE = re.compile(r"data:[^;,\s]{1,120};base64,", re.IGNORECASE)
UPLOAD_ID_RE = re.compile(r"\b[0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?\b")
BASE64_CHARS_RE = re.compile(r"[A-Za-z0-9+/=]")


@dataclass
class UploadFileRecord:
    path: Path
    size_bytes: int


@dataclass
class UploadTraversalState:
    visited_files: int = 0
    visited_dirs: int = 0
    skipped_symlink_count: int = 0
    skipped_unreadable_count: int = 0
    truncated: bool = False


def collect_storage_bloat_diagnostics(
    *,
    database_url: str | None = None,
    db_path: str | os.PathLike[str] | None = None,
    upload_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Collect safe, bounded storage diagnostics without mutating state."""
    warnings: list[str] = []
    resolved_db_path = _resolve_sqlite_path(database_url, db_path, warnings)
    db_report, live_upload_ids = _collect_db_report(resolved_db_path, warnings)
    upload_report = _collect_upload_report(
        Path(upload_dir or UPLOAD_DIR),
        live_upload_ids,
        warnings,
    )
    return {
        "status": "success",
        "database": db_report,
        "uploads": upload_report,
        "warnings": warnings,
        "bounds": {
            "largest_content_rows": MAX_LARGEST_ROWS,
            "inline_payload_scan_rows": INLINE_SCAN_LIMIT,
            "inline_payload_rows": MAX_INLINE_ROWS,
            "orphan_uploads": MAX_ORPHAN_ROWS,
            "live_reference_scan_rows": MAX_REFERENCE_SCAN_ROWS,
            "live_reference_content_chars": MAX_REFERENCE_CONTENT_CHARS,
            "upload_traversal_files": MAX_UPLOAD_TRAVERSAL_FILES,
            "upload_traversal_dirs": MAX_UPLOAD_TRAVERSAL_DIRS,
        },
    }


def _resolve_sqlite_path(
    database_url: str | None,
    db_path: str | os.PathLike[str] | None,
    warnings: list[str],
) -> Path | None:
    if db_path is not None:
        return Path(db_path)
    if not database_url:
        return None
    if database_url == "sqlite:///:memory:":
        warnings.append("in-memory SQLite database has no file size")
        return None
    if not database_url.startswith("sqlite:///"):
        warnings.append("unsupported database URL; only SQLite file diagnostics are available")
        return None
    return Path(unquote(database_url.removeprefix("sqlite:///")))


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(db_path.as_posix())}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _collect_db_report(
    db_path: Path | None,
    warnings: list[str],
) -> tuple[dict[str, Any], set[str]]:
    report: dict[str, Any] = {
        "path_present": False,
        "size_bytes": None,
        "tables": {},
        "table_sizes": {"available": False, "objects": {}},
        "content_rows": {"largest": [], "inline_payload_suspects": []},
        "fts": {"present": False, "tables": []},
        "upload_references": {
            "source": "chat_messages.content",
            "live_reference_count": 0,
            "scan_rows": 0,
            "scan_limit": MAX_REFERENCE_SCAN_ROWS,
            "content_char_scan_limit": MAX_REFERENCE_CONTENT_CHARS,
            "complete": False,
        },
    }
    live_upload_ids: set[str] = set()
    if db_path is None:
        warnings.append("database file path unavailable")
        return report, live_upload_ids
    if not db_path.exists():
        warnings.append("database file missing")
        return report, live_upload_ids

    report["path_present"] = True
    report["size_bytes"] = _safe_file_size(db_path)
    try:
        with _connect_readonly(db_path) as conn:
            tables = _list_tables(conn)
            report["table_sizes"] = _table_size_report(conn, warnings)
            size_objects = report["table_sizes"]["objects"]
            report["tables"] = _table_counts(conn, tables, warnings, size_objects)
            report["fts"] = _fts_report(
                conn,
                tables,
                warnings,
                size_objects,
                report["table_sizes"]["available"],
            )
            if "chat_messages" in tables:
                report["content_rows"] = _content_rows_report(conn, warnings)
                references, live_upload_ids = _live_upload_reference_report(
                    conn,
                    warnings,
                    report["tables"].get("chat_messages", {}).get("row_count"),
                )
                report["upload_references"] = references
            else:
                warnings.append("chat_messages table missing")
    except sqlite3.Error as exc:
        warnings.append(f"database diagnostics unavailable: {type(exc).__name__}")
    return report, live_upload_ids


def _safe_file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _list_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    ).fetchall()
    return {str(row["name"]) for row in rows}


def _table_size_report(
    conn: sqlite3.Connection,
    warnings: list[str],
) -> dict[str, Any]:
    try:
        rows = _dbstat_rows(conn)
    except sqlite3.Error as exc:
        warnings.append(f"table_size_bytes_unavailable: {type(exc).__name__}")
        return {"available": False, "objects": {}}

    objects: dict[str, dict[str, int]] = {}
    for row in rows:
        name = str(row["name"])
        objects[name] = {
            "size_bytes": int(row["size_bytes"] or 0),
            "page_count": int(row["page_count"] or 0),
        }
    return {"available": True, "objects": objects}


def _dbstat_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT name, SUM(pgsize) AS size_bytes, COUNT(*) AS page_count
        FROM dbstat
        GROUP BY name
        """
    ).fetchall()


def _table_counts(
    conn: sqlite3.Connection,
    tables: set[str],
    warnings: list[str],
    size_objects: dict[str, dict[str, int]],
) -> dict[str, dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for table in DEFAULT_TABLES:
        size_info = size_objects.get(table, {})
        if table not in tables:
            counts[table] = {
                "present": False,
                "row_count": None,
                "size_bytes": None,
                "page_count": None,
            }
            continue
        try:
            counts[table] = {
                "present": True,
                "row_count": conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],
                "size_bytes": size_info.get("size_bytes"),
                "page_count": size_info.get("page_count"),
            }
        except sqlite3.Error as exc:
            counts[table] = {
                "present": True,
                "row_count": None,
                "size_bytes": size_info.get("size_bytes"),
                "page_count": size_info.get("page_count"),
            }
            warnings.append(f"{table} row count unavailable: {exc}")
    return counts


def _fts_report(
    conn: sqlite3.Connection,
    tables: set[str],
    warnings: list[str],
    size_objects: dict[str, dict[str, int]],
    size_available: bool,
) -> dict[str, Any]:
    fts_tables = sorted(
        name for name in tables
        if name.endswith("_fts") or "_fts_" in name
    )
    report: dict[str, Any] = {
        "present": "chat_messages_fts" in tables,
        "tables": fts_tables,
        "size_available": size_available,
        "size_bytes": _sum_object_sizes(fts_tables, size_objects) if size_available else None,
        "table_sizes": {
            table: size_objects[table]
            for table in fts_tables
            if table in size_objects
        },
    }
    if "chat_messages_fts" not in tables:
        warnings.append("chat_messages_fts table missing")
        return report
    try:
        report["chat_messages_fts_row_count"] = conn.execute(
            "SELECT COUNT(*) FROM chat_messages_fts"
        ).fetchone()[0]
    except sqlite3.Error as exc:
        report["chat_messages_fts_row_count"] = None
        warnings.append(f"chat_messages_fts row count unavailable: {exc}")
    for shadow in ("chat_messages_fts_data", "chat_messages_fts_idx", "chat_messages_fts_docsize"):
        if shadow in tables:
            try:
                report[f"{shadow}_row_count"] = conn.execute(
                    f'SELECT COUNT(*) FROM "{shadow}"'
                ).fetchone()[0]
            except sqlite3.Error as exc:
                warnings.append(f"{shadow} row count unavailable: {exc}")
    return report


def _sum_object_sizes(
    names: list[str],
    size_objects: dict[str, dict[str, int]],
) -> int:
    return sum(size_objects.get(name, {}).get("size_bytes", 0) for name in names)


def _content_rows_report(conn: sqlite3.Connection, warnings: list[str]) -> dict[str, Any]:
    rows = _largest_message_rows(conn, INLINE_SCAN_LIMIT, warnings)
    largest = [_safe_row_metadata(row) for row in rows[:MAX_LARGEST_ROWS]]
    suspects = []
    for row in rows:
        reasons = _inline_payload_reasons(row["content_sample"] or "")
        if not reasons:
            continue
        meta = _safe_row_metadata(row)
        meta["reasons"] = reasons
        suspects.append(meta)
        if len(suspects) >= MAX_INLINE_ROWS:
            break
    if any((row["char_length"] or 0) > MAX_REFERENCE_CONTENT_CHARS for row in rows):
        warnings.append("inline payload scan limited by content length")
    return {
        "largest": largest,
        "inline_payload_suspects": suspects,
        "inline_payload_suspect_count_in_scan": sum(
            1 for row in rows if _inline_payload_reasons(row["content_sample"] or "")
        ),
        "inline_payload_scan_rows": len(rows),
    }


def _largest_message_rows(
    conn: sqlite3.Connection,
    limit: int,
    warnings: list[str],
) -> list[sqlite3.Row]:
    try:
        return conn.execute(
            """
            SELECT id, session_id, role,
                   substr(content, 1, ?) AS content_sample,
                   length(content) AS char_length,
                   length(CAST(content AS BLOB)) AS byte_length
            FROM chat_messages
            ORDER BY byte_length DESC
            LIMIT ?
            """,
            (MAX_REFERENCE_CONTENT_CHARS, limit),
        ).fetchall()
    except sqlite3.Error as exc:
        warnings.append(f"largest chat message rows unavailable: {exc}")
        return []


def _safe_row_metadata(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "char_length": row["char_length"] or 0,
        "byte_length": row["byte_length"] or 0,
    }


def _inline_payload_reasons(content: str) -> list[str]:
    reasons: list[str] = []
    if DATA_URL_RE.search(content):
        reasons.append("data_url_base64")
    if len(UPLOAD_ID_RE.findall(content)) >= 5:
        reasons.append("many_upload_ids")
    if _base64_density(content) >= 0.85 and len(content) >= 4096:
        reasons.append("base64_heavy")
    return reasons


def _base64_density(content: str) -> float:
    sample = content[:20000]
    if not sample:
        return 0.0
    return len(BASE64_CHARS_RE.findall(sample)) / len(sample)


def _live_upload_reference_report(
    conn: sqlite3.Connection,
    warnings: list[str],
    total_chat_rows: Any,
) -> tuple[dict[str, Any], set[str]]:
    report: dict[str, Any] = {
        "source": "chat_messages.content",
        "live_reference_count": 0,
        "scan_rows": 0,
        "scan_limit": MAX_REFERENCE_SCAN_ROWS,
        "content_char_scan_limit": MAX_REFERENCE_CONTENT_CHARS,
        "complete": False,
    }
    try:
        rows = conn.execute(
            """
            SELECT substr(content, 1, ?) AS content_sample,
                   length(content) AS char_length
            FROM chat_messages
            ORDER BY length(CAST(content AS BLOB)) DESC
            LIMIT ?
            """,
            (MAX_REFERENCE_CONTENT_CHARS, MAX_REFERENCE_SCAN_ROWS),
        ).fetchall()
    except sqlite3.Error as exc:
        warnings.append(f"live upload reference scan unavailable: {exc}")
        return report, set()

    content_samples = [str(row["content_sample"] or "") for row in rows]
    live_ids = _content_upload_ids(content_samples)
    scanned_all_rows = not isinstance(total_chat_rows, int) or total_chat_rows <= len(rows)
    scanned_full_content = all(
        (row["char_length"] or 0) <= MAX_REFERENCE_CONTENT_CHARS
        for row in rows
    )
    if not scanned_all_rows:
        warnings.append("live upload reference scan limited by row count")
    if not scanned_full_content:
        warnings.append("live upload reference scan limited by content length")

    report.update({
        "live_reference_count": len(live_ids),
        "scan_rows": len(rows),
        "complete": scanned_all_rows and scanned_full_content,
    })
    return report, live_ids


def _content_upload_ids(content_samples: list[str]) -> set[str]:
    ids: set[str] = set()
    for sample in content_samples:
        ids.update(UPLOAD_ID_RE.findall(sample))
    return ids


def _collect_upload_report(
    upload_dir: Path,
    live_upload_ids: set[str],
    warnings: list[str],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "present": False,
        "total_size_bytes": 0,
        "total_size_bytes_observed": 0,
        "file_count": 0,
        "file_count_observed": 0,
        "truncated": False,
        "bounds": {
            "max_visited_files": MAX_UPLOAD_TRAVERSAL_FILES,
            "max_visited_dirs": MAX_UPLOAD_TRAVERSAL_DIRS,
            "orphan_sample_rows": MAX_ORPHAN_ROWS,
        },
        "visited_files": 0,
        "visited_dirs": 0,
        "skipped_symlink_count": 0,
        "skipped_unreadable_count": 0,
        "manifest_present": False,
        "manifest_entry_count": 0,
        "manifest_entries": {"count": 0, "sample": []},
        "suspected_orphans": {"count": 0, "sample": []},
    }
    if not upload_dir.exists():
        warnings.append("upload directory missing")
        return report
    if not upload_dir.is_dir():
        warnings.append("upload path is not a directory")
        return report

    report["present"] = True
    manifest = _load_upload_manifest(upload_dir, warnings)
    manifest_ids = _manifest_upload_ids(manifest or {})
    file_report = _upload_file_report(upload_dir, manifest_ids, live_upload_ids, warnings)
    report.update(file_report)
    report["manifest_present"] = manifest is not None
    report["manifest_entry_count"] = len(manifest or {})
    report["manifest_entries"] = _manifest_entry_report(manifest or {})
    return report


def _upload_file_report(
    upload_dir: Path,
    manifest_ids: set[str],
    live_upload_ids: set[str],
    warnings: list[str],
) -> dict[str, Any]:
    state = UploadTraversalState(visited_dirs=1)
    total_size = 0
    file_count = 0
    orphan_count = 0
    orphan_sample: list[dict[str, Any]] = []

    for record in _iter_upload_files(upload_dir, state):
        file_count += 1
        total_size += record.size_bytes
        if _upload_file_is_live(record.path, live_upload_ids):
            continue
        orphan_count += 1
        if len(orphan_sample) < MAX_ORPHAN_ROWS:
            orphan_sample.append(
                _upload_sample(record, upload_dir, manifest_ids, live_upload_ids)
            )

    if state.truncated:
        warnings.append("upload_traversal_truncated")
    return {
        "file_count": None if state.truncated else file_count,
        "file_count_observed": file_count,
        "total_size_bytes": None if state.truncated else total_size,
        "total_size_bytes_observed": total_size,
        "truncated": state.truncated,
        "visited_files": state.visited_files,
        "visited_dirs": state.visited_dirs,
        "skipped_symlink_count": state.skipped_symlink_count,
        "skipped_unreadable_count": state.skipped_unreadable_count,
        "suspected_orphans": {
            "count": orphan_count,
            "observed_count": orphan_count,
            "sample": orphan_sample,
        },
    }


def _iter_upload_files(
    upload_dir: Path,
    state: UploadTraversalState,
) -> Iterator[UploadFileRecord]:
    stack = [upload_dir]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    result = _visit_upload_entry(entry, stack, state)
                    if state.truncated:
                        return
                    if result is not None:
                        yield result
        except OSError:
            state.skipped_unreadable_count += 1


def _visit_upload_entry(
    entry: os.DirEntry[str],
    stack: list[Path],
    state: UploadTraversalState,
) -> UploadFileRecord | None:
    if entry.name.startswith("."):
        return None
    try:
        if entry.is_dir(follow_symlinks=False):
            _queue_upload_dir(Path(entry.path), stack, state)
            return None
        if not _is_upload_file_candidate(entry.name):
            return None
        if state.visited_files >= MAX_UPLOAD_TRAVERSAL_FILES:
            state.truncated = True
            return None
        state.visited_files += 1
        if entry.is_symlink():
            state.skipped_symlink_count += 1
            return None
        if not entry.is_file(follow_symlinks=False):
            return None
        stat_result = entry.stat(follow_symlinks=False)
    except OSError:
        state.skipped_unreadable_count += 1
        return None
    return UploadFileRecord(Path(entry.path), int(stat_result.st_size))


def _queue_upload_dir(
    path: Path,
    stack: list[Path],
    state: UploadTraversalState,
) -> None:
    if state.visited_dirs >= MAX_UPLOAD_TRAVERSAL_DIRS:
        state.truncated = True
        return
    state.visited_dirs += 1
    stack.append(path)


def _is_upload_file_candidate(filename: str) -> bool:
    return filename not in {"uploads.json", "uploads.json.bak"}


def _load_upload_manifest(upload_dir: Path, warnings: list[str]) -> dict[str, Any] | None:
    manifest_path = upload_dir / "uploads.json"
    try:
        manifest_path.lstat()
    except FileNotFoundError:
        warnings.append("uploads.json manifest missing")
        return None
    except OSError as exc:
        warnings.append(f"uploads.json manifest unavailable: {type(exc).__name__}")
        return None
    if manifest_path.is_symlink():
        warnings.append("uploads.json manifest symlink skipped")
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"uploads.json manifest unreadable: {type(exc).__name__}")
        return None
    if not isinstance(data, dict):
        warnings.append("uploads.json manifest has unsupported shape")
        return None
    return data


def _manifest_entry_report(manifest: dict[str, Any]) -> dict[str, Any]:
    ids = sorted(_manifest_upload_ids(manifest))
    return {
        "count": len(ids),
        "sample": [
            {"stored_name_hash": _hash_upload_identifier(upload_id)}
            for upload_id in ids[:MAX_ORPHAN_ROWS]
        ],
    }


def _upload_sample(
    record: UploadFileRecord,
    upload_dir: Path,
    manifest_ids: set[str],
    live_upload_ids: set[str],
) -> dict[str, Any]:
    return {
        "path_hash": _hash_upload_identifier(_relative_upload_path(upload_dir, record.path)),
        "relative_dir_bucket": _safe_relative_dir_bucket(upload_dir, record.path),
        "size_bytes": record.size_bytes,
        "manifest_present": _upload_file_in_manifest(record.path, manifest_ids),
        "live_db_reference_found": _upload_file_is_live(record.path, live_upload_ids),
    }


def _upload_file_is_live(path: Path, live_upload_ids: set[str]) -> bool:
    return path.name in live_upload_ids or path.stem in live_upload_ids


def _upload_file_in_manifest(path: Path, manifest_ids: set[str]) -> bool:
    return path.name in manifest_ids or path.stem in manifest_ids


def _manifest_upload_ids(manifest: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for row in manifest.values():
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            ids.add(row["id"])
    return ids


def _relative_upload_path(upload_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(upload_dir).as_posix()
    except ValueError:
        return path.name


def _hash_upload_identifier(identifier: str) -> str:
    return hashlib.sha256(identifier.encode("utf-8", errors="replace")).hexdigest()[:16]


def _safe_relative_dir_bucket(upload_dir: Path, path: Path) -> str:
    try:
        relative = path.parent.relative_to(upload_dir)
    except ValueError:
        return ""
    parts = relative.parts
    if not parts:
        return ""
    if (
        len(parts) == 3
        and re.fullmatch(r"\d{4}", parts[0])
        and re.fullmatch(r"\d{2}", parts[1])
        and re.fullmatch(r"\d{2}", parts[2])
    ):
        return relative.as_posix()
    return "other"
