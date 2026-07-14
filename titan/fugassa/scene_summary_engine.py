"""ADR §2 layer 3 / §7 — `scene_summaries`: "popis lokace po odchodu, ne
náhrada chat digestu" (a per-location recap written the moment the player
leaves, distinct from and never a substitute for the rolling chat digest).

Deterministic, no LLM: composed from the `event_log` rows written while the
player was at that location (turn-range bounded), so it stays cheap and
always available even with the LLM disabled.

Per-turn `scene_turn_deltas` (Sprint 2 G4) capture one sentence of what changed
each turn at the current location — injected into GM context so the model
advances instead of repeating prior beats.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

_ENTRY_TURNS_KEY = "_location_entry_turn"
_DELTA_MAX_LEN = 220
_SCENE_CAST_RE = re.compile(r"\[Scene cast[^\]]*\]\s*", re.IGNORECASE)


def _strip_scene_cast_metadata(text: str) -> str:
    """Remove chat metadata prefix — not narrative for per-turn deltas."""
    return _SCENE_CAST_RE.sub("", str(text or ""), count=1).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _dedupe_bullets(bullets: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in bullets:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _events_for_location_exit(
    conn: sqlite3.Connection,
    *,
    from_location_id: int,
    turn_start: int,
    turn_end: int,
) -> list[str]:
    """ADR C4 — typed chronicle rollup for location exit recap."""
    conn.row_factory = sqlite3.Row
    loc_rows = conn.execute(
        """
        SELECT event_type, summary FROM event_log
        WHERE turn_id BETWEEN ? AND ? AND is_active = 1
          AND (location_id = ? OR location_id IS NULL)
        ORDER BY turn_id ASC, id ASC
        """,
        (turn_start, turn_end, int(from_location_id)),
    ).fetchall()
    if not loc_rows:
        loc_rows = conn.execute(
            """
            SELECT event_type, summary FROM event_log
            WHERE turn_id BETWEEN ? AND ? AND is_active = 1
            ORDER BY turn_id ASC, id ASC
            """,
            (turn_start, turn_end),
        ).fetchall()
    typed = [str(r["summary"] or "").strip() for r in loc_rows if str(r["event_type"] or "") != "turn"]
    turn_rows = [str(r["summary"] or "").strip() for r in loc_rows if str(r["event_type"] or "") == "turn"]
    return _dedupe_bullets([b for b in typed if b] + [b for b in turn_rows if b])


def _first_sentence(text: str, *, max_len: int = _DELTA_MAX_LEN) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
    if len(sentence) > max_len:
        sentence = sentence[: max_len - 3].rstrip() + "..."
    if sentence and sentence[-1] not in ".!?":
        sentence += "."
    return sentence


def compose_turn_delta(
    player_text: str,
    gm_prose: str,
    *,
    turn_number: int,
    db_path: str | None = None,
    turn_resolution: Any | None = None,
) -> str:
    """One-sentence deterministic delta — ADR C4: GM first, engine chronicle, then player."""
    gm_narrative = ""
    gm_source = _strip_scene_cast_metadata(gm_prose)
    if gm_source:
        try:
            from titan.fugassa.gm_response_parser import extract_current_scene_narrative

            gm_narrative = extract_current_scene_narrative(gm_source).strip()
        except Exception:  # noqa: BLE001
            gm_narrative = ""
        if not gm_narrative:
            gm_narrative = gm_source
    gm_sentence = _first_sentence(gm_narrative)
    if gm_sentence:
        return gm_sentence

    from titan.fugassa import campaign_chronicle

    engine_line = campaign_chronicle.engine_summary_for_turn(
        db_path,
        int(turn_number),
        turn_resolution=turn_resolution,
    )
    engine_sentence = _first_sentence(engine_line)
    if engine_sentence:
        return engine_sentence

    player_sentence = _first_sentence(str(player_text or ""))
    if player_sentence:
        return player_sentence
    return f"Turn {turn_number}: the scene advanced."


def record_turn_delta(
    db_path: str | None,
    *,
    location_id: int,
    turn_number: int,
    player_text: str,
    gm_prose: str,
    turn_resolution: Any | None = None,
) -> str | None:
    """Persist mandatory per-turn delta for the active location."""
    if not db_path or not os.path.isfile(db_path) or not location_id:
        return None
    delta = compose_turn_delta(
        player_text,
        gm_prose,
        turn_number=int(turn_number),
        db_path=db_path,
        turn_resolution=turn_resolution,
    )
    player_excerpt = re.sub(r"\s+", " ", str(player_text or "").strip())[:240]
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO scene_turn_deltas (location_id, turn_number, delta_text, player_excerpt, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(location_id, turn_number) DO UPDATE SET
                delta_text = excluded.delta_text,
                player_excerpt = excluded.player_excerpt,
                created_at = excluded.created_at
            """,
            (int(location_id), int(turn_number), delta, player_excerpt or None, _utc_now()),
        )
        conn.commit()
        return delta
    finally:
        conn.close()


def repair_scene_turn_deltas(db_path: str | None, *, dry_run: bool = False) -> dict[str, Any]:
    """Rebuild GM-first per-turn deltas from turn_history (ADR C4 backfill)."""
    summary: dict[str, Any] = {"rebuilt": 0, "skipped": 0, "turns": []}
    if not db_path or not os.path.isfile(db_path):
        summary["error"] = "no_db"
        return summary

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT turn_number, player_text, ai_text FROM turn_history ORDER BY turn_number ASC"
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        turn_number = int(row["turn_number"])
        player_text = str(row["player_text"] or "")
        gm_prose = str(row["ai_text"] or "")
        if not player_text.strip() and not gm_prose.strip():
            summary["skipped"] += 1
            continue

        conn = _connect(db_path)
        try:
            loc_row = conn.execute(
                "SELECT location_id FROM scene_turn_deltas WHERE turn_number = ? LIMIT 1",
                (turn_number,),
            ).fetchone()
            if not loc_row:
                loc_row = conn.execute(
                    """
                    SELECT location_id FROM event_log
                    WHERE turn_id = ? AND location_id IS NOT NULL AND is_active = 1
                    ORDER BY id DESC LIMIT 1
                    """,
                    (turn_number,),
                ).fetchone()
            location_id = int(loc_row["location_id"]) if loc_row and loc_row["location_id"] else None
        finally:
            conn.close()

        if not location_id:
            summary["skipped"] += 1
            continue

        if dry_run:
            delta = compose_turn_delta(
                player_text,
                gm_prose,
                turn_number=turn_number,
                db_path=db_path,
            )
            summary["turns"].append({"turn": turn_number, "delta_preview": delta[:120]})
            summary["rebuilt"] += 1
            continue

        record_turn_delta(
            db_path,
            location_id=location_id,
            turn_number=turn_number,
            player_text=player_text,
            gm_prose=gm_prose,
        )
        summary["rebuilt"] += 1

    return summary


def latest_turn_deltas_for_location(
    db_path: str | None,
    location_id: int | None,
    *,
    limit: int = 5,
    since_turn: int | None = None,
) -> list[dict[str, Any]]:
    if not db_path or not location_id or not os.path.isfile(db_path):
        return []
    conn = _connect(db_path)
    try:
        if since_turn is not None:
            rows = conn.execute(
                """
                SELECT turn_number, delta_text FROM scene_turn_deltas
                WHERE location_id = ? AND turn_number >= ?
                ORDER BY turn_number DESC LIMIT ?
                """,
                (int(location_id), int(since_turn), int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT turn_number, delta_text FROM scene_turn_deltas
                WHERE location_id = ?
                ORDER BY turn_number DESC LIMIT ?
                """,
                (int(location_id), int(limit)),
            ).fetchall()
        return [
            {"turn_number": int(r["turn_number"]), "delta_text": str(r["delta_text"] or "").strip()}
            for r in rows
            if str(r["delta_text"] or "").strip()
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _rollup_delta_text(
    conn: sqlite3.Connection,
    *,
    location_id: int,
    turn_start: int,
    turn_end: int,
    bullets: list[str],
) -> str:
    try:
        row = conn.execute(
            """
            SELECT delta_text FROM scene_turn_deltas
            WHERE location_id = ? AND turn_number BETWEEN ? AND ?
            ORDER BY turn_number DESC LIMIT 1
            """,
            (int(location_id), int(turn_start), int(turn_end)),
        ).fetchone()
        if row and str(row["delta_text"] or "").strip():
            return str(row["delta_text"]).strip()
    except sqlite3.OperationalError:
        pass
    if bullets:
        last = str(bullets[-1])
        if last.lower().startswith("turn "):
            parts = last.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
        return last[:_DELTA_MAX_LEN]
    return ""


def mark_location_entered(state: dict[str, Any], location_id: int | None, turn: int) -> None:
    """Record the turn a location was entered, so the eventual exit knows the turn range."""
    if not location_id:
        return
    entry = state.setdefault(_ENTRY_TURNS_KEY, {})
    entry[str(int(location_id))] = int(turn)


def generate_on_location_exit_conn(
    conn: sqlite3.Connection,
    *,
    from_location_id: int,
    turn_start: int,
    turn_end: int,
) -> int | None:
    bullets = _events_for_location_exit(
        conn,
        from_location_id=int(from_location_id),
        turn_start=int(turn_start),
        turn_end=int(turn_end),
    )
    if not bullets:
        return None
    loc = conn.execute("SELECT name FROM locations WHERE id = ?", (from_location_id,)).fetchone()
    name = str(loc["name"]) if loc and loc["name"] else f"location #{from_location_id}"
    text = f"At {name} (turns {turn_start}-{turn_end}): " + "; ".join(bullets)
    text = text[:900]
    delta_text = _rollup_delta_text(
        conn,
        location_id=int(from_location_id),
        turn_start=int(turn_start),
        turn_end=int(turn_end),
        bullets=bullets,
    )
    prev = conn.execute(
        """
        SELECT summary_text FROM scene_summaries
        WHERE location_id = ? ORDER BY id DESC LIMIT 1
        """,
        (int(from_location_id),),
    ).fetchone()
    if prev and str(prev["summary_text"] or "").strip() == text.strip():
        return None
    conn.execute(
        """
        INSERT INTO scene_summaries (location_id, summary_text, delta_text, turn_start, turn_end, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (int(from_location_id), text, delta_text or None, int(turn_start), int(turn_end), _utc_now()),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def generate_on_location_exit(
    db_path: str | None,
    state: dict[str, Any],
    *,
    from_location_id: int,
    turn_end: int,
) -> int | None:
    if not db_path or not os.path.isfile(db_path):
        return None
    entry_map = state.get(_ENTRY_TURNS_KEY) or {}
    turn_start = int(entry_map.get(str(from_location_id), 0))
    conn = _connect(db_path)
    try:
        row_id = generate_on_location_exit_conn(
            conn,
            from_location_id=int(from_location_id),
            turn_start=turn_start,
            turn_end=int(turn_end),
        )
        conn.commit()
        return row_id
    finally:
        conn.close()


def _summary_bullet_key(text: str) -> str:
    """Normalize summary body so identical event bullets dedupe across locations."""
    raw = str(text or "").strip()
    match = re.match(r"At .+ \(turns \d+-\d+\): (.+)", raw, flags=re.DOTALL)
    return (match.group(1) if match else raw).strip().lower()


def dedupe_scene_summaries(db_path: str) -> int:
    """Remove duplicate scene summary rows (exact text or identical event bullets)."""
    if not db_path or not os.path.isfile(db_path):
        return 0
    conn = _connect(db_path)
    removed = 0
    try:
        rows = conn.execute(
            """
            SELECT id, location_id, summary_text
            FROM scene_summaries
            ORDER BY id DESC
            """
        ).fetchall()
        seen_exact: set[tuple[int | None, str]] = set()
        seen_bullets: set[str] = set()
        for row in rows:
            text = str(row["summary_text"] or "").strip()
            exact_key = (row["location_id"], text)
            bullet_key = _summary_bullet_key(text)
            if (
                not text
                or exact_key in seen_exact
                or (bullet_key and bullet_key in seen_bullets)
            ):
                conn.execute("DELETE FROM scene_summaries WHERE id = ?", (int(row["id"]),))
                removed += 1
            else:
                seen_exact.add(exact_key)
                if bullet_key:
                    seen_bullets.add(bullet_key)
        conn.commit()
        return removed
    finally:
        conn.close()


def latest_summaries_for_location(db_path: str | None, location_id: int | None, *, limit: int = 2) -> list[str]:
    if not db_path or not location_id or not os.path.isfile(db_path):
        return []
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT summary_text, delta_text FROM scene_summaries
            WHERE location_id = ? ORDER BY id DESC LIMIT ?
            """,
            (int(location_id), int(limit)),
        ).fetchall()
        out: list[str] = []
        for r in rows:
            summary = str(r["summary_text"] or "").strip()
            delta = str(r["delta_text"] or "").strip()
            if summary and delta:
                out.append(f"{summary} [Last change: {delta}]")
            elif summary:
                out.append(summary)
        return out
    finally:
        conn.close()