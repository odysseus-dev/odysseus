"""ADR §7 Tier 2 — campaign narrative digest.

Two-tier chat memory:

  Tier 1 (rolling window)  — the newest 15 `turn_history` rows stay verbatim
                              in every GM prompt (`ROLLING_WINDOW_PAIRS`).
  Tier 2 (campaign digest) — once the active log reaches 30 rows, the oldest
                              15 are condensed into this table and marked
                              `is_active = 0` on `turn_history` (still kanon,
                              just no longer replayed verbatim per turn).

Condensation prefers an LLM call (cheap model, "STRUCTURED SNAPSHOT" style —
don't repeat what's already in the DB) but always has a deterministic
fallback, so the pipeline keeps working end-to-end with the LLM disabled.
Mega-merge: once the digest text itself exceeds a cap, the current text is
archived into `mega_anchors_json` (never deleted) and a fresh digest starts,
per ADR "prior mega nerozpustit, je základ další mega".
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import campaign_chronicle

LOG = logging.getLogger("titan.fugassa.campaign_digest")

ROLLING_WINDOW_PAIRS = 15
CONDENSE_TRIGGER_PAIRS = 30
CONDENSE_BATCH_PAIRS = 15
DIGEST_MEGA_CAP_CHARS = 100_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_row_conn(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT OR IGNORE INTO campaign_digest (id, digest_text) VALUES (1, '')")


def get_digest_conn(conn: sqlite3.Connection) -> dict[str, Any]:
    _ensure_row_conn(conn)
    row = conn.execute("SELECT * FROM campaign_digest WHERE id = 1").fetchone()
    return dict(row) if row else {"digest_text": "", "mega_anchors_json": "[]", "last_condensed_turn": 0}


def get_digest(db_path: str | None) -> dict[str, Any]:
    if not db_path or not os.path.isfile(db_path):
        return {"digest_text": "", "mega_anchors_json": "[]", "last_condensed_turn": 0}
    conn = _connect(db_path)
    try:
        return get_digest_conn(conn)
    finally:
        conn.close()


def build_digest_block(db_path: str | None) -> str:
    """ADR §5/§7 — the condensed-older-history block for the GM prompt."""
    digest = get_digest(db_path)
    text = str(digest.get("digest_text") or "").strip()
    if not text:
        return ""
    anchors = digest.get("mega_anchors_json") or "[]"
    try:
        n_anchors = len(json.loads(anchors))
    except (TypeError, ValueError):
        n_anchors = 0
    prefix = "CAMPAIGN DIGEST (condensed older events — SQL/turn_resolution always wins on conflict"
    if "ENGINE APPENDIX" in text:
        prefix += "; ENGINE APPENDIX sections are authoritative for quest/party/property facts"
    prefix += ")"
    if n_anchors:
        prefix += f"\n[{n_anchors} earlier digest era(s) archived beyond this — still in DB, not replayed here]"
    return f"{prefix}:\n{text}"


def _pending_active_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, turn_number, player_text, ai_text FROM turn_history WHERE is_active = 1 ORDER BY turn_number ASC, id ASC"
    ).fetchall()


def get_min_active_turn(db_path: str | None) -> int:
    """Lowest `turn_number` still verbatim in the rolling window (not yet
    condensed into the digest). The frontend uses this to hide per-message
    "generate scene image" affordances for turns that have condensed —
    their chat_message-scoped assets get hard-deleted at the same time
    (see `condense_pending_conn`), so there is nothing left to show anyway."""
    if not db_path or not os.path.isfile(db_path):
        return 0
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MIN(turn_number) AS t FROM turn_history WHERE is_active = 1").fetchone()
        value = row["t"] if row else None
        return int(value) if value is not None else 0
    finally:
        conn.close()


def _deterministic_condense(rows: list[sqlite3.Row], *, db_path: str | None = None) -> str:
    """No-LLM fallback — one bullet per turn, skipping duplicate player actions."""
    bullets: list[str] = []
    seen_actions: set[str] = set()
    for r in rows:
        p = str(r["player_text"] or "").strip().replace("\n", " ")[:140]
        action_key = p.lower()
        if action_key and action_key in seen_actions:
            continue
        if action_key:
            seen_actions.add(action_key)
        a = str(r["ai_text"] or "").strip().replace("\n", " ")[:220]
        bullets.append(f"- Turn {r['turn_number']}: player \"{p}\" -> {a}")
    text = "\n".join(bullets)
    if db_path and rows:
        turn_min = int(rows[0]["turn_number"])
        turn_max = int(rows[-1]["turn_number"])
        text += campaign_chronicle.build_engine_appendix(db_path, turn_min, turn_max)
    return text


async def _llm_condense(rows: list[sqlite3.Row], *, db_path: str, owner: str | None) -> str | None:
    from titan.fugassa import campaign_facts
    from titan.fugassa.llm_client import chat_completion

    pinned = campaign_facts.list_pinned_facts(db_path, limit=6)
    transcript = "\n".join(
        f"Turn {r['turn_number']} — Player: {str(r['player_text'] or '').strip()[:300]}\n"
        f"Turn {r['turn_number']} — GM: {str(r['ai_text'] or '').strip()[:500]}"
        for r in rows
    )
    system = (
        "You are condensing older turns of a text RPG into a compact narrative digest for "
        "future GM prompts. Carry forward important open threads verbatim where possible; "
        "silence on a prior thread means it is unchanged. Do NOT restate stats, inventory, "
        "quest status, or NPC disposition — those live in the database and are provided "
        "separately every turn. Focus on: what happened, why it matters, unresolved narrative "
        "threads, and any promises/consequences the GM must remember. Write 4-8 dense sentences, "
        "prose, no headers, no bullet lists. If an ENGINE APPENDIX is appended after your "
        "output, treat it as authoritative for quest/party/property facts."
    )
    user = (
        (f"Pinned campaign facts (already known — do not repeat verbatim): {'; '.join(pinned)}\n\n" if pinned else "")
        + f"Turns to condense:\n{transcript[:6000]}"
    )
    try:
        raw = await chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            owner=owner,
            max_tokens=400,
            temperature=0.3,
        )
    except Exception as exc:  # noqa: BLE001 — condensation must never break the turn pipeline
        LOG.warning("LLM digest condensation failed, using deterministic fallback: %s", exc)
        return None
    text = str(raw or "").strip()
    if not text:
        return None
    turn_min = int(rows[0]["turn_number"])
    turn_max = int(rows[-1]["turn_number"])
    return text + campaign_chronicle.build_engine_appendix(db_path, turn_min, turn_max)


def _mega_merge_if_needed_conn(conn: sqlite3.Connection) -> None:
    row = get_digest_conn(conn)
    text = str(row.get("digest_text") or "")
    if len(text) <= DIGEST_MEGA_CAP_CHARS:
        return
    try:
        anchors = json.loads(row.get("mega_anchors_json") or "[]")
    except (TypeError, ValueError):
        anchors = []
    anchors.append({"archived_at": _utc_now(), "char_count": len(text), "up_to_turn": row.get("last_condensed_turn")})
    conn.execute(
        "UPDATE campaign_digest SET digest_text = '', mega_anchors_json = ?, updated_at = ? WHERE id = 1",
        (json.dumps(anchors, ensure_ascii=False), _utc_now()),
    )


def append_digest_conn(conn: sqlite3.Connection, new_text: str, *, last_condensed_turn: int) -> None:
    _ensure_row_conn(conn)
    row = get_digest_conn(conn)
    existing = str(row.get("digest_text") or "").strip()
    incoming = str(new_text or "").strip()
    if not incoming:
        return
    if incoming in existing or existing.endswith(incoming):
        conn.execute(
            "UPDATE campaign_digest SET last_condensed_turn = MAX(last_condensed_turn, ?), updated_at = ? WHERE id = 1",
            (last_condensed_turn, _utc_now()),
        )
        return
    combined = f"{existing}\n{incoming}".strip() if existing else incoming
    conn.execute(
        "UPDATE campaign_digest SET digest_text = ?, last_condensed_turn = ?, updated_at = ? WHERE id = 1",
        (combined, last_condensed_turn, _utc_now()),
    )
    _mega_merge_if_needed_conn(conn)


def condense_pending_conn(
    conn: sqlite3.Connection,
    batch: list[sqlite3.Row],
    *,
    condensed_text: str,
    generated_root: str | None = None,
) -> dict[str, Any]:
    last_turn = int(batch[-1]["turn_number"]) if batch else 0
    append_digest_conn(conn, condensed_text, last_condensed_turn=last_turn)
    ids = [int(r["id"]) for r in batch]
    conn.execute(f"UPDATE turn_history SET is_active = 0 WHERE id IN ({','.join('?' for _ in ids)})", ids)
    deleted_assets = 0
    if generated_root:
        from titan.fugassa.db import asset_repository

        turn_numbers = [int(r["turn_number"]) for r in batch]
        deleted_assets = asset_repository.delete_assets_for_entities_conn(
            conn, generated_root, entity_type="other", asset_type="scene", entity_ids=turn_numbers
        )
    return {"condensed": True, "batch_size": len(batch), "last_condensed_turn": last_turn, "deleted_chat_assets": deleted_assets}


async def maybe_condense(
    db_path: str | None,
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
    generated_root: str | None = None,
) -> dict[str, Any]:
    """Call once per completed turn (ADR §7 workflow step). No-op below the
    30-pair trigger. Never raises — a condensation failure must not break the
    surrounding turn."""
    if not db_path or not os.path.isfile(db_path):
        return {"condensed": False}
    conn = _connect(db_path)
    try:
        rows = _pending_active_rows(conn)
        if len(rows) < CONDENSE_TRIGGER_PAIRS:
            return {"condensed": False, "active_pairs": len(rows)}
        batch = rows[:CONDENSE_BATCH_PAIRS]
        text = None
        if llm_enabled:
            try:
                text = await _llm_condense(batch, db_path=db_path, owner=owner)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("digest condensation LLM path errored: %s", exc)
        if not text:
            text = _deterministic_condense(batch, db_path=db_path)
        result = condense_pending_conn(conn, batch, condensed_text=text, generated_root=generated_root)
        conn.commit()
        return result
    except Exception as exc:  # noqa: BLE001 — digest maintenance must never break the turn
        LOG.warning("campaign_digest.maybe_condense failed: %s", exc)
        return {"condensed": False, "error": str(exc)}
    finally:
        conn.close()
