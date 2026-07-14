"""Player renown — ADR §E2: group-scale reputation, separate from personal
`npc_relationships`.

Two parallel tracks (ADR §E2 intro):

  Personal track    -> npc_relationships + hexagon drift (combat/social engines)
  Group track       -> player_renown + renown_reactions (this module)

A tier-4 event (saving a kingdom, a massacre) never iterates every NPC's
hexagon — it grants the player ONE `player_renown` row, and individual NPCs
react to it only when they first have reason to (their faction/region matches
a tag, checked once at first contact — see `apply_renown_on_first_contact`).
Tier-1 events are ephemeral and never become renown at all (§E2f).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import campaign_facts

MIN_TIER_FOR_RENOWN = 2  # ADR §E2a: tier 1 = ephemeral, never a renown tag


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _hero_pc_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
    return int(row["id"]) if row else None


def grant_renown_conn(
    conn: sqlite3.Connection,
    *,
    renown_code: str,
    scope_type: str,
    valence: str = "positive",
    impact_tier: int,
    title_display: str | None = None,
    scope_id: str | None = None,
    source_event_id: int | None = None,
    granted_at_turn: int = 0,
    in_game_day: int | None = None,
    bonuses_json: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Same-transaction variant — use when the caller (e.g. `quest_engine`
    reward granting) already holds an open write connection."""
    if impact_tier < MIN_TIER_FOR_RENOWN:
        return None
    if scope_type not in ("faction", "region", "global"):
        raise ValueError("scope_type must be 'faction', 'region', or 'global'")

    memory_duration = "permanent" if impact_tier >= 4 else "arc"
    pc_id = _hero_pc_id(conn)
    if not pc_id:
        return None
    from titan.fugassa.title_engine import default_bonuses_for_tier

    bonuses = bonuses_json if isinstance(bonuses_json, dict) else default_bonuses_for_tier(impact_tier)
    conn.execute(
        """
        INSERT INTO player_renown (
            player_character_id, renown_code, title_display, scope_type, scope_id,
            valence, impact_tier, memory_duration, source_event_id, granted_at_turn,
            in_game_day, bonuses_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pc_id,
            renown_code,
            title_display or renown_code,
            scope_type,
            scope_id,
            valence,
            impact_tier,
            memory_duration,
            source_event_id,
            granted_at_turn,
            in_game_day,
            json.dumps(bonuses),
            _utc_now(),
        ),
    )
    if title_display or renown_code:
        conn.execute(
            "UPDATE player_characters SET active_title_code = ? WHERE id = ?",
            (renown_code, pc_id),
        )
    if impact_tier >= 4:
        # ADR §5 pinned facts: a tier-4 ("permanent") renown is exactly the
        # kind of world-defining event the GM must never forget or contradict.
        scope_label = scope_id or scope_type
        campaign_facts.pin_fact_conn(
            conn,
            f"Player is known as '{title_display or renown_code}' among {scope_label} — "
            f"a tier-{impact_tier} deed that permanently marked the world.",
            known_by=scope_label,
            source_event_id=source_event_id,
        )
    return {
        "renown_code": renown_code,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "valence": valence,
        "impact_tier": impact_tier,
        "memory_duration": memory_duration,
    }


def grant_renown(
    db_path: str,
    *,
    renown_code: str,
    scope_type: str,
    valence: str = "positive",
    impact_tier: int,
    title_display: str | None = None,
    scope_id: str | None = None,
    source_event_id: int | None = None,
    granted_at_turn: int = 0,
    in_game_day: int | None = None,
) -> dict[str, Any] | None:
    """Engine-only grant hook (validated archivist `op`, quest completion, world
    event) — never a GM-invented tag. Returns None for tier-1 (ephemeral —
    ADR §E2f says these must stay as a plain relationship_event/npc_memory,
    never a renown row)."""
    if not db_path or not os.path.isfile(db_path):
        return None
    conn = _connect(db_path)
    try:
        result = grant_renown_conn(
            conn,
            renown_code=renown_code,
            scope_type=scope_type,
            valence=valence,
            impact_tier=impact_tier,
            title_display=title_display,
            scope_id=scope_id,
            source_event_id=source_event_id,
            granted_at_turn=granted_at_turn,
            in_game_day=in_game_day,
        )
        if result:
            conn.commit()
        return result
    finally:
        conn.close()


def list_renown(db_path: str, *, pc_id: int | None = None) -> list[dict[str, Any]]:
    """Chronological renown log for context-builder/GM prompt use — ADR §E2g:
    conflicting tags (hero, then traitor) both stay, ordered by `granted_at_turn`,
    never merged/overwritten."""
    if not db_path or not os.path.isfile(db_path):
        return []
    conn = _connect(db_path)
    try:
        pid = pc_id or _hero_pc_id(conn)
        if not pid:
            return []
        rows = conn.execute(
            "SELECT * FROM player_renown WHERE player_character_id = ? ORDER BY granted_at_turn ASC, id ASC",
            (pid,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _npc_faction_codes(conn: sqlite3.Connection, npc_id: int) -> list[str]:
    rows = conn.execute("SELECT tag FROM npc_tags WHERE npc_id = ? AND tag LIKE 'faction:%'", (npc_id,)).fetchall()
    return [r["tag"].split(":", 1)[1] for r in rows if ":" in r["tag"]]


def _npc_region(conn: sqlite3.Connection, npc_id: int) -> str | None:
    row = conn.execute(
        "SELECT l.region_name FROM npcs n JOIN locations l ON l.id = n.current_location_id WHERE n.id = ?",
        (npc_id,),
    ).fetchone()
    return row["region_name"] if row and row["region_name"] else None


def apply_renown_on_first_contact(conn: sqlite3.Connection, npc_id: int) -> list[dict[str, Any]]:
    """Call once, right when an NPC's `recognition_level` first moves off
    `stranger` (social/combat engines check this — see `recognition_summary`
    before calling `mark_met_conn`). Applies each matching `renown_reactions`
    row as a one-time bounded trust nudge — never a hexagon change (ADR §E2c:
    hexagon only moves for a *direct* witness/victim/betrayer event, not "heard
    about the hero")."""
    pc_id = _hero_pc_id(conn)
    if not pc_id:
        return []
    renown_rows = conn.execute(
        "SELECT * FROM player_renown WHERE player_character_id = ? ORDER BY granted_at_turn ASC, id ASC",
        (pc_id,),
    ).fetchall()
    if not renown_rows:
        return []

    factions = _npc_faction_codes(conn, npc_id)
    region = _npc_region(conn, npc_id)
    applied: list[dict[str, Any]] = []

    for r in renown_rows:
        # ADR §E2b/§E2d: `renown_reactions` is an authored template *per
        # renown_code*, independent of where the renown was originally earned
        # (`player_renown.scope_type/scope_id`) — a hero_of_amalur tag can have
        # a "faction:rebels -> negative" row even though the renown itself was
        # scoped to "faction:amalur_guard". Match purely on THIS NPC's own
        # faction/region against the reactions table.
        reactions: list[sqlite3.Row] = []
        if factions:
            placeholders = ",".join("?" for _ in factions)
            reactions += conn.execute(
                f"SELECT * FROM renown_reactions WHERE renown_code = ? AND target_type = 'faction' AND target_id IN ({placeholders})",
                (r["renown_code"], *factions),
            ).fetchall()
        if region:
            reactions += conn.execute(
                "SELECT * FROM renown_reactions WHERE renown_code = ? AND target_type = 'region' AND target_id = ?",
                (r["renown_code"], region),
            ).fetchall()

        if not reactions:
            reactions = conn.execute(
                "SELECT * FROM renown_reactions WHERE renown_code = ? AND target_type = 'default_stranger'",
                (r["renown_code"],),
            ).fetchall()

        for reaction in reactions:
            delta = int(reaction["disposition_modifier"] or 0)
            if delta:
                conn.execute(
                    """
                    UPDATE npc_relationships SET trust = MAX(-10, MIN(10, trust + ?)), updated_at = ?
                    WHERE source_npc_id = ? AND target_type = 'player'
                    """,
                    (delta, _utc_now(), npc_id),
                )
            applied.append(
                {
                    "renown_code": r["renown_code"],
                    "reaction": reaction["reaction"],
                    "trust_delta": delta,
                }
            )
    return applied


def set_renown_reaction(
    db_path: str,
    *,
    renown_code: str,
    target_type: str,
    reaction: str,
    target_id: str | None = None,
    disposition_modifier: int = 0,
    notes: str | None = None,
) -> None:
    """Engine-owned template row (wizard/world-building step, not per-NPC)."""
    if target_type not in ("faction", "region", "default_stranger"):
        raise ValueError("target_type must be 'faction', 'region', or 'default_stranger'")
    if reaction not in ("positive", "negative", "indifferent", "wary"):
        raise ValueError("invalid reaction")
    if not db_path or not os.path.isfile(db_path):
        return
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO renown_reactions (renown_code, target_type, target_id, reaction, disposition_modifier, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (renown_code, target_type, target_id, reaction, disposition_modifier, notes),
        )
        conn.commit()
    finally:
        conn.close()
