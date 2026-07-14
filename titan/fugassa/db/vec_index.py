"""Optional sqlite-vec semantic recall — ADR §8 (M7, lowest-priority fallback layer).

`sqlite-vec` is a loadable SQLite extension (`vec0` virtual tables) that lives
entirely inside `game.db` — ADR §8 is explicit that Fugassa never uses Chroma.
This module degrades gracefully when the `sqlite-vec` pip package isn't
installed, the extension fails to load, or no embedding backend is reachable:
indexing/recall silently become no-ops and the rest of the turn pipeline
(structured SQL, quest/combat/social engines, top-K per-NPC memory) is
completely unaffected. This is intentionally the *only* soft-optional layer in
the memory stack (ADR §4 layer 4) — never the canon, never required.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import struct
from typing import Any

LOG = logging.getLogger("titan.fugassa.vec_index")

# kind -> vec0 virtual table name. `rowid` on each table is the source row's
# own primary key (event_log.id / npc_memories.id) — no join table needed.
_TABLES = {
    "event_log": "vec_event_log",
    "npc_memory": "vec_npc_memory",
}

_availability_checked = False
_available = False


def is_available() -> bool:
    """True if the sqlite-vec extension can actually be loaded in this environment.

    Cached per-process — cheap to call from the hot turn path.
    """
    global _availability_checked, _available
    if _availability_checked:
        return _available
    _availability_checked = True
    try:
        import sqlite_vec

        conn = sqlite3.connect(":memory:")
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            _available = True
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — any failure just means "not available"
        LOG.info(
            "sqlite-vec not available (%s) — semantic recall disabled; "
            "structured SQL memory (quests/npc top-K) is unaffected",
            exc,
        )
        _available = False
    return _available


def _load_ext(conn: sqlite3.Connection) -> None:
    import sqlite_vec

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def _serialize(vec: Any) -> bytes:
    values = [float(v) for v in vec]
    return struct.pack(f"<{len(values)}f", *values)


def _ensure_table(conn: sqlite3.Connection, kind: str, dim: int) -> str | None:
    table = _TABLES.get(kind)
    if not table:
        return None
    try:
        conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING vec0(embedding float[{dim}])")
        return table
    except sqlite3.OperationalError as exc:
        LOG.warning("vec_index: could not create %s (dim=%s): %s", table, dim, exc)
        return None


def _get_embedder():
    try:
        from src.embeddings import get_embedding_client

        return get_embedding_client()
    except Exception as exc:  # noqa: BLE001
        LOG.info("vec_index: embedding client unavailable (%s)", exc)
        return None


def index_text(db_path: str, kind: str, row_id: int, text: str) -> bool:
    """Embed `text` and upsert it into the vec table under `row_id`.

    `kind` is "event_log" (row_id = event_log.id) or "npc_memory" (row_id =
    npc_memories.id). Best-effort and silent — never raises, never blocks the
    turn pipeline that calls it.
    """
    text = (text or "").strip()
    if not text or not db_path or not os.path.isfile(db_path) or not is_available():
        return False
    embedder = _get_embedder()
    if embedder is None:
        return False
    try:
        vecs = embedder.encode([text])
        if vecs is None or len(vecs) == 0:
            return False
        vec = vecs[0]
    except Exception as exc:  # noqa: BLE001
        LOG.info("vec_index: embedding failed (%s)", exc)
        return False

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        _load_ext(conn)
        table = _ensure_table(conn, kind, len(vec))
        if not table:
            return False
        conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (row_id,))
        conn.execute(f"INSERT INTO {table}(rowid, embedding) VALUES (?, ?)", (row_id, _serialize(vec)))
        conn.commit()
        return True
    except sqlite3.Error as exc:
        LOG.info("vec_index: index_text failed (%s)", exc)
        return False
    finally:
        conn.close()


def semantic_recall(db_path: str, kind: str, query_text: str, *, top_k: int = 5) -> list[dict[str, Any]]:
    """Return up to `top_k` {row_id, distance} matches for `query_text`, or [] if unavailable.

    Only ever a supplementary fallback (ADR §8: "query only when structured
    SELECT + FTS + graph aren't enough") — callers must never treat an empty
    result as an error.
    """
    query_text = (query_text or "").strip()
    if not query_text or not db_path or not os.path.isfile(db_path) or not is_available():
        return []
    table = _TABLES.get(kind)
    if not table:
        return []
    embedder = _get_embedder()
    if embedder is None:
        return []
    try:
        vecs = embedder.encode([query_text])
        if vecs is None or len(vecs) == 0:
            return []
        vec = vecs[0]
    except Exception as exc:  # noqa: BLE001
        LOG.info("vec_index: query embedding failed (%s)", exc)
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _load_ext(conn)
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            f"SELECT rowid, distance FROM {table} WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (_serialize(vec), int(top_k)),
        ).fetchall()
        return [{"row_id": int(r["rowid"]), "distance": float(r["distance"])} for r in rows]
    except sqlite3.Error as exc:
        LOG.info("vec_index: semantic_recall failed (%s)", exc)
        return []
    finally:
        conn.close()


def remove(db_path: str, kind: str, row_id: int) -> None:
    """Drop a vector when its source row is deleted/archived. Best-effort."""
    table = _TABLES.get(kind)
    if not table or not db_path or not os.path.isfile(db_path) or not is_available():
        return
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        _load_ext(conn)
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if exists:
            conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (row_id,))
            conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()
