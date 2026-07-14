"""Social resolver — ADR §E: deterministic skill check + relationship delta.

DC and outcome cap derive from the target NPC's hexagon + current trust, not
the GM's prose. GM narrates the roll result; it must not invent a different
relationship shift.
"""

from __future__ import annotations

import os
import random
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import npc_agenda, npc_knowledge, quest_engine, renown_engine

_RENOUNCE_RE = re.compile(
    r"\b(give up|giving up|renounce|abandon|forget)\b.{0,25}\bquest\b"
    r"|\bcan'?t\s+(?:do|finish|complete)\s+(?:this|the)\s+quest\b"
    r"|\bi\s+quit\s+(?:this|the)\s+quest\b",
    re.I,
)
_NEGOTIATE_RE = re.compile(
    r"\b(more (?:gold|coin|money|pay|reward)|higher pay|extra reward|bigger reward|deserve more|"
    r"double (?:the )?(?:pay|reward)|ask for more|demand\s+\d+|demand\s+(?:more|extra))\b",
    re.I,
)

ATTITUDE_LADDER = (
    (-6, "hostile"),
    (-2, "wary"),
    (2, "neutral"),
    (5, "friendly"),
    (999, "ally"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _attitude_for_trust(trust: int) -> str:
    for ceiling, label in ATTITUDE_LADDER:
        if trust <= ceiling:
            return label
    return "ally"


def _find_target_npc(conn: sqlite3.Connection, location_id: int, player_text: str) -> sqlite3.Row | None:
    rows = conn.execute(
        "SELECT id, name FROM npcs WHERE current_location_id = ? AND status = 'alive'",
        (location_id,),
    ).fetchall()
    if not rows:
        return None
    hint = (player_text or "").lower()
    best: tuple[int, sqlite3.Row] | None = None
    for r in rows:
        name = (r["name"] or "").lower()
        if not name:
            continue
        if name in hint and (best is None or len(name) > best[0]):
            best = (len(name), r)
            continue
        for token in name.split():
            if len(token) >= 3 and token in hint and (best is None or len(token) > best[0]):
                best = (len(token), r)
    return best[1] if best else rows[0]


def resolve_social(db_path: str, state: dict[str, Any], player_text: str) -> dict[str, Any]:
    """Roll a persuasion-style check against a hexagon+trust derived DC."""
    roll = random.randint(1, 20)
    result: dict[str, Any] = {"skill": "persuasion", "roll": roll}

    if not db_path or not os.path.isfile(db_path):
        dc = 13
        success = roll >= dc
        result.update(
            {
                "dc": dc,
                "success": success,
                "relationship_delta": 2 if success else (-1 if roll == 1 else 0),
                "summary": f"Social d20={roll} vs DC {dc}: {'success' if success else 'failure'}",
            }
        )
        return result

    conn = _connect(db_path)
    try:
        loc_row = conn.execute(
            "SELECT current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
        ).fetchone()
        loc_id = int(loc_row["current_location_id"]) if loc_row and loc_row["current_location_id"] else None
        npc = _find_target_npc(conn, loc_id, player_text) if loc_id else None
        if not npc:
            dc = 13
            success = roll >= dc
            result.update(
                {
                    "dc": dc,
                    "success": success,
                    "relationship_delta": 0,
                    "summary": f"Social d20={roll} vs DC {dc}: {'success' if success else 'failure'} (no NPC present)",
                }
            )
            return result

        # ADR §E: any dialog attempt is a personal encounter — the NPC now
        # recognizes the player's face regardless of how the conversation goes.
        was_stranger = npc_knowledge.recognition_summary(conn, npc["id"])["recognition_level"] == "stranger"
        npc_knowledge.mark_met_conn(conn, npc["id"], source="witness")
        if was_stranger:
            # ADR §E2d: first contact is where group-scale renown (not personal
            # hexagon) colors the NPC's starting disposition.
            renown_engine.apply_renown_on_first_contact(conn, npc["id"])
        conn.commit()

        # ADR §H8.3/§H8.6: quest renounce/negotiation ride on ordinary dialog with
        # the quest giver — no meta "abandon" button, no separate negotiation UI.
        if _RENOUNCE_RE.search(player_text or ""):
            renounced = quest_engine.renounce_quest(db_path, state, npc["id"])
            if renounced:
                result.update(
                    {
                        "quest_renounced": renounced,
                        "success": True,
                        "relationship_delta": 0,
                        "summary": f"You renounce the quest '{renounced['quest']}' to {npc['name']}.",
                    }
                )
                return result
        if _NEGOTIATE_RE.search(player_text or ""):
            negotiation = quest_engine.negotiate_reward(db_path, state, npc["id"], player_text, roll)
            if negotiation:
                result.update(
                    {
                        "quest_negotiation": negotiation,
                        "success": negotiation.get("outcome") in ("success", "crit_success"),
                        "relationship_delta": negotiation.get("trust_delta", 0),
                        "npc_name": npc["name"],
                        "dc": negotiation.get("dc"),
                        "summary": negotiation.get("summary", ""),
                    }
                )
                return result

        # ADR §B5c: Insight rides on the ordinary dialog roll — no separate
        # "read this NPC" action needed. Reveal happens before the persuasion
        # outcome is computed so a successful read can color the rest.
        turn = int(state.get("turn") or 0)
        agenda_reveal = npc_agenda.attempt_reveal_via_check_conn(conn, npc["id"], skill="insight", roll=roll, turn=turn)
        if agenda_reveal:
            conn.commit()
            result["agenda_revealed"] = agenda_reveal

        hexagon = conn.execute("SELECT * FROM npc_personality_hex WHERE npc_id = ?", (npc["id"],)).fetchone()
        rel = conn.execute(
            "SELECT trust, attitude FROM npc_relationships WHERE source_npc_id = ? AND target_type = 'player'",
            (npc["id"],),
        ).fetchone()
        trust = int(rel["trust"]) if rel else 0
        kindness = int(hexagon["kindness"]) if hexagon else 0
        composure = int(hexagon["composure"]) if hexagon else 0

        # ADR §E: harder to sway a guarded/unkind NPC; existing trust lowers DC (inertia toward warmth)
        dc = 13 - min(4, max(0, trust)) + max(0, -kindness) + max(0, composure - 1)
        dc = max(5, min(20, dc))
        crit_success = roll == 20
        crit_fail = roll == 1
        success = crit_success or (not crit_fail and roll >= dc)

        # ADR §D1: normal pass/fail is capped by relationship inertia (+1 / 0);
        # only a crit breaks the per-scene cap, and even then by a bounded
        # amount — +3/-3 here previously let a single crit roll blow past the
        # "hostile -> ally requires an arc" rule in one shot.
        if crit_success:
            delta = 2
        elif crit_fail:
            delta = -1
        elif success:
            delta = 1
        else:
            delta = 0

        new_trust = max(-10, min(10, trust + delta))
        new_attitude = _attitude_for_trust(new_trust)
        if rel:
            conn.execute(
                "UPDATE npc_relationships SET trust = ?, attitude = ?, updated_at = ? WHERE source_npc_id = ? AND target_type = 'player'",
                (new_trust, new_attitude, _utc_now(), npc["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO npc_relationships (source_npc_id, target_type, target_id, attitude, trust, created_at, updated_at)
                VALUES (?, 'player', NULL, ?, ?, ?, ?)
                """,
                (npc["id"], new_attitude, new_trust, _utc_now(), _utc_now()),
            )
        if new_attitude == "hostile":
            conn.execute(
                "INSERT OR IGNORE INTO npc_tags (npc_id, tag, source, created_at) VALUES (?, 'hostile', 'social', ?)",
                (npc["id"], _utc_now()),
            )
        npc_knowledge.upgrade_to_personal_if_trusted(conn, npc["id"])
        conn.commit()

        result.update(
            {
                "npc_name": npc["name"],
                "dc": dc,
                "success": success,
                "crit_success": crit_success,
                "crit_fail": crit_fail,
                "relationship_delta": delta,
                "trust": new_trust,
                "attitude": new_attitude,
                "summary": (
                    f"Social d20={roll} vs DC {dc} ({npc['name']}): "
                    f"{'crit success' if crit_success else 'crit fail' if crit_fail else 'success' if success else 'failure'}"
                    f" — trust {trust:+d} → {new_trust:+d} ({new_attitude})"
                ),
            }
        )
        return result
    finally:
        conn.close()
