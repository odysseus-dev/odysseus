"""NPC generator — ADR §B4 spawn packages (T0–T3).

Engine computes stats/level from tier + CR; LLM/GM only supplies identity
(name, race, backstory) via the caller. T3 gets a fixed CR — never scaled.
"""

from __future__ import annotations

import hashlib
import random
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import npc_agenda
from titan.fugassa.dnd5e_character_builder import build, spell_budgets
from titan.fugassa.dnd5e_database import get_dnd5e_database
from titan.fugassa.sheet_persistence import apply_npc_sheet

HEX_AXES = ("kindness", "empathy", "wit", "drive", "boldness", "composure")
ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")

_CLASS_STAPLE_SPELLS: dict[str, list[str]] = {
    "wizard": ["fire-bolt", "light", "mage-hand", "prestidigitation", "magic-missile", "shield", "detect-magic", "sleep"],
    "cleric": ["sacred-flame", "guidance", "light", "cure-wounds", "bless", "healing-word", "sanctuary"],
    "druid": ["produce-flame", "shillelagh", "entangle", "healing-word", "faerie-fire"],
    "bard": ["vicious-mockery", "prestidigitation", "healing-word", "disguise-self", "sleep"],
    "sorcerer": ["fire-bolt", "light", "ray-of-frost", "magic-missile", "shield", "chromatic-orb"],
    "warlock": ["eldritch-blast", "mage-hand", "hex", "armor-of-agathys", "hellish-rebuke"],
    "ranger": ["hunters-mark", "cure-wounds", "goodberry", "entangle"],
    "paladin": ["bless", "cure-wounds", "shield-of-faith", "command"],
}

# ADR B5: monster preset bands by keyword — bestiary lite, avoids LLM math for T0/T1
_MONSTER_PRESETS: dict[str, dict[str, Any]] = {
    "wolf": {"cr": 0.25, "combat_stance": "aggressive", "damage_dice": "2d4", "tags": ["monster", "predator"]},
    "bandit": {"cr": 0.5, "combat_stance": "wary", "damage_dice": "1d6", "tags": ["monster", "hostile"]},
    "goblin": {"cr": 0.25, "combat_stance": "wary", "damage_dice": "1d6", "tags": ["monster", "hostile"]},
    "cultist": {"cr": 0.5, "combat_stance": "wary", "damage_dice": "1d8", "tags": ["monster", "hostile"]},
    "bear": {"cr": 1.0, "combat_stance": "aggressive", "damage_dice": "2d6", "tags": ["monster", "predator"]},
    "bandit captain": {"cr": 2.0, "combat_stance": "aggressive", "damage_dice": "2d8", "tags": ["monster", "hostile", "boss"]},
}

TIER_CR_DEFAULT = {"T0": 0.0, "T1": 0.25, "T2": 0.5, "T3": 2.0}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(name: str, fallback: str = "npc") -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return (base[:40] or fallback)


def _cr_to_npc_level(cr: float, tier: str) -> int:
    if tier == "T3":
        return max(5, min(20, int(round(float(cr) * 3)) + 3))
    return max(1, min(20, int(round(float(cr) * 4)) + 1))


def _should_build_full_sheet(*, tier: str, preset: dict[str, Any] | None, race: str | None, class_role: str | None) -> bool:
    if preset or tier not in ("T2", "T3"):
        return False
    return bool(str(race or "").strip() and str(class_role or "").strip())


def _auto_select_npc_spells(
    db,
    sheet: dict[str, Any],
    build_input: dict[str, Any],
    seed_key: str,
) -> tuple[list[str], dict[int, list[str]]]:
    sc = sheet.get("spellcasting") or {}
    if not sc.get("has"):
        return [], {}
    budgets = spell_budgets(sc)
    cant_cap = int(budgets.get("cantrip_max") or 0)
    spell_cap = int(budgets.get("leveled_cap") or 0)
    max_lvl = int(budgets.get("max_spell_level") or 0)
    class_id = str(sheet.get("spell_list_class_id") or (sheet.get("resolved") or {}).get("class_id") or "")
    rng = _seeded_rng(seed_key)
    staples = list(_CLASS_STAPLE_SPELLS.get(class_id, []))
    cantrip_pool = [s["index"] for s in db.list_spells_for(class_id, 0) if s.get("index")]
    if staples:
        cantrip_pool = staples + [c for c in cantrip_pool if c not in staples]
    cantrips: list[str] = []
    for spell_id in cantrip_pool:
        if len(cantrips) >= cant_cap:
            break
        cantrips.append(spell_id)
    spells_by_level: dict[int, list[str]] = {}
    picked = 0
    for lvl in range(1, max_lvl + 1):
        if picked >= spell_cap:
            break
        pool = [s["index"] for s in db.list_spells_for(class_id, lvl) if s.get("index")]
        if staples:
            pool = [p for p in staples if p in pool] + [p for p in pool if p not in staples]
        rng.shuffle(pool)
        bucket: list[str] = []
        for spell_id in pool:
            if picked >= spell_cap:
                break
            bucket.append(spell_id)
            picked += 1
        if bucket:
            spells_by_level[lvl] = bucket
    return cantrips, spells_by_level


def build_npc_sheet(
    *,
    race: str,
    class_role: str,
    tier: str,
    cr: float,
    npc_code: str,
    level: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    db = get_dnd5e_database()
    lvl = level if level is not None else _cr_to_npc_level(cr, tier)
    rng = _seeded_rng(f"{npc_code}:abilities")
    abilities = {key: max(8, min(18, 10 + rng.randint(-2, 3))) for key in ABILITY_KEYS}
    build_input: dict[str, Any] = {
        "class_label": class_role,
        "race_label": race,
        "level": lvl,
        "abilities_pre_race": abilities,
        "skill_proficiencies": {},
        "expertise": {},
        "selected_cantrips": [],
        "selected_spells_by_level": {},
        "asi_choices": {},
        "homebrew_details": {},
        "spell_list_class_id": "",
        "rules_mode": "5e-style",
        "playstyle_framework": "rules_based",
    }
    preview = build(db, build_input)
    if not build_input.get("skill_proficiencies"):
        class_id = str((preview.get("resolved") or {}).get("class_id") or "")
        cls = db.get_class_data(class_id) if class_id else {}
        blocks = cls.get("proficiency_choices") or []
        block0 = blocks[0] if blocks and isinstance(blocks[0], dict) else {}
        options: list[str] = []
        for opt in (block0.get("from") or {}).get("options") or []:
            if not isinstance(opt, dict):
                continue
            idx = str((opt.get("item") or {}).get("index") or "")
            if idx.startswith("skill-"):
                options.append(idx.replace("skill-", "", 1))
        if not options:
            options = ["perception", "insight", "persuasion", "stealth", "investigation", "arcana"]
        rng.shuffle(options)
        cap = max(1, min(int(block0.get("choose") or 2), int(preview.get("skill_proficiency_cap") or 2)))
        build_input["skill_proficiencies"] = {opt: True for opt in options[:cap]}
        preview = build(db, build_input)
    cantrips, spells_by_level = _auto_select_npc_spells(db, preview, build_input, f"{npc_code}:spells")
    build_input["selected_cantrips"] = cantrips
    build_input["selected_spells_by_level"] = {str(k): v for k, v in spells_by_level.items()}
    sheet = build(db, build_input)
    return sheet, build_input


def _seeded_rng(seed_key: str) -> random.Random:
    h = hashlib.sha256(seed_key.encode("utf-8")).hexdigest()
    return random.Random(int(h[:16], 16))


def _match_preset(name: str, class_role: str | None) -> dict[str, Any] | None:
    haystack = f"{name} {class_role or ''}".lower()
    best: tuple[int, dict[str, Any]] | None = None
    for key, preset in _MONSTER_PRESETS.items():
        if key in haystack and (best is None or len(key) > best[0]):
            best = (len(key), preset)
    return best[1] if best else None


def _roll_hexagon(rng: random.Random, bias: dict[str, int] | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for ax in HEX_AXES:
        base = rng.randint(-2, 2)
        if bias and ax in bias:
            base = max(-3, min(3, base + int(bias[ax])))
        out[ax] = base
    return out


def _cr_to_stats(cr: float, rng: random.Random, *, damage_dice: str | None = None) -> dict[str, Any]:
    hp = max(4, int(round(cr * 16)) + rng.randint(0, 6))
    ac = 10 + min(8, int(round(cr * 2)))
    atk = 2 + int(round(cr))
    dice = damage_dice or ("1d6" if cr < 1 else "2d6" if cr < 3 else "3d8")
    return {
        "armor_class": ac,
        "hit_points_current": hp,
        "hit_points_max": hp,
        "attack_bonus": atk,
        "damage_dice": dice,
        "passive_perception": 10 + rng.randint(-1, 3),
        "initiative_bonus": rng.randint(-1, 3),
        "speed_walk": 30,
    }


def get_npc_id_by_code(conn: sqlite3.Connection, code: str) -> int | None:
    row = conn.execute("SELECT id FROM npcs WHERE code = ?", (code,)).fetchone()
    return int(row["id"] if isinstance(row, sqlite3.Row) else row[0]) if row else None


def spawn_npc(
    db_path_or_conn: str | sqlite3.Connection,
    *,
    name: str,
    tier: str = "T2",
    location_id: int | None = None,
    race: str | None = None,
    class_role: str | None = None,
    is_hostile: bool = False,
    is_important: bool | None = None,
    combat_stance: str | None = None,
    fixed_cr: float | None = None,
    initial_tags: list[str] | None = None,
    goals: list[str] | None = None,
    secret_agenda: str | None = None,
    public_disposition: str | None = None,
    secret_disposition: str | None = None,
    agenda_code: str | None = None,
    reveal_condition: str | None = None,
    betrayal_trigger: dict[str, Any] | None = None,
    backstory_summary: str | None = None,
    code: str | None = None,
    portrait_prompt: str | None = None,
) -> dict[str, Any]:
    """Create (or return existing) full NPC package: npcs + stats + hexagon + tags + goals + player relationship.

    Idempotent by `code` (or slug of name). Monsters (T0/T1 matching a bestiary
    preset) skip goals/backstory depth per ADR B4 "monstery: zjednodušený balík".
    """
    own_conn = isinstance(db_path_or_conn, str)
    conn = sqlite3.connect(db_path_or_conn) if own_conn else db_path_or_conn
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        tier = tier if tier in ("T0", "T1", "T2", "T3") else "T2"
        npc_code = code or _slug(name, "npc")
        now = _utc_now()
        existing_id = get_npc_id_by_code(conn, npc_code)
        if existing_id:
            updates: list[str] = []
            params: list[Any] = []
            if location_id is not None:
                updates.append("current_location_id = ?")
                params.append(int(location_id))
            if portrait_prompt and str(portrait_prompt).strip():
                updates.append("portrait_prompt = COALESCE(NULLIF(TRIM(portrait_prompt), ''), ?)")
                params.append(str(portrait_prompt).strip())
            if updates:
                updates.append("updated_at = ?")
                params.append(now)
                params.append(existing_id)
                conn.execute(
                    f"UPDATE npcs SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                if own_conn:
                    conn.commit()
            if own_conn:
                conn.close()
            return {"npc_id": existing_id, "created": False, "moved": location_id is not None}

        preset = _match_preset(name, class_role) if tier in ("T0", "T1") else None
        full_sheet = _should_build_full_sheet(tier=tier, preset=preset, race=race, class_role=class_role)
        npc_sheet: dict[str, Any] | None = None
        npc_build_input: dict[str, Any] | None = None
        conn.execute(
            """
            INSERT INTO npcs (
                code, name, race, class_role, current_location_id,
                backstory_summary, portrait_prompt, status, is_hostile, is_important, context_enabled,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'alive', ?, ?, 1, ?, ?)
            """,
            (
                npc_code,
                str(name),
                race,
                class_role,
                location_id,
                backstory_summary,
                str(portrait_prompt).strip() if portrait_prompt and str(portrait_prompt).strip() else None,
                1 if (is_hostile or (preset and "hostile" in preset.get("tags", []))) else 0,
                1 if (is_important if is_important is not None else tier in ("T2", "T3")) else 0,
                now,
                now,
            ),
        )
        npc_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        rng = _seeded_rng(f"{npc_code}:{npc_id}")
        cr = fixed_cr if fixed_cr is not None else (preset["cr"] if preset else TIER_CR_DEFAULT.get(tier, 0.5))
        stance = combat_stance or (preset["combat_stance"] if preset else ("wary" if is_hostile else "passive"))
        dice = preset.get("damage_dice") if preset else None
        if full_sheet:
            npc_sheet, npc_build_input = build_npc_sheet(
                race=str(race or ""),
                class_role=str(class_role or ""),
                tier=tier,
                cr=float(cr),
                npc_code=npc_code,
            )
            stats = {
                "armor_class": int(npc_sheet.get("ac_base") or 10),
                "hit_points_current": int(npc_sheet.get("hp") or 10),
                "hit_points_max": int(npc_sheet.get("hp") or 10),
                "speed_walk": int(npc_sheet.get("speed") or 30),
                "passive_perception": int(npc_sheet.get("passive_perception") or 10),
                "initiative_bonus": int((npc_sheet.get("ability_modifiers") or {}).get("dex", 0)),
                "attack_bonus": int((npc_sheet.get("spellcasting") or {}).get("spell_attack_mod") or 2),
                "damage_dice": dice or "1d8",
            }
            ability_scores = npc_sheet.get("abilities") or {}
        else:
            stats = _cr_to_stats(cr, rng, damage_dice=dice)
            ability_scores = {key: 10 + rng.randint(-2, 3) for key in ABILITY_KEYS}
        sc = (npc_sheet or {}).get("spellcasting") or {}
        conn.execute(
            """
            INSERT INTO npc_stats (
                npc_id, armor_class, hit_points_current, hit_points_max, speed_walk,
                str_score, dex_score, con_score, int_score, wis_score, cha_score,
                passive_perception, initiative_bonus, attack_bonus, damage_dice,
                challenge_rating, tier, combat_stance, spell_save_dc, spell_attack_bonus, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                npc_id,
                stats["armor_class"],
                stats["hit_points_current"],
                stats["hit_points_max"],
                stats["speed_walk"],
                int(ability_scores.get("str", 10)),
                int(ability_scores.get("dex", 10)),
                int(ability_scores.get("con", 10)),
                int(ability_scores.get("int", 10)),
                int(ability_scores.get("wis", 10)),
                int(ability_scores.get("cha", 10)),
                stats["passive_perception"],
                stats["initiative_bonus"],
                stats["attack_bonus"],
                stats["damage_dice"],
                cr,
                tier,
                stance,
                int(sc.get("spell_save_dc") or 0) or None,
                int(sc.get("spell_attack_mod") or 0) or None,
                now,
            ),
        )

        hexagon = _roll_hexagon(rng)
        conn.execute(
            """
            INSERT INTO npc_personality_hex (npc_id, kindness, empathy, wit, drive, boldness, composure)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (npc_id, hexagon["kindness"], hexagon["empathy"], hexagon["wit"], hexagon["drive"], hexagon["boldness"], hexagon["composure"]),
        )

        tags = list(initial_tags or [])
        if preset:
            tags.extend(preset.get("tags", []))
        if is_hostile and "hostile" not in tags:
            tags.append("hostile")
        if full_sheet and npc_sheet and npc_build_input:
            apply_npc_sheet(conn, npc_id, npc_sheet, npc_build_input)
        else:
            for skill_name in rng.sample(
                ["Perception", "Persuasion", "Insight", "Stealth", "Athletics", "Investigation", "Intimidation", "Deception"],
                k=2,
            ):
                conn.execute(
                    "INSERT OR IGNORE INTO npc_skills (npc_id, skill_name, bonus) VALUES (?, ?, ?)",
                    (npc_id, skill_name, rng.randint(0, 4)),
                )
        for tag in dict.fromkeys(tags):
            conn.execute(
                "INSERT OR IGNORE INTO npc_tags (npc_id, tag, source, created_at) VALUES (?, ?, 'spawn', ?)",
                (npc_id, tag, now),
            )

        if tier in ("T2", "T3") and not preset:
            for goal in (goals or []):
                conn.execute(
                    "INSERT INTO npc_goals (npc_id, goal_text, priority, is_secret, created_at) VALUES (?, ?, 3, 0, ?)",
                    (npc_id, str(goal), now),
                )
            if secret_agenda:
                conn.execute(
                    "INSERT INTO npc_goals (npc_id, goal_text, priority, is_secret, created_at) VALUES (?, ?, 5, 1, ?)",
                    (npc_id, str(secret_agenda), now),
                )

        # ADR §B5c: mechanical facade — a `secret_disposition` seeds the
        # `npc_agenda` row that drives tag-swap-on-reveal and betrayal
        # triggers, distinct from `secret_agenda`'s narrative-only goal text.
        if secret_disposition:
            npc_agenda.set_agenda_conn(
                conn,
                npc_id,
                public_disposition=public_disposition or "friendly",
                secret_disposition=secret_disposition,
                agenda_code=agenda_code,
                reveal_condition=reveal_condition,
                betrayal_trigger=betrayal_trigger,
            )

        conn.execute(
            """
            INSERT OR IGNORE INTO npc_relationships (source_npc_id, target_type, target_id, attitude, trust, created_at, updated_at)
            VALUES (?, 'player', NULL, 'neutral', 0, ?, ?)
            """,
            (npc_id, now, now),
        )

        if own_conn:
            conn.commit()
        return {"npc_id": npc_id, "created": True, "tier": tier, "challenge_rating": cr}
    finally:
        if own_conn:
            conn.close()


def get_npc_detail(db_path: str, npc_id: int, *, include_secrets: bool = False) -> dict[str, Any] | None:
    """`include_secrets` gates `npc_agenda`'s hidden layer — ADR §15 graph viz:
    "Secrets: npc_agenda ... jen když include_secrets=1 (dev mód)". Once an
    agenda has actually been revealed in-fiction it is no longer a secret, so
    it is always surfaced regardless of the flag.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        npc = conn.execute("SELECT * FROM npcs WHERE id = ?", (npc_id,)).fetchone()
        if not npc:
            return None
        stats = conn.execute("SELECT * FROM npc_stats WHERE npc_id = ?", (npc_id,)).fetchone()
        hexagon = conn.execute("SELECT * FROM npc_personality_hex WHERE npc_id = ?", (npc_id,)).fetchone()
        tags = [r["tag"] for r in conn.execute("SELECT tag FROM npc_tags WHERE npc_id = ?", (npc_id,)).fetchall()]
        skills = [dict(r) for r in conn.execute("SELECT skill_name, bonus FROM npc_skills WHERE npc_id = ?", (npc_id,)).fetchall()]
        spellbook = [
            dict(r)
            for r in conn.execute(
                "SELECT spell_index, spell_level, is_cantrip FROM npc_spellbooks WHERE npc_id = ? ORDER BY is_cantrip DESC, spell_level",
                (npc_id,),
            ).fetchall()
        ]
        goals = [
            dict(r)
            for r in conn.execute(
                "SELECT goal_text, priority, is_secret FROM npc_goals WHERE npc_id = ? AND is_secret = 0", (npc_id,)
            ).fetchall()
        ]
        rel = conn.execute(
            "SELECT attitude, trust, summary FROM npc_relationships WHERE source_npc_id = ? AND target_type = 'player'",
            (npc_id,),
        ).fetchone()
        memories = [
            dict(r)
            for r in conn.execute(
                "SELECT memory_text, importance, created_at FROM npc_memories WHERE npc_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 7",
                (npc_id,),
            ).fetchall()
        ]
        agenda_row = conn.execute("SELECT * FROM npc_agenda WHERE npc_id = ?", (npc_id,)).fetchone()
        agenda: dict[str, Any] | None = None
        if agenda_row:
            revealed = agenda_row["revealed_at_turn"] is not None
            if revealed or include_secrets:
                agenda = dict(agenda_row)
            else:
                agenda = {
                    "npc_id": npc_id,
                    "public_disposition": agenda_row["public_disposition"],
                    "secret_disposition": None,
                    "agenda_code": None,
                    "revealed_at_turn": None,
                }

        return {
            "npc": dict(npc),
            "stats": dict(stats) if stats else None,
            "hexagon": dict(hexagon) if hexagon else None,
            "tags": tags,
            "skills": skills,
            "spellbook": spellbook,
            "goals": goals,
            "relationship": dict(rel) if rel else None,
            "memories": memories,
            "agenda": agenda,
        }
    finally:
        conn.close()


# ADR §16 hexagon axis poles — only surfaced in the GM prompt when a trait is
# actually notable (|value| >= 2), so bland/neutral NPCs don't clutter it.
_HEX_POLES = {
    "kindness": ("cruel", "kind"),
    "empathy": ("callous", "empathetic"),
    "wit": ("dull-witted", "sharp-witted"),
    "drive": ("apathetic", "driven"),
    "boldness": ("timid", "bold"),
    "composure": ("volatile", "composed"),
}


def hexagon_trait_labels(hexagon: dict[str, Any] | None, *, threshold: int = 2) -> list[str]:
    if not hexagon:
        return []
    labels: list[str] = []
    for axis, (low, high) in _HEX_POLES.items():
        value = int(hexagon.get(axis) or 0)
        if value >= threshold:
            labels.append(high)
        elif value <= -threshold:
            labels.append(low)
    return labels


def get_npc_scene_brief_conn(conn: sqlite3.Connection, npc_id: int) -> dict[str, Any] | None:
    """ADR §5 row 4 "Per-NPC detail: hexagon, stats, goals" — a compact,
    GM-prompt-sized brief (top-K memories are handled separately by
    `memory_context`, and secrets stay in `npc_agenda`'s own gated block)."""
    npc = conn.execute("SELECT id, name, race, class_role, is_hostile FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    if not npc:
        return None
    hexagon = conn.execute("SELECT * FROM npc_personality_hex WHERE npc_id = ?", (npc_id,)).fetchone()
    rel = conn.execute(
        "SELECT attitude, trust FROM npc_relationships WHERE source_npc_id = ? AND target_type = 'player'",
        (npc_id,),
    ).fetchone()
    goals = [
        r["goal_text"]
        for r in conn.execute(
            "SELECT goal_text FROM npc_goals WHERE npc_id = ? AND is_secret = 0 ORDER BY priority DESC LIMIT 3",
            (npc_id,),
        ).fetchall()
    ]
    spells = [
        r["spell_index"]
        for r in conn.execute(
            "SELECT spell_index FROM npc_spellbooks WHERE npc_id = ? ORDER BY is_cantrip DESC, spell_level LIMIT 6",
            (npc_id,),
        ).fetchall()
    ]
    return {
        "npc_id": npc_id,
        "name": npc["name"],
        "race": npc["race"],
        "class_role": npc["class_role"],
        "is_hostile": bool(npc["is_hostile"]),
        "attitude": rel["attitude"] if rel else "stranger",
        "trust": int(rel["trust"]) if rel else 0,
        "traits": hexagon_trait_labels(dict(hexagon) if hexagon else None),
        "goals": goals,
        "spells": spells,
    }
