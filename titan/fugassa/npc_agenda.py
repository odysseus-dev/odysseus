"""Hidden NPC agenda / facade hostility — ADR §B5c.

An NPC can present a friendly `public_disposition` to the player while the
engine (and only the engine + GM secret block) knows a `secret_disposition`
and `agenda_code` underneath. Two layers, one row per NPC:

  public layer (player/UI)     secret layer (engine + GM-only prompt block)
  npc_tags: friendly            secret_disposition: hostile
  disposition: friendly         agenda_code: steal_artifact | betray_at_crossroads | ...
  social resolver: normal       betrayal_trigger: {type, params}

Agenda rows are seeded at NPC-creation time (wizard, quest, generator, or a
validated archivist `create` call) — never invented by the GM mid-chat. This
module only *reveals* what already exists in the DB; it never authors new
secrets from prose.

Reveal paths (ADR §B5c "Odhalení"):
  - Insight/Investigation check (type-A social/search skill check) vs the DC
    encoded in `reveal_condition` (e.g. "insight:15", "investigation:18").
  - Witness event / hard evidence — exposed via `reveal_via_witness` for other
    engine code (quest objectives, scripted world events) to call.
  - `betrayal_trigger` firing on its own (quest_flag / location / turn) —
    checked every turn against the current scene via `evaluate_scene_agendas`.

On reveal: `secret_disposition` becomes the new public truth (`npc_tags`
swapped, `revealed_at_turn` stamped), a `relationship_event: betrayed` trust
hit lands, and — if the secret disposition is `hostile` — `combat_stance` is
bumped to `aggressive` so `combat_engine.evaluate_combat_trigger` picks the
ambush up on the very same turn, with no separate "start fight" code path.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import campaign_facts, world_flags

_CONDITION_RE = re.compile(r"^\s*(insight|investigation)\s*[:=]\s*(\d+)\s*$", re.I)

# ADR §D1-style ladder reused for the post-betrayal attitude label.
_ATTITUDE_LADDER = (
    (-6, "hostile"),
    (-2, "wary"),
    (2, "neutral"),
    (5, "friendly"),
    (999, "ally"),
)

BETRAYAL_TRUST_PENALTY = -5


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _attitude_for_trust(trust: int) -> str:
    for ceiling, label in _ATTITUDE_LADDER:
        if trust <= ceiling:
            return label
    return "ally"


def get_agenda_conn(conn: sqlite3.Connection, npc_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM npc_agenda WHERE npc_id = ?", (npc_id,)).fetchone()
    return dict(row) if row else None


def get_agenda(db_path: str, npc_id: int) -> dict[str, Any] | None:
    if not db_path or not os.path.isfile(db_path):
        return None
    conn = _connect(db_path)
    try:
        return get_agenda_conn(conn, npc_id)
    finally:
        conn.close()


def is_revealed(agenda: dict[str, Any] | None) -> bool:
    return bool(agenda) and agenda.get("revealed_at_turn") is not None


def set_agenda_conn(
    conn: sqlite3.Connection,
    npc_id: int,
    *,
    public_disposition: str = "neutral",
    secret_disposition: str | None = None,
    agenda_code: str | None = None,
    reveal_condition: str | None = None,
    betrayal_trigger: dict[str, Any] | None = None,
) -> None:
    """Seed-time only: wizard, quest, or generator wiring — never called from GM prose."""
    conn.execute(
        """
        INSERT INTO npc_agenda (npc_id, public_disposition, secret_disposition, agenda_code, reveal_condition, betrayal_trigger_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(npc_id) DO UPDATE SET
            public_disposition = excluded.public_disposition,
            secret_disposition = excluded.secret_disposition,
            agenda_code = excluded.agenda_code,
            reveal_condition = excluded.reveal_condition,
            betrayal_trigger_json = excluded.betrayal_trigger_json
        """,
        (
            npc_id,
            public_disposition,
            secret_disposition,
            agenda_code,
            reveal_condition,
            json.dumps(betrayal_trigger, ensure_ascii=False) if betrayal_trigger else None,
            _utc_now(),
        ),
    )


def set_agenda(db_path: str, npc_id: int, **kwargs: Any) -> None:
    conn = _connect(db_path)
    try:
        set_agenda_conn(conn, npc_id, **kwargs)
        conn.commit()
    finally:
        conn.close()


def secret_gm_block_conn(conn: sqlite3.Connection, npc_id: int) -> str | None:
    """GM-only double-game note — never shown to the player, dropped once revealed."""
    agenda = get_agenda_conn(conn, npc_id)
    if not agenda or is_revealed(agenda) or not agenda.get("secret_disposition"):
        return None
    npc = conn.execute("SELECT name FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    name = npc["name"] if npc else f"npc#{npc_id}"
    parts = [
        f"{name} publicly reads as '{agenda['public_disposition']}' but secretly is "
        f"'{agenda['secret_disposition']}' (agenda: {agenda.get('agenda_code') or 'unspecified'})."
    ]
    parts.append(
        "Play the facade in narration — do not let the player perceive this unless they "
        "succeed an Insight/Investigation check, witness hard evidence, or the betrayal fires on its own."
    )
    return " ".join(parts)


def secret_blocks_for_location_conn(conn: sqlite3.Connection, location_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM npcs WHERE current_location_id = ? AND status = 'alive'",
        (location_id,),
    ).fetchall()
    blocks: list[str] = []
    for r in rows:
        block = secret_gm_block_conn(conn, r["id"])
        if block:
            blocks.append(block)
    return blocks


def _swap_public_tag_for_secret(conn: sqlite3.Connection, npc_id: int, agenda: dict[str, Any]) -> None:
    public_tag = agenda.get("public_disposition")
    secret_tag = agenda.get("secret_disposition")
    if public_tag:
        conn.execute("DELETE FROM npc_tags WHERE npc_id = ? AND tag = ?", (npc_id, public_tag))
    if secret_tag:
        conn.execute(
            "INSERT OR IGNORE INTO npc_tags (npc_id, tag, source, created_at) VALUES (?, ?, 'agenda_reveal', ?)",
            (npc_id, secret_tag, _utc_now()),
        )


def reveal_agenda_conn(conn: sqlite3.Connection, npc_id: int, *, turn: int = 0, method: str = "") -> dict[str, Any] | None:
    agenda = get_agenda_conn(conn, npc_id)
    if not agenda or is_revealed(agenda):
        return None

    npc = conn.execute("SELECT id, code, name FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    if not npc:
        return None

    conn.execute("UPDATE npc_agenda SET revealed_at_turn = ? WHERE npc_id = ?", (turn, npc_id))
    _swap_public_tag_for_secret(conn, npc_id, agenda)

    secret = agenda.get("secret_disposition")
    if secret == "hostile":
        conn.execute("UPDATE npcs SET is_hostile = 1, updated_at = ? WHERE id = ?", (_utc_now(), npc_id))
        conn.execute(
            "UPDATE npc_stats SET combat_stance = 'aggressive', updated_at = ? WHERE npc_id = ?",
            (_utc_now(), npc_id),
        )

    rel = conn.execute(
        "SELECT trust FROM npc_relationships WHERE source_npc_id = ? AND target_type = 'player'",
        (npc_id,),
    ).fetchone()
    trust = int(rel["trust"]) if rel else 0
    new_trust = max(-10, trust + BETRAYAL_TRUST_PENALTY)
    new_attitude = _attitude_for_trust(new_trust)
    if rel:
        conn.execute(
            "UPDATE npc_relationships SET trust = ?, attitude = ?, summary = ?, updated_at = ? WHERE source_npc_id = ? AND target_type = 'player'",
            (new_trust, new_attitude, "betrayed", _utc_now(), npc_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO npc_relationships (source_npc_id, target_type, target_id, attitude, trust, summary, met_player, recognition_level, created_at, updated_at)
            VALUES (?, 'player', NULL, ?, ?, 'betrayed', 1, 'personal', ?, ?)
            """,
            (npc_id, new_attitude, new_trust, _utc_now(), _utc_now()),
        )

    # ADR §H8.1 wait_event vocabulary extension — quests can chain off a betrayal reveal.
    world_flags.set_flag_conn(conn, f"npc_betrayed:{npc['code']}")

    # ADR §5 pinned facts — a betrayal reveal permanently redefines the story,
    # never re-hidden even if the GM later forgets the scene it happened in.
    campaign_facts.pin_fact_conn(
        conn,
        f"{npc['name']}'s true colors were revealed ({method or 'trigger'}): secretly "
        f"{secret or 'unspecified'} (agenda: {agenda.get('agenda_code') or 'unspecified'}).",
        known_by=npc["name"],
    )

    return {
        "npc_id": npc_id,
        "npc_name": npc["name"],
        "method": method or "revealed",
        "agenda_code": agenda.get("agenda_code"),
        "secret_disposition": secret,
        "trust": new_trust,
        "attitude": new_attitude,
        "summary": f"{npc['name']}'s true colors are revealed ({method or 'trigger'}) — secretly {secret}, agenda: {agenda.get('agenda_code') or 'unspecified'}.",
    }


def reveal_agenda(db_path: str, npc_id: int, *, turn: int = 0, method: str = "") -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        result = reveal_agenda_conn(conn, npc_id, turn=turn, method=method)
        if result:
            conn.commit()
        return result
    finally:
        conn.close()


def reveal_via_witness(db_path: str, npc_id: int, *, turn: int = 0) -> dict[str, Any] | None:
    """Hook for other engine code (quests, scripted world events) — hard evidence, no roll."""
    return reveal_agenda(db_path, npc_id, turn=turn, method="witness_event")


def attempt_reveal_via_check_conn(
    conn: sqlite3.Connection, npc_id: int, *, skill: str, roll: int, turn: int = 0
) -> dict[str, Any] | None:
    """Type-A skill check reveal: Insight (social) or Investigation (search)."""
    agenda = get_agenda_conn(conn, npc_id)
    if not agenda or is_revealed(agenda):
        return None
    condition = agenda.get("reveal_condition") or ""
    m = _CONDITION_RE.match(condition)
    if not m:
        return None
    cond_skill, dc = m.group(1).lower(), int(m.group(2))
    if cond_skill != skill.lower():
        return None
    if roll < dc:
        return None
    return reveal_agenda_conn(conn, npc_id, turn=turn, method=f"{skill}_check")


def _betrayal_trigger_fires(
    trigger: dict[str, Any], *, turn: int, location_code: str | None, flag_lookup: Any
) -> bool:
    kind = trigger.get("type")
    params = trigger.get("params") or trigger
    if kind == "turn":
        target_turn = params.get("turn")
        return target_turn is not None and turn >= int(target_turn)
    if kind == "location":
        target_code = params.get("location_code") or params.get("code")
        return bool(target_code) and location_code == target_code
    if kind == "quest_flag":
        flag = params.get("flag")
        return bool(flag) and bool(flag_lookup(flag))
    return False


def check_betrayal_trigger_conn(
    conn: sqlite3.Connection, npc_id: int, *, turn: int, location_code: str | None
) -> dict[str, Any] | None:
    agenda = get_agenda_conn(conn, npc_id)
    if not agenda or is_revealed(agenda) or not agenda.get("betrayal_trigger_json"):
        return None
    try:
        trigger = json.loads(agenda["betrayal_trigger_json"])
    except (TypeError, ValueError):
        return None
    if not isinstance(trigger, dict):
        return None
    fires = _betrayal_trigger_fires(
        trigger,
        turn=turn,
        location_code=location_code,
        flag_lookup=lambda key: world_flags.get_flag_conn(conn, key),
    )
    if not fires:
        return None
    return reveal_agenda_conn(conn, npc_id, turn=turn, method="betrayal_trigger")


def evaluate_scene_agendas(db_path: str | None, state: dict[str, Any]) -> dict[str, Any]:
    """Per-turn sweep: fire ready betrayal triggers, collect secret GM notes for the rest.

    Called once per turn from `turn_resolver.resolve_turn`, before the combat
    auto-trigger check — a reveal that flips `combat_stance` to `aggressive`
    is picked up by `combat_engine.evaluate_combat_trigger` in the same turn,
    so an ambush needs no separate "start fight" wiring.
    """
    out: dict[str, Any] = {"revealed": [], "secret_gm_notes": ""}
    if not db_path or not os.path.isfile(db_path):
        return out
    conn = _connect(db_path)
    try:
        loc_row = conn.execute(
            "SELECT current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
        ).fetchone()
        loc_id = int(loc_row["current_location_id"]) if loc_row and loc_row["current_location_id"] else None
        if not loc_id:
            return out
        loc_code_row = conn.execute("SELECT code FROM locations WHERE id = ?", (loc_id,)).fetchone()
        location_code = loc_code_row["code"] if loc_code_row else None
        turn = int(state.get("turn") or 0)

        npc_rows = conn.execute(
            "SELECT id FROM npcs WHERE current_location_id = ? AND status = 'alive'", (loc_id,)
        ).fetchall()
        revealed: list[dict[str, Any]] = []
        for r in npc_rows:
            fired = check_betrayal_trigger_conn(conn, r["id"], turn=turn, location_code=location_code)
            if fired:
                revealed.append(fired)
        if revealed:
            conn.commit()

        notes = secret_blocks_for_location_conn(conn, loc_id)
        out["revealed"] = revealed
        out["secret_gm_notes"] = "\n".join(notes)
        return out
    finally:
        conn.close()
