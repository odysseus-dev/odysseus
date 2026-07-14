"""Map computed 5e character sheets ↔ game.json and SQLite (PC + NPC)."""

from __future__ import annotations

import sqlite3
from typing import Any

from titan.fugassa.dnd5e_character_builder import (
    build,
    draft_to_build_input,
    normalize_cantrip_set,
    normalize_spells_by_level,
)
from titan.fugassa.dnd5e_database import get_dnd5e_database

ABILITY_LONG = {
    "str": "strength",
    "dex": "dexterity",
    "con": "constitution",
    "int": "intelligence",
    "wis": "wisdom",
    "cha": "charisma",
}


def build_sheet_from_draft(draft: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    database = get_dnd5e_database()
    build_input = draft_to_build_input(draft)
    return build(database, build_input), build_input


def _stable_abilities(sheet: dict[str, Any]) -> dict[str, int]:
    raw = sheet.get("abilities") or {}
    out: dict[str, int] = {}
    for short, long_key in ABILITY_LONG.items():
        out[long_key] = int(raw.get(short, raw.get(long_key, 10)))
    return out


def _spellcasting_stable(sheet: dict[str, Any], build_input: dict[str, Any]) -> dict[str, Any] | None:
    sc = sheet.get("spellcasting") or {}
    if not sc.get("has"):
        return None
    cantrips = list(normalize_cantrip_set(build_input.get("selected_cantrips")))
    by_level = normalize_spells_by_level(build_input.get("selected_spells_by_level"))
    spells_known: list[str] = []
    spells_by_level: dict[str, list[str]] = {}
    for lvl in sorted(by_level.keys()):
        ids = list(by_level[lvl].keys())
        spells_by_level[str(lvl)] = ids
        spells_known.extend(ids)
    slots = sc.get("slots_by_level") or {}
    return {
        "ability": sc.get("ability") or "",
        "model": sc.get("model") or "",
        "slots": {str(k): int(v) for k, v in slots.items()},
        "cantrips": cantrips,
        "spells_known": spells_known,
        "spells_by_level": spells_by_level,
        "save_dc": int(sc.get("spell_save_dc") or 0),
        "attack_bonus": int(sc.get("spell_attack_mod") or 0),
        "cantrips_known": int(sc.get("cantrips_known") or 0),
        "spells_prepared_estimate": int(sc.get("spells_prepared_estimate") or -1),
        "spells_known_cap": int(sc.get("spells_known") or -1),
    }


def _features_stable(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in sheet.get("class_features") or []:
        if isinstance(row, dict):
            out.append(
                {
                    "index": str(row.get("index") or ""),
                    "name": str(row.get("name") or row.get("index") or ""),
                    "source": "class",
                    "level": int(row.get("level") or 0),
                }
            )
    for row in sheet.get("subclass_features") or []:
        if isinstance(row, dict):
            out.append(
                {
                    "index": str(row.get("index") or ""),
                    "name": str(row.get("name") or row.get("index") or ""),
                    "source": "subclass",
                    "level": int(row.get("level") or 0),
                }
            )
    return out


def _traits_stable(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in sheet.get("racial_traits") or []:
        if isinstance(row, dict):
            out.append(
                {
                    "index": str(row.get("index") or ""),
                    "name": str(row.get("name") or row.get("index") or ""),
                    "source": "race",
                }
            )
    return out


def _class_resources_stable(sheet: dict[str, Any]) -> dict[str, Any]:
    resources = dict(sheet.get("class_resources") or {})
    selections = sheet.get("class_mechanic_selections") or []
    for sel in selections:
        if not isinstance(sel, dict):
            continue
        sid = str(sel.get("id", ""))
        names = [str(v.get("name", "")) for v in (sel.get("values") or []) if v.get("name")]
        if sid == "infusions" and names:
            resources["infusions"] = names
        elif sid == "invocations" and names:
            resources["invocations"] = names
        elif sid == "metamagic" and names:
            resources["metamagic"] = names
        elif sid == "fighting_style" and names:
            resources["fighting_style"] = names[0]
        elif sid == "favored_enemy" and names:
            resources["favored_enemy"] = names[0]
        elif sid == "favored_terrain" and names:
            resources["favored_terrain"] = names[0]
    return resources


def _class_mechanics_stable(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sel in sheet.get("class_mechanic_selections") or []:
        if not isinstance(sel, dict):
            continue
        values = [
            {"id": str(v.get("id", "")), "name": str(v.get("name", ""))}
            for v in (sel.get("values") or [])
            if isinstance(v, dict) and v.get("name")
        ]
        if values:
            out.append(
                {
                    "id": str(sel.get("id", "")),
                    "label": str(sel.get("label", sel.get("id", ""))),
                    "values": values,
                }
            )
    return out


def _feats_stable(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in sheet.get("feats_picked") or []:
        if isinstance(row, dict):
            out.append(
                {
                    "name": str(row.get("name") or row.get("feat") or ""),
                    "level": int(row.get("level") or 0),
                }
            )
    return out


def _skills_stable(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in sheet.get("skills") or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "id": str(row.get("id") or row.get("index") or ""),
                "name": str(row.get("name") or ""),
                "bonus": int(row.get("modifier") if row.get("modifier") is not None else row.get("bonus") or 0),
                "modifier_str": str(row.get("modifier_str") or ""),
                "proficient": bool(row.get("proficient")),
                "expertise": bool(row.get("expertise")),
            }
        )
    return out


def _saves_stable(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in sheet.get("saving_throws") or []:
        if isinstance(row, dict):
            out.append(
                {
                    "ability": str(row.get("ability") or ""),
                    "modifier": int(row.get("modifier") or 0),
                    "modifier_str": str(row.get("modifier_str") or ""),
                    "proficient": bool(row.get("proficient")),
                }
            )
    return out


def _llm_summaries(sheet: dict[str, Any], build_input: dict[str, Any], *, identity_line: str) -> dict[str, str]:
    sc_block = _spellcasting_stable(sheet, build_input) or {}
    cantrips = sc_block.get("cantrips") or []
    spells = sc_block.get("spells_known") or []
    spell_bits: list[str] = []
    if cantrips:
        spell_bits.append(f"Cantrips: {', '.join(cantrips)}")
    if spells:
        spell_bits.append(f"Spells: {', '.join(spells)}")
    if sc_block.get("save_dc"):
        spell_bits.append(f"Spell DC {sc_block['save_dc']}")
    features = [f.get("name") for f in _features_stable(sheet)[:6] if f.get("name")]
    traits = [t.get("name") for t in _traits_stable(sheet)[:4] if t.get("name")]
    feats = [f.get("name") for f in _feats_stable(sheet) if f.get("name")]
    feature_bits = features + traits + feats
    mech_bits: list[str] = []
    for sel in sheet.get("class_mechanic_selections") or []:
        if not isinstance(sel, dict):
            continue
        names = [str(v.get("name", "")) for v in (sel.get("values") or []) if v.get("name")]
        if names:
            mech_bits.append(f"{sel.get('label', sel.get('id', ''))}: {', '.join(names)}")
    if mech_bits:
        feature_bits.extend(mech_bits)
    return {
        "character_summary_compact": identity_line,
        "spell_summary": "; ".join(spell_bits) if spell_bits else "",
        "feature_summary": ", ".join(feature_bits) if feature_bits else "",
    }


def sheet_to_game_json(
    sheet: dict[str, Any],
    build_input: dict[str, Any],
    *,
    identity: dict[str, Any],
    weapon_name: str,
    armor_name: str,
    loc_name: str,
    hp_current: int,
    inventory_notes: str = "",
) -> dict[str, Any]:
    labels = sheet.get("labels") or {}
    sc = sheet.get("spellcasting") or {}
    slots = sc.get("slots_by_level") or {}
    spellcasting = _spellcasting_stable(sheet, build_input)
    race_class = f"{labels.get('race') or identity.get('race', '')} {labels.get('class') or identity.get('character_class', '')}".strip()
    sub = labels.get("subclass") or identity.get("subclass") or ""
    if sub:
        race_class = f"{race_class} ({sub})"
    identity_line = (
        f"{identity.get('name', 'Hero')} ({race_class}, age {identity.get('age', '')}), "
        f"level {identity.get('level', 1)} — {identity.get('background', '')}"
    )
    return {
        "stable_sheet": {
            "identity": dict(identity),
            "abilities": _stable_abilities(sheet),
            "skills": _skills_stable(sheet),
            "saving_throws": _saves_stable(sheet),
            "features": _features_stable(sheet),
            "traits": _traits_stable(sheet),
            "feats": _feats_stable(sheet),
            "class_resources": _class_resources_stable(sheet),
            "class_mechanics": _class_mechanics_stable(sheet),
            "spellcasting": spellcasting,
            "inventory": {
                "notes": inventory_notes,
                "weapon": weapon_name,
                "armor": armor_name,
            },
        },
        "derived": {
            "proficiency_bonus": int(sheet.get("proficiency_bonus") or 2),
            "passive_perception": int(sheet.get("passive_perception") or 10),
            "ac_base": int(sheet.get("ac_base") or 10),
            "speed": int(sheet.get("speed") or 30),
            "hit_die": int(sheet.get("hit_die") or 8),
            "initiative_bonus": int((sheet.get("ability_modifiers") or {}).get("dex", 0)),
            "spell_save_dc": int(sc.get("spell_save_dc") or 0),
            "spell_attack_bonus": int(sc.get("spell_attack_mod") or 0),
        },
        "volatile_state": {
            "hp_current": int(hp_current),
            "location": loc_name,
            "conditions": [],
            "spell_slots_remaining": {str(k): int(v) for k, v in slots.items()},
        },
        "llm_summary": _llm_summaries(sheet, build_input, identity_line=identity_line),
        "computed": sheet,
    }


def _clear_player_children(conn: sqlite3.Connection, pc_id: int) -> None:
    for table in ("player_skills", "player_feats", "player_features", "player_spells"):
        conn.execute(f"DELETE FROM {table} WHERE player_character_id = ?", (pc_id,))


def apply_player_sheet(
    conn: sqlite3.Connection,
    pc_id: int,
    sheet: dict[str, Any],
    build_input: dict[str, Any],
) -> None:
    """Replace child rows and update derived columns on player_characters."""
    _clear_player_children(conn, pc_id)
    abilities = sheet.get("abilities") or {}
    sc = sheet.get("spellcasting") or {}
    labels = sheet.get("labels") or {}
    dex_mod = int((sheet.get("ability_modifiers") or {}).get("dex", 0))
    conn.execute(
        """
        UPDATE player_characters SET
            subrace = ?,
            proficiency_bonus = ?,
            str_score = ?, dex_score = ?, con_score = ?, int_score = ?, wis_score = ?, cha_score = ?,
            armor_class = COALESCE(armor_class, ?),
            speed_walk = ?,
            passive_perception = ?,
            initiative_bonus = ?,
            spell_save_dc = ?,
            spell_attack_bonus = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (
            labels.get("subrace") or None,
            int(sheet.get("proficiency_bonus") or 2),
            int(abilities.get("str", 10)),
            int(abilities.get("dex", 10)),
            int(abilities.get("con", 10)),
            int(abilities.get("int", 10)),
            int(abilities.get("wis", 10)),
            int(abilities.get("cha", 10)),
            int(sheet.get("ac_base") or 10),
            int(sheet.get("speed") or 30),
            int(sheet.get("passive_perception") or 10),
            dex_mod,
            int(sc.get("spell_save_dc") or 0) or None,
            int(sc.get("spell_attack_mod") or 0) or None,
            pc_id,
        ),
    )
    expertise = build_input.get("expertise") or {}
    for row in sheet.get("skills") or []:
        if not isinstance(row, dict):
            continue
        skill_id = str(row.get("id") or row.get("index") or "").strip()
        if not skill_id:
            continue
        conn.execute(
            """
            INSERT INTO player_skills (player_character_id, skill_id, skill_name, bonus, proficient, expertise)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                pc_id,
                skill_id,
                str(row.get("name") or skill_id),
                int(row.get("modifier") if row.get("modifier") is not None else row.get("bonus") or 0),
                1 if row.get("proficient") else 0,
                1 if expertise.get(skill_id) or row.get("expertise") else 0,
            ),
        )
    for feat in sheet.get("feats_picked") or []:
        if not isinstance(feat, dict):
            continue
        name = str(feat.get("name") or feat.get("feat") or "").strip()
        if not name:
            continue
        conn.execute(
            """
            INSERT INTO player_feats (player_character_id, feat_index, feat_name, level_gained)
            VALUES (?, ?, ?, ?)
            """,
            (pc_id, str(feat.get("index") or ""), name, int(feat.get("level") or 0) or None),
        )
    for row in sheet.get("class_features") or []:
        if isinstance(row, dict):
            _insert_player_feature(conn, pc_id, row, "class")
    for row in sheet.get("subclass_features") or []:
        if isinstance(row, dict):
            _insert_player_feature(conn, pc_id, row, "subclass")
    for row in sheet.get("racial_traits") or []:
        if isinstance(row, dict):
            _insert_player_feature(conn, pc_id, row, "race")
    cantrips = normalize_cantrip_set(build_input.get("selected_cantrips"))
    for spell_id in cantrips:
        conn.execute(
            """
            INSERT INTO player_spells (player_character_id, spell_index, spell_level, is_cantrip)
            VALUES (?, ?, 0, 1)
            """,
            (pc_id, spell_id),
        )
    by_level = normalize_spells_by_level(build_input.get("selected_spells_by_level"))
    for lvl, bucket in by_level.items():
        for spell_id in bucket:
            conn.execute(
                """
                INSERT INTO player_spells (player_character_id, spell_index, spell_level, is_cantrip)
                VALUES (?, ?, ?, 0)
                """,
                (pc_id, spell_id, int(lvl)),
            )


def _insert_player_feature(conn: sqlite3.Connection, pc_id: int, row: dict[str, Any], source: str) -> None:
    index = str(row.get("index") or row.get("name") or "").strip()
    name = str(row.get("name") or index or "").strip()
    if not name:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO player_features (player_character_id, feature_index, feature_name, source, level_gained)
        VALUES (?, ?, ?, ?, ?)
        """,
        (pc_id, index, name, source, int(row.get("level") or 0) or None),
    )


def apply_npc_sheet(
    conn: sqlite3.Connection,
    npc_id: int,
    sheet: dict[str, Any],
    build_input: dict[str, Any],
) -> None:
    conn.execute("DELETE FROM npc_spellbooks WHERE npc_id = ?", (npc_id,))
    conn.execute("DELETE FROM npc_skills WHERE npc_id = ?", (npc_id,))
    abilities = sheet.get("abilities") or {}
    sc = sheet.get("spellcasting") or {}
    dex_mod = int((sheet.get("ability_modifiers") or {}).get("dex", 0))
    conn.execute(
        """
        UPDATE npc_stats SET
            armor_class = ?,
            hit_points_current = ?,
            hit_points_max = ?,
            speed_walk = ?,
            str_score = ?, dex_score = ?, con_score = ?, int_score = ?, wis_score = ?, cha_score = ?,
            passive_perception = ?,
            initiative_bonus = ?,
            attack_bonus = ?,
            spell_save_dc = ?,
            spell_attack_bonus = ?,
            updated_at = datetime('now')
        WHERE npc_id = ?
        """,
        (
            int(sheet.get("ac_base") or 10),
            int(sheet.get("hp") or 10),
            int(sheet.get("hp") or 10),
            int(sheet.get("speed") or 30),
            int(abilities.get("str", 10)),
            int(abilities.get("dex", 10)),
            int(abilities.get("con", 10)),
            int(abilities.get("int", 10)),
            int(abilities.get("wis", 10)),
            int(abilities.get("cha", 10)),
            int(sheet.get("passive_perception") or 10),
            dex_mod,
            int(sc.get("spell_attack_mod") or 0) or 2,
            int(sc.get("spell_save_dc") or 0) or None,
            int(sc.get("spell_attack_mod") or 0) or None,
            npc_id,
        ),
    )
    for row in sheet.get("skills") or []:
        if not isinstance(row, dict) or not row.get("proficient"):
            continue
        name = str(row.get("name") or row.get("id") or "").strip()
        if not name:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO npc_skills (npc_id, skill_name, bonus) VALUES (?, ?, ?)",
            (npc_id, name, int(row.get("modifier") if row.get("modifier") is not None else row.get("bonus") or 0)),
        )
    cantrips = normalize_cantrip_set(build_input.get("selected_cantrips"))
    for spell_id in cantrips:
        conn.execute(
            "INSERT INTO npc_spellbooks (npc_id, spell_index, spell_level, is_cantrip) VALUES (?, ?, 0, 1)",
            (npc_id, spell_id),
        )
    by_level = normalize_spells_by_level(build_input.get("selected_spells_by_level"))
    for lvl, bucket in by_level.items():
        for spell_id in bucket:
            conn.execute(
                "INSERT INTO npc_spellbooks (npc_id, spell_index, spell_level, is_cantrip) VALUES (?, ?, ?, 0)",
                (npc_id, spell_id, int(lvl)),
            )


def enrich_character_sheet_from_sql(conn: sqlite3.Connection, pc_id: int, state: dict[str, Any]) -> dict[str, Any]:
    """Merge SQL child tables into runtime character_sheet (dual-read ADR M2)."""
    cs = dict(state.get("character_sheet") or {})
    stable = dict(cs.get("stable_sheet") or {})
    skill_rows = conn.execute(
        """
        SELECT skill_id, skill_name, bonus, proficient, expertise
        FROM player_skills WHERE player_character_id = ? ORDER BY skill_name
        """,
        (pc_id,),
    ).fetchall()
    if skill_rows:
        stable["skills"] = [
            {
                "id": r["skill_id"],
                "name": r["skill_name"],
                "bonus": int(r["bonus"]),
                "modifier": int(r["bonus"]),
                "modifier_str": (
                    f"+{int(r['bonus'])}" if int(r["bonus"]) >= 0 else str(int(r["bonus"]))
                ),
                "proficient": bool(r["proficient"]),
                "expertise": bool(r["expertise"]),
            }
            for r in skill_rows
        ]
    spell_rows = conn.execute(
        """
        SELECT spell_index, spell_level, is_cantrip
        FROM player_spells WHERE player_character_id = ? ORDER BY is_cantrip DESC, spell_level, spell_index
        """,
        (pc_id,),
    ).fetchall()
    if spell_rows:
        sc = dict(stable.get("spellcasting") or {})
        sc["cantrips"] = [r["spell_index"] for r in spell_rows if r["is_cantrip"]]
        by_level: dict[str, list[str]] = {}
        known: list[str] = []
        for r in spell_rows:
            if r["is_cantrip"]:
                continue
            lvl = str(int(r["spell_level"]))
            by_level.setdefault(lvl, []).append(r["spell_index"])
            known.append(r["spell_index"])
        sc["spells_known"] = known
        sc["spells_by_level"] = by_level
        stable["spellcasting"] = sc
    feat_rows = conn.execute(
        "SELECT feat_name, level_gained FROM player_feats WHERE player_character_id = ?",
        (pc_id,),
    ).fetchall()
    if feat_rows:
        stable["feats"] = [{"name": r["feat_name"], "level": r["level_gained"]} for r in feat_rows]
    feature_rows = conn.execute(
        "SELECT feature_index, feature_name, source, level_gained FROM player_features WHERE player_character_id = ?",
        (pc_id,),
    ).fetchall()
    if feature_rows:
        stable["features"] = [
            {
                "index": r["feature_index"],
                "name": r["feature_name"],
                "source": r["source"],
                "level": r["level_gained"],
            }
            for r in feature_rows
        ]
    cs["stable_sheet"] = stable
    state["character_sheet"] = cs
    return state


def npc_spell_summary(conn: sqlite3.Connection, npc_id: int, *, limit: int = 8) -> str:
    rows = conn.execute(
        """
        SELECT spell_index, is_cantrip FROM npc_spellbooks
        WHERE npc_id = ? ORDER BY is_cantrip DESC, spell_level, spell_index LIMIT ?
        """,
        (npc_id, limit),
    ).fetchall()
    if not rows:
        return ""
    return ", ".join(r["spell_index"] for r in rows)


__all__ = [
    "apply_npc_sheet",
    "apply_player_sheet",
    "build_sheet_from_draft",
    "enrich_character_sheet_from_sql",
    "npc_spell_summary",
    "sheet_to_game_json",
]
