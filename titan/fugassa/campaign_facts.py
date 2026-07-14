"""ADR §5 context table row 7 — `campaign_facts`: curated, pinned long-term
facts (renown milestones, revealed betrayals, world-changing decisions).

Distinct from `event_log` (everything that happens) and `scene_summaries`
(per-location recap) — this is the short, hand-picked list the GM must never
contradict. Populated by engine code only (renown grants, agenda reveals,
validated archivist `add campaign_fact` ops) — never invented mid-prose.

Priority (ADR §7): SQLite + turn_resolution > pinned facts > campaign digest >
rolling chat > vec recall.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def pin_fact_conn(
    conn: sqlite3.Connection,
    fact_text: str,
    *,
    known_by: str | None = None,
    source_event_id: int | None = None,
    pinned: bool = True,
) -> int | None:
    text = str(fact_text or "").strip()
    if not text:
        return None
    text = text[:400]
    # De-dupe — repeated triggers (e.g. re-evaluating the same betrayal on a
    # later turn) must not spam the pinned list.
    existing = conn.execute("SELECT id FROM campaign_facts WHERE fact_text = ? LIMIT 1", (text,)).fetchone()
    if existing:
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO campaign_facts (fact_text, known_by, source_event_id, pinned, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (text, known_by, source_event_id, 1 if pinned else 0, _utc_now()),
    )
    return int(cur.lastrowid)


def pin_fact(
    db_path: str | None,
    fact_text: str,
    *,
    known_by: str | None = None,
    source_event_id: int | None = None,
    pinned: bool = True,
) -> int | None:
    if not db_path or not os.path.isfile(db_path):
        return None
    conn = _connect(db_path)
    try:
        fact_id = pin_fact_conn(conn, fact_text, known_by=known_by, source_event_id=source_event_id, pinned=pinned)
        conn.commit()
        return fact_id
    finally:
        conn.close()


def list_pinned_facts(db_path: str | None, *, limit: int = 8) -> list[str]:
    if not db_path or not os.path.isfile(db_path):
        return []
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT fact_text FROM campaign_facts WHERE pinned = 1 ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [str(r["fact_text"]) for r in rows]
    finally:
        conn.close()


def unpin_fact(db_path: str | None, fact_id: int) -> bool:
    """Wizard/debug escape hatch — a fact stays in the DB (kanon), just drops off the prompt."""
    if not db_path or not os.path.isfile(db_path):
        return False
    conn = _connect(db_path)
    try:
        cur = conn.execute("UPDATE campaign_facts SET pinned = 0 WHERE id = ?", (fact_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
