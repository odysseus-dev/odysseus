"""Combat engine — ADR §B5 stance triggers + deterministic attack resolution (M5).

Combat state (HP, death, initiative) is engine/DB truth; GM only narrates the
`turn_resolution.combat` block it receives — never invents damage or death.
"""

from __future__ import annotations

import logging
import random
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import npc_knowledge, renown_engine, world_flags

LOG = logging.getLogger("titan.fugassa.combat_engine")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _roll_dice(dice_str: str) -> int:
    m = re.match(r"\s*(\d+)\s*d\s*(\d+)\s*", dice_str or "1d6")
    if not m:
        return random.randint(1, 6)
    n, sides = int(m.group(1)), int(m.group(2))
    return sum(random.randint(1, sides) for _ in range(max(1, n)))


def get_current_location_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
    return int(row["current_location_id"]) if row and row["current_location_id"] else None


def list_hostile_npcs_at_location(conn: sqlite3.Connection, location_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT n.id AS npc_id, n.name, n.is_hostile, ns.combat_stance, ns.hit_points_current
        FROM npcs n
        LEFT JOIN npc_stats ns ON ns.npc_id = n.id
        WHERE n.current_location_id = ? AND n.status = 'alive' AND n.is_hostile = 1
        """,
        (location_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def evaluate_combat_trigger(db_path: str, state: dict[str, Any]) -> dict[str, Any] | None:
    """ADR B5c: engine-only auto-initiation — aggressive stance encounter.

    Social/ambush/betrayal triggers stay narrative (GM + archivist agenda);
    this covers the deterministic "monster/aggressive NPC on sight" case so
    combat never starts purely from GM prose.
    """
    if not db_path or bool(state.get("in_combat")):
        return None
    import os

    if not os.path.isfile(db_path):
        return None
    conn = _connect(db_path)
    try:
        loc_id = get_current_location_id(conn)
        if not loc_id:
            return None
        hostiles = [h for h in list_hostile_npcs_at_location(conn, loc_id) if h.get("hit_points_current", 1) > 0]
        aggressive = [h for h in hostiles if (h.get("combat_stance") or "wary") == "aggressive"]
        if not aggressive:
            return None
        return {
            "initiated": True,
            "reason": "aggressive_stance",
            "participants": [h["name"] for h in aggressive],
        }
    finally:
        conn.close()


def start_combat(db_path: str, state: dict[str, Any]) -> dict[str, Any]:
    party = state.get("party") or []
    names: list[tuple[str, int]] = []
    for member in party:
        if not isinstance(member, dict):
            continue
        init = random.randint(1, 20) + int(member.get("initiative_bonus", 0) or 0)
        names.append((str(member.get("name") or "Hero"), init))

    hostiles: list[dict[str, Any]] = []
    if db_path:
        import os

        if os.path.isfile(db_path):
            conn = _connect(db_path)
            try:
                loc_id = get_current_location_id(conn)
                if loc_id:
                    rows = conn.execute(
                        """
                        SELECT n.id AS npc_id, n.name, ns.initiative_bonus
                        FROM npcs n
                        LEFT JOIN npc_stats ns ON ns.npc_id = n.id
                        WHERE n.current_location_id = ? AND n.status = 'alive' AND n.is_hostile = 1
                        """,
                        (loc_id,),
                    ).fetchall()
                    hostiles = [dict(r) for r in rows]
            finally:
                conn.close()

    if not hostiles:
        loc = state.get("location_state") or {}
        enemy_names = list(loc.get("enemies") or []) or ["Hostile presence"]
        hostiles = [{"npc_id": None, "name": n, "initiative_bonus": 0} for n in enemy_names]

    for h in hostiles:
        init = random.randint(1, 20) + int(h.get("initiative_bonus", 0) or 0)
        names.append((str(h.get("name")), init))

    names.sort(key=lambda t: t[1], reverse=True)
    order = [f"{n} ({i})" for n, i in names]

    loc = dict(state.get("location_state") or {})
    loc["enemies"] = [h["name"] for h in hostiles]
    state["location_state"] = loc
    state["in_combat"] = True
    state["initiative_order"] = order
    state["combat_participants"] = {h["name"]: h.get("npc_id") for h in hostiles}
    return {"order": order, "hostiles": [h["name"] for h in hostiles]}


def _find_npc_by_name(conn: sqlite3.Connection, location_id: int, name_hint: str) -> sqlite3.Row | None:
    rows = conn.execute(
        """
        SELECT n.id, n.code, n.name, ns.armor_class, ns.hit_points_current, ns.hit_points_max, ns.attack_bonus, ns.damage_dice
        FROM npcs n
        LEFT JOIN npc_stats ns ON ns.npc_id = n.id
        WHERE n.current_location_id = ? AND n.status = 'alive' AND n.is_hostile = 1
        """,
        (location_id,),
    ).fetchall()
    if not rows:
        return None
    hint = (name_hint or "").lower()
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


def _ability_modifier(score: int) -> int:
    return (int(score) - 10) // 2


def _player_attack_profile(conn: sqlite3.Connection, hero: dict[str, Any]) -> tuple[int, str]:
    """Attack bonus + damage dice from the actual sheet, not a magic constant.

    No structured weapon stats are persisted anywhere yet (wizard gear is a
    free-text name only — see `game_bootstrap.apply_wizard_draft`), so damage
    dice still falls back to a class-agnostic default; but attack bonus is
    real: proficiency + STR modifier (5e default for a melee weapon), sourced
    from `player_characters`, not `hero.get("attack_bonus", 4)` which is a key
    that's never actually populated anywhere in game state.
    """
    row = conn.execute(
        "SELECT proficiency_bonus, str_score, dex_score FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
    ).fetchone()
    if row:
        prof = int(row["proficiency_bonus"] or 2)
        str_mod = _ability_modifier(row["str_score"] or 10)
        dex_mod = _ability_modifier(row["dex_score"] or 10)
        attack_bonus = prof + max(str_mod, dex_mod)
    else:
        attack_bonus = int(hero.get("attack_bonus", 4) or 4)
    dice = str(hero.get("damage_dice") or "1d8")
    return attack_bonus, dice


def resolve_player_attack(db_path: str, state: dict[str, Any], player_text: str) -> dict[str, Any]:
    """Resolve one player combat action against a hostile NPC at the current location."""
    party = state.get("party") or []
    hero = party[0] if party and isinstance(party[0], dict) else {}

    import os

    if not db_path or not os.path.isfile(db_path):
        attack_bonus = int(hero.get("attack_bonus", 4) or 4)
        roll = random.randint(1, 20)
        return {
            "in_combat": True,
            "attack_roll": roll,
            "attack_bonus": attack_bonus,
            "summary": f"Attack roll d20={roll}+{attack_bonus} (no target data)",
        }

    conn = _connect(db_path)
    try:
        attack_bonus, hero_dice = _player_attack_profile(conn, hero)
        result: dict[str, Any] = {
            "in_combat": True,
            "attack_roll": random.randint(1, 20),
            "attack_bonus": attack_bonus,
        }
        loc_id = get_current_location_id(conn)
        target = _find_npc_by_name(conn, loc_id, player_text) if loc_id else None
        if not target:
            result["summary"] = f"Attack roll d20={result['attack_roll']}+{attack_bonus} (no target present)"
            return result

        # ADR §E: trading blows is a personal encounter — the NPC now knows the
        # player's face regardless of hit/miss/outcome.
        was_stranger = npc_knowledge.recognition_summary(conn, target["id"])["recognition_level"] == "stranger"
        npc_knowledge.mark_met_conn(conn, target["id"], source="witness")
        if was_stranger:
            renown_engine.apply_renown_on_first_contact(conn, target["id"])

        total = result["attack_roll"] + attack_bonus
        ac = int(target["armor_class"] or 10)
        crit = result["attack_roll"] == 20
        hit = crit or (result["attack_roll"] != 1 and total >= ac)
        result.update({"target": target["name"], "target_ac": ac, "hit": hit, "crit": crit})

        if hit:
            dmg = _roll_dice(hero_dice) * (2 if crit else 1)
            hp_current = max(0, int(target["hit_points_current"] or 0) - dmg)
            conn.execute(
                "UPDATE npc_stats SET hit_points_current = ?, updated_at = ? WHERE npc_id = ?",
                (hp_current, _utc_now(), target["id"]),
            )
            result["damage"] = dmg
            result["target_hp_current"] = hp_current
            result["target_hp_max"] = int(target["hit_points_max"] or dmg)
            if hp_current <= 0:
                conn.execute(
                    "UPDATE npcs SET status = 'dead', updated_at = ? WHERE id = ?",
                    (_utc_now(), target["id"]),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO npc_tags (npc_id, tag, source, created_at) VALUES (?, 'dead', 'combat', ?)",
                    (target["id"], _utc_now()),
                )
                # ADR §H8.1 wait_event hook: kills are a first-class engine event a
                # quest can chain off (target_code = "npc_dead:<npc_code>"), no GM/
                # archivist involvement.
                world_flags.set_flag_conn(conn, f"npc_dead:{target['code']}")
                result["target_dead"] = True
                loc = dict(state.get("location_state") or {})
                loc["enemies"] = [e for e in (loc.get("enemies") or []) if e != target["name"]]
                state["location_state"] = loc
                if not loc["enemies"]:
                    state["in_combat"] = False
                    state["initiative_order"] = []
            result["summary"] = (
                f"Attack d20={result['attack_roll']}+{attack_bonus}={total} vs AC{ac}: "
                f"{'CRIT ' if crit else ''}HIT, {dmg} dmg → {target['name']} at {hp_current}/{result['target_hp_max']} HP"
                + (" (defeated)" if result.get("target_dead") else "")
            )
        else:
            result["summary"] = f"Attack d20={result['attack_roll']}+{attack_bonus}={total} vs AC{ac}: MISS"
        conn.commit()
        return result
    finally:
        conn.close()


def resolve_npc_counterattacks(db_path: str, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Alive hostiles at the location strike back at the hero (simple round-robin, no manual targeting)."""
    if not state.get("in_combat"):
        return []
    party = state.get("party") or []
    hero = party[0] if party and isinstance(party[0], dict) else {}
    hero_ac = int(hero.get("ac", 12) or 12)
    hero_hp = int(hero.get("hp", 100) or 100)

    import os

    if not db_path or not os.path.isfile(db_path):
        return []
    conn = _connect(db_path)
    out: list[dict[str, Any]] = []
    try:
        loc_id = get_current_location_id(conn)
        if not loc_id:
            return []
        rows = conn.execute(
            """
            SELECT n.id, n.name, ns.attack_bonus, ns.damage_dice
            FROM npcs n JOIN npc_stats ns ON ns.npc_id = n.id
            WHERE n.current_location_id = ? AND n.status = 'alive' AND n.is_hostile = 1
            """,
            (loc_id,),
        ).fetchall()
        for r in rows:
            roll = random.randint(1, 20)
            atk_bonus = int(r["attack_bonus"] or 2)
            total = roll + atk_bonus
            hit = roll != 1 and (roll == 20 or total >= hero_ac)
            entry = {"attacker": r["name"], "roll": roll, "hit": hit}
            if hit:
                dmg = _roll_dice(r["damage_dice"] or "1d6")
                hero_hp = max(0, hero_hp - dmg)
                entry["damage"] = dmg
                entry["summary"] = f"{r['name']} hits for {dmg} dmg (d20={roll}+{atk_bonus} vs AC{hero_ac})"
            else:
                entry["summary"] = f"{r['name']} misses (d20={roll}+{atk_bonus} vs AC{hero_ac})"
            out.append(entry)
        if party and isinstance(party[0], dict):
            party = list(party)
            hero2 = dict(party[0])
            hero2["hp"] = hero_hp
            party[0] = hero2
            state["party"] = party
        if hero_hp <= 0:
            state["in_combat"] = False
        return out
    finally:
        conn.close()
