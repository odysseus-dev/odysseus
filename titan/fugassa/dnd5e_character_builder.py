"""D&D 5e character sheet builder — port of Fugassa-II `DnD5eCharacterBuilder.gd`."""

from __future__ import annotations

import math
import re
from typing import Any

from titan.fugassa.class_mechanics import (
    class_mechanic_pickers,
    class_resource_display_lines,
    merge_resources_with_mechanics,
    normalize_mechanic_choices,
    resolve_mechanic_selections,
    validate_class_mechanic_choices,
)
from titan.fugassa.dnd5e_database import Dnd5eDatabase, get_dnd5e_database
from titan.fugassa.dnd5e_options import (
    effective_class,
    effective_race,
    effective_subclass,
)
from titan.fugassa.homebrew_normalize import flatten_homebrew_details

SPELL_SLOT_KEYS = (
    "spell_slots_level_1",
    "spell_slots_level_2",
    "spell_slots_level_3",
    "spell_slots_level_4",
    "spell_slots_level_5",
    "spell_slots_level_6",
    "spell_slots_level_7",
    "spell_slots_level_8",
    "spell_slots_level_9",
)

CLASS_SPELLCASTING_ABILITY: dict[str, str] = {
    "bard": "cha",
    "cleric": "wis",
    "druid": "wis",
    "paladin": "cha",
    "ranger": "wis",
    "sorcerer": "cha",
    "warlock": "cha",
    "wizard": "int",
}

CLASS_SPELL_MODEL: dict[str, str] = {
    "bard": "known",
    "cleric": "prepared",
    "druid": "prepared",
    "paladin": "prepared",
    "ranger": "known",
    "sorcerer": "known",
    "warlock": "known",
    "wizard": "prepared",
}

CLASS_HIT_DICE: dict[str, int] = {
    "barbarian": 12,
    "fighter": 10,
    "paladin": 10,
    "ranger": 10,
    "bard": 8,
    "cleric": 8,
    "druid": 8,
    "monk": 8,
    "rogue": 8,
    "warlock": 8,
    "sorcerer": 6,
    "wizard": 6,
}

SKILL_ABILITIES: dict[str, str] = {
    "acrobatics": "dex",
    "animal-handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight-of-hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}

SKILL_ORDER = list(SKILL_ABILITIES.keys())

ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")


def build(db: Dnd5eDatabase | None, input_data: dict[str, Any]) -> dict[str, Any]:
    database = db or get_dnd5e_database()
    level = max(1, min(int(input_data.get("level", 1)), 20))
    class_label = str(input_data.get("class_label", "")).strip()
    subclass_label = str(input_data.get("subclass_label", "")).strip()
    race_label = str(input_data.get("race_label", "")).strip()
    subrace_label = str(input_data.get("subrace_label", "")).strip()
    abilities_pre_race = _normalize_abilities(input_data.get("abilities_pre_race") or {})
    skill_prof_map = dict(input_data.get("skill_proficiencies") or {})
    expertise_map = dict(input_data.get("expertise") or {})
    hp_method = str(input_data.get("hp_method", "average"))
    hp_rolled_total = int(input_data.get("hp_rolled_total", 0))
    hb = _scrub_homebrew_details(dict(input_data.get("homebrew_details") or {}))
    spell_list_class_id_in = str(input_data.get("spell_list_class_id", "")).strip()

    class_id = _resolve_class_id(database, class_label)
    subclass_id = _resolve_subclass_id(database, class_id, subclass_label)
    race_id = _resolve_race_id(database, race_label)
    subrace_id = _resolve_subrace_id(database, race_id, subrace_label)

    is_homebrew_class = not class_id and bool(class_label)
    is_homebrew_race = not race_id and bool(race_label)

    race_bonuses = database.ability_bonuses_for(race_id, subrace_id) if race_id else {}
    if is_homebrew_race and isinstance(hb.get("ability_bonuses_race"), dict):
        race_bonuses = _merge_numeric_ability_maps(race_bonuses, hb["ability_bonuses_race"])

    abilities_post_race = _apply_race_bonuses(abilities_pre_race, race_bonuses)
    asi_applied = _apply_asi(abilities_post_race, dict(input_data.get("asi_choices") or {}), level, class_id)
    abilities = dict(asi_applied["abilities"])
    feats_picked = list(asi_applied["feats"])
    ability_modifiers = _compute_modifiers(abilities)
    proficiency_bonus = Dnd5eDatabase.proficiency_bonus_at_level(level)

    saving_throw_profs = _class_saving_throws(database, class_id)
    if not class_id and hb.get("saving_throw_profs"):
        raw_saves = hb["saving_throw_profs"]
        if isinstance(raw_saves, list) and raw_saves:
            saving_throw_profs = _normalize_save_prof_list(raw_saves)

    saving_throws = _compute_saving_throws(abilities, ability_modifiers, proficiency_bonus, saving_throw_profs)

    prof_choice_hint = _class_skill_choice_hint(database, class_id)
    if is_homebrew_class and isinstance(hb.get("skill_proficiency_options"), list) and hb["skill_proficiency_options"]:
        opt_names = [str(o) for o in hb["skill_proficiency_options"]]
        tpl_id = spell_list_class_id_in
        tpl_hint = _class_skill_choice_hint(database, tpl_id) if tpl_id else {"count": 0, "options": [], "option_skill_ids": []}
        choose_n = hb.get("skill_proficiency_choose")
        if choose_n is None:
            choose_n = hb.get("optional_skill_proficiency_choose")
        if choose_n is None:
            tpl_count = int(tpl_hint.get("count", 0))
            choose_n = tpl_count if tpl_count > 0 else None
        if choose_n is None or int(choose_n) <= 0:
            choose_n = min(3, len(opt_names))
        pick_n = max(1, min(int(choose_n), len(opt_names)))
        prof_choice_hint = {
            "count": pick_n,
            "options": opt_names,
            "option_skill_ids": _option_skill_ids_from_option_strings(opt_names),
        }

    class_skill_choose_for_cap = int(prof_choice_hint.get("count", 0))
    used_srd_mean_class = False
    if class_skill_choose_for_cap <= 0:
        class_skill_choose_for_cap = _srd_mean_class_skill_choose(database)
        used_srd_mean_class = True

    race_skill_choose_for_cap = 0
    used_srd_mean_race = False
    if race_id:
        race_skill_choose_for_cap = _race_trait_skill_choice_count(database, race_id, subrace_id)
    elif is_homebrew_race:
        if hb.get("skill_proficiency_bonus_race") is not None:
            race_skill_choose_for_cap = max(0, int(hb["skill_proficiency_bonus_race"]))
        else:
            race_skill_choose_for_cap = _srd_mean_race_trait_skill_choose(database)
            used_srd_mean_race = True

    skill_proficiency_cap = max(1, min(class_skill_choose_for_cap + race_skill_choose_for_cap, len(SKILL_ABILITIES)))
    allowed_skill_ids = set(prof_choice_hint.get("option_skill_ids") or []) or None
    if is_homebrew_class and allowed_skill_ids:
        allowed_skill_ids = set(allowed_skill_ids)
    else:
        allowed_skill_ids = None
    skill_prof_map = _sanitize_skill_proficiencies(skill_prof_map, allowed_ids=allowed_skill_ids, cap=skill_proficiency_cap)
    for sid in normalize_mechanic_choices(input_data.get("class_mechanic_choices")).get("expertise", []):
        if skill_prof_map.get(sid):
            expertise_map[sid] = True
    expertise_map = _effective_expertise_map(expertise_map, class_id, level)

    skills = _compute_skills(abilities, ability_modifiers, proficiency_bonus, skill_prof_map, expertise_map)
    passive_perception = 10 + _skill_modifier(skills, "perception")

    hit_die = CLASS_HIT_DICE.get(class_id, 8)
    if not class_id and hb.get("hit_die"):
        hit_die = max(6, min(12, int(hb["hit_die"])))
    con_mod = int(ability_modifiers.get("con", 0))
    hp_bonus_per_level = _racial_hp_bonus_per_level(race_id, subrace_id)
    hp = _compute_hp(hp_method, hp_rolled_total, hit_die, con_mod, level, hp_bonus_per_level)

    dex_mod = int(ability_modifiers.get("dex", 0))
    ac_base = 10 + dex_mod

    class_features = database.list_class_features_up_to(class_id, level) if class_id else []
    subclass_features = (
        database.list_subclass_features_up_to(class_id, subclass_id, level)
        if class_id and subclass_id
        else []
    )
    racial_traits = database.list_traits_for(race_id, subrace_id) if race_id else []

    spellcasting = _compute_spellcasting(database, class_id, level, ability_modifiers, proficiency_bonus)
    if not class_id and isinstance(hb.get("spellcasting"), dict) and hb["spellcasting"].get("has"):
        spellcasting = _spellcasting_from_homebrew(hb["spellcasting"], ability_modifiers, proficiency_bonus, level)

    class_resources = _collect_class_specific(database, class_id, level)
    if not class_id and isinstance(hb.get("class_resources"), dict):
        class_resources = dict(hb["class_resources"])
    elif class_id and isinstance(hb.get("class_resources"), dict) and hb["class_resources"]:
        class_resources = {**class_resources, **hb["class_resources"]}

    asi_levels = _asi_levels_reached(class_id, level)
    speed = _race_speed(database, race_id, subrace_id)
    size = _race_size(database, race_id, subrace_id)
    languages = _race_languages(database, race_id, subrace_id)
    if is_homebrew_race:
        if hb.get("speed") is not None:
            speed = int(hb["speed"])
        if hb.get("size"):
            size = str(hb["size"])
        if isinstance(hb.get("languages"), list) and hb["languages"]:
            languages = [str(x) for x in hb["languages"]]

    if not class_id and hb.get("class_features"):
        _append_llm_features(class_features, hb["class_features"])
    if class_id and not subclass_id and hb.get("subclass_features"):
        _append_llm_features(subclass_features, hb["subclass_features"])
    elif not class_id and hb.get("subclass_features"):
        _append_llm_features(subclass_features, hb["subclass_features"])
    if hb.get("racial_traits"):
        _append_llm_features(racial_traits, hb["racial_traits"])

    spell_list_slug = _spell_list_slug(database, spell_list_class_id_in, class_id)
    spell_list_out = class_id or spell_list_slug
    homebrew_spell_catalog = _normalize_homebrew_spell_catalog(hb.get("spell_catalog", []))
    cantrip_pool_source = spell_list_slug
    if is_homebrew_class and spellcasting.get("has"):
        homebrew_spell_catalog, cantrip_pool_source = _supplement_homebrew_spell_catalog(
            homebrew_spell_catalog,
            spellcasting=spellcasting,
            template_class_id=spell_list_slug,
            db=database,
        )
    homebrew_pending_choices = _detect_homebrew_pending_choices(class_features, racial_traits)

    mechanic_pickers = class_mechanic_pickers(
        class_id=class_id,
        template_class_id=spell_list_slug,
        level=level,
        class_resources=class_resources,
        class_features=class_features,
        is_homebrew_class=is_homebrew_class,
        skill_proficiencies=skill_prof_map,
    )
    mechanic_choices = normalize_mechanic_choices(input_data.get("class_mechanic_choices"))
    class_mechanic_selections = resolve_mechanic_selections(mechanic_pickers, mechanic_choices)
    class_resources = merge_resources_with_mechanics(class_resources, class_mechanic_selections)
    class_resource_summary = class_resource_display_lines(class_resources)

    return {
        "is_homebrew_class": is_homebrew_class,
        "is_homebrew_race": is_homebrew_race,
        "feats_picked": feats_picked,
        "asi_applied_increments": asi_applied.get("increments", {}),
        "resolved": {
            "class_id": class_id,
            "subclass_id": subclass_id,
            "race_id": race_id,
            "subrace_id": subrace_id,
        },
        "labels": {
            "class": class_label,
            "subclass": subclass_label,
            "race": race_label,
            "subrace": subrace_label,
        },
        "level": level,
        "abilities_pre_race": abilities_pre_race,
        "ability_bonuses_race": race_bonuses,
        "abilities": abilities,
        "ability_modifiers": ability_modifiers,
        "proficiency_bonus": proficiency_bonus,
        "hit_die": hit_die,
        "hp": hp,
        "hp_method": hp_method,
        "ac_base": ac_base,
        "speed": speed,
        "size": size,
        "languages": languages,
        "saving_throws": saving_throws,
        "saving_throw_profs": list(saving_throw_profs),
        "skills": skills,
        "passive_perception": passive_perception,
        "class_features": class_features,
        "subclass_features": subclass_features,
        "racial_traits": racial_traits,
        "spellcasting": spellcasting,
        "class_resources": class_resources,
        "asi_feat_levels_reached": list(asi_levels),
        "skill_proficiency_choice_hint": prof_choice_hint,
        "skill_proficiency_cap": skill_proficiency_cap,
        "skill_proficiency_cap_parts": {
            "class_choose": class_skill_choose_for_cap,
            "race_choose": race_skill_choose_for_cap,
            "used_srd_mean_class": used_srd_mean_class,
            "used_srd_mean_race": used_srd_mean_race,
        },
        "spell_list_class_id": spell_list_out,
        "homebrew_spell_catalog": homebrew_spell_catalog,
        "homebrew_pending_choices": homebrew_pending_choices,
        "class_mechanic_pickers": mechanic_pickers,
        "class_mechanic_selections": class_mechanic_selections,
        "class_resource_summary": class_resource_summary,
        "homebrew_cantrips_supplemented": any(
            s.get("srd_fallback") for s in homebrew_spell_catalog if isinstance(s, dict)
        ),
        "cantrip_pool_source": cantrip_pool_source,
    }


def class_id_from_label(db: Dnd5eDatabase | None, label: str) -> str:
    return _resolve_class_id(db or get_dnd5e_database(), label)


PLAYSTYLE_FRAMEWORK: dict[str, str] = {
    "hardcore": "rules_based",
    "adventure": "rules_based",
    "exploration": "rules_based",
    "survival": "rules_based",
    "mystery": "rules_based",
    "slice_of_life": "freeform",
}


def _playstyle_framework(draft: dict[str, Any]) -> str:
    explicit = str(draft.get("playstyle_framework") or "").strip()
    if explicit:
        return explicit
    playstyle = str(draft.get("playstyle") or "adventure")
    return PLAYSTYLE_FRAMEWORK.get(playstyle, "rules_based")


def draft_to_build_input(draft: dict[str, Any]) -> dict[str, Any]:
    abilities = draft.get("abilities") or {}
    return {
        "class_label": effective_class(draft),
        "subclass_label": effective_subclass(draft),
        "race_label": effective_race(draft),
        "subrace_label": _effective_subrace(draft),
        "level": int(draft.get("level") or 1),
        "abilities_pre_race": abilities,
        "skill_proficiencies": draft.get("skill_proficiencies") or {},
        "expertise": draft.get("expertise") or {},
        "selected_cantrips": draft.get("selected_cantrips") or [],
        "selected_spells_by_level": draft.get("selected_spells_by_level") or {},
        "asi_choices": draft.get("asi_choices") or {},
        "homebrew_choices": draft.get("homebrew_choices") or {},
        "class_mechanic_choices": draft.get("class_mechanic_choices") or {},
        "hp_method": draft.get("hp_method") or "average",
        "hp_rolled_total": int(draft.get("hp_rolled_total") or 0),
        "homebrew_details": draft.get("homebrew_details") or {},
        "spell_list_class_id": str(draft.get("spell_list_class_id") or ""),
        "rules_mode": str(draft.get("rules_mode") or "5e-style"),
        "playstyle_framework": _playstyle_framework(draft),
    }


def _effective_subrace(draft: dict[str, Any]) -> str:
    custom = str(draft.get("player_subrace_custom") or "").strip()
    if custom:
        return custom
    idx = int(draft.get("player_subrace_idx") or 0)
    options = draft.get("_subrace_options") or []
    if isinstance(options, list) and 0 <= idx < len(options):
        return str(options[idx])
    return ""


def normalize_cantrip_set(raw: Any) -> dict[str, bool]:
    if isinstance(raw, dict):
        return {str(k): bool(v) for k, v in raw.items() if v}
    if isinstance(raw, list):
        return {str(x): True for x in raw if str(x).strip()}
    return {}


def normalize_spells_by_level(raw: Any) -> dict[int, dict[str, bool]]:
    out: dict[int, dict[str, bool]] = {}
    if not isinstance(raw, dict):
        return out
    for level_key, bucket in raw.items():
        lvl = int(str(level_key))
        if isinstance(bucket, dict):
            out[lvl] = {str(k): bool(v) for k, v in bucket.items() if v}
        elif isinstance(bucket, list):
            out[lvl] = {str(spell_id): True for spell_id in bucket if str(spell_id).strip()}
    return out


def spell_budgets(sc: dict[str, Any]) -> dict[str, Any]:
    cant_max = max(0, int(sc.get("cantrips_known", 0)))
    model = str(sc.get("model", "prepared"))
    if model == "known":
        spell_cap = max(0, int(sc.get("spells_known", 0)))
    else:
        spell_cap = max(0, int(sc.get("spells_prepared_estimate", 0)))
    max_lvl = max(0, int(sc.get("max_castable_spell_level", 0)))
    return {
        "cantrip_max": cant_max,
        "leveled_cap": spell_cap,
        "model": model,
        "max_spell_level": max_lvl,
        "pact_magic": bool(sc.get("pact_magic", False)),
    }


def count_leveled_spells(selected_spells_by_level: dict[int, dict[str, bool]]) -> int:
    return sum(len(bucket) for bucket in selected_spells_by_level.values())


def validate_sheet_input(
    draft: dict[str, Any],
    *,
    db: Dnd5eDatabase | None = None,
) -> dict[str, Any]:
    """Returns { ok, errors[], sheet } — hard block rules for rules_based playstyles."""
    database = db or get_dnd5e_database()
    build_input = draft_to_build_input(draft)
    sheet = build(database, build_input)
    errors: list[str] = []

    framework = str(build_input.get("playstyle_framework") or "rules_based")
    if framework == "freeform":
        return {"ok": True, "errors": [], "sheet": sheet}

    race_label = build_input["race_label"]
    class_label = build_input["class_label"]
    resolved = sheet.get("resolved") or {}

    if race_label and sheet.get("is_homebrew_race") and not (draft.get("homebrew_details") or {}):
        errors.append("Custom race requires generated or manual homebrew mechanics.")
    if class_label and sheet.get("is_homebrew_class") and not (draft.get("homebrew_details") or {}):
        errors.append("Custom class requires generated or manual homebrew mechanics.")

    race_id = resolved.get("race_id") or ""
    if race_id:
        race = database.get_race(race_id)
        subraces = [s for s in (race.get("subraces_detail") or []) if isinstance(s, dict)]
        if subraces and not resolved.get("subrace_id") and not build_input.get("subrace_label"):
            errors.append("Subrace is required for this race.")

    prof_picked = sum(
        1
        for row in (sheet.get("skills") or [])
        if isinstance(row, dict) and row.get("proficient")
    )
    cap = int(sheet.get("skill_proficiency_cap") or 0)
    allowed_ids = set((sheet.get("skill_proficiency_choice_hint") or {}).get("option_skill_ids") or [])
    if allowed_ids:
        invalid = [sid for sid, on in (build_input.get("skill_proficiencies") or {}).items() if on and sid not in allowed_ids]
        if invalid:
            errors.append(
                f"Skill(s) not allowed for this class: {', '.join(_skill_display_name(s) for s in invalid)}."
            )
    if prof_picked > cap:
        errors.append(f"Too many skill proficiencies selected ({prof_picked}/{cap}).")
    elif prof_picked < cap:
        errors.append(f"Pick {cap - prof_picked} more skill proficiency(ies) ({prof_picked}/{cap}).")

    asi_levels = list(sheet.get("asi_feat_levels_reached") or [])
    asi_choices = dict(build_input.get("asi_choices") or {})
    for lvl in asi_levels:
        entry = asi_choices.get(str(lvl)) or asi_choices.get(lvl)
        if not entry or str(entry.get("kind", "none")) == "none":
            errors.append(f"Level {lvl}: choose an ASI or feat.")

    sc = sheet.get("spellcasting") or {}
    if sc.get("has"):
        cantrips = normalize_cantrip_set(build_input.get("selected_cantrips"))
        spells = normalize_spells_by_level(build_input.get("selected_spells_by_level"))
        budgets = spell_budgets(sc)
        cant_cap = int(budgets["cantrip_max"])
        cant_picked = len(cantrips)
        if cant_cap > 0 and cant_picked < cant_cap:
            errors.append(f"Pick {cant_cap - cant_picked} more cantrip(s) ({cant_picked}/{cant_cap}).")
        elif cant_picked > cant_cap:
            errors.append(f"Too many cantrips selected ({cant_picked}/{cant_cap}).")

        leveled_cap = int(budgets["leveled_cap"])
        leveled_picked = count_leveled_spells(spells)
        if leveled_cap > 0 and leveled_picked < leveled_cap:
            label = "spells prepared" if budgets["model"] == "prepared" else "spells known"
            errors.append(f"Pick {leveled_cap - leveled_picked} more {label} ({leveled_picked}/{leveled_cap}).")
        elif leveled_picked > leveled_cap:
            errors.append(f"Too many leveled spells selected ({leveled_picked}/{leveled_cap}).")

    for choice in sheet.get("homebrew_pending_choices") or []:
        if not isinstance(choice, dict):
            continue
        cid = str(choice.get("id", "")).strip()
        if not cid:
            continue
        val = str((draft.get("homebrew_choices") or {}).get(cid) or "").strip()
        if not val:
            label = str(choice.get("label", cid)).strip()
            errors.append(f"{label}: select in Required choices (Character tab, below Skills).")

    errors.extend(
        validate_class_mechanic_choices(
            sheet.get("class_mechanic_pickers") or [],
            draft.get("class_mechanic_choices"),
        )
    )

    return {"ok": not errors, "errors": errors, "sheet": sheet}


def can_select_spell(
    sc: dict[str, Any],
    selected_cantrips: dict[str, bool],
    selected_spells_by_level: dict[int, dict[str, bool]],
    spell_level: int,
    spell_id: str,
    selecting: bool,
) -> dict[str, Any]:
    if not selecting:
        return {"ok": True, "reason": ""}
    budgets = spell_budgets(sc)
    max_lvl = int(budgets["max_spell_level"])
    if spell_level == 0:
        cap_c = int(budgets["cantrip_max"])
        if selected_cantrips.get(spell_id):
            return {"ok": True, "reason": ""}
        if len(selected_cantrips) >= cap_c:
            return {"ok": False, "reason": f"Cantrip limit reached ({cap_c}). Deselect another cantrip first."}
        return {"ok": True, "reason": ""}
    if max_lvl <= 0:
        return {"ok": False, "reason": "No spell slots at this level — cannot learn leveled spells yet."}
    if spell_level > max_lvl:
        return {"ok": False, "reason": f"Max spell level you can cast is {max_lvl} — pick a lower-tier spell."}
    cap_l = int(budgets["leveled_cap"])
    cur = count_leveled_spells(selected_spells_by_level)
    bucket = dict(selected_spells_by_level.get(spell_level) or selected_spells_by_level.get(int(spell_level)) or {})
    if bucket.get(spell_id):
        return {"ok": True, "reason": ""}
    if cur >= cap_l:
        kind = str(budgets["model"])
        lab = "Spells prepared" if kind == "prepared" else "Spells known"
        return {"ok": False, "reason": f"{lab} limit reached ({cap_l}). Deselect one first."}
    return {"ok": True, "reason": ""}


def _sanitize_skill_proficiencies(
    prof_map: dict[str, Any],
    *,
    allowed_ids: set[str] | None,
    cap: int,
) -> dict[str, bool]:
    """Keep only allowed skills, in SKILL_ORDER priority, up to cap."""
    picked: list[str] = []
    for skill_id in SKILL_ORDER:
        if not prof_map.get(skill_id):
            continue
        if allowed_ids is not None and skill_id not in allowed_ids:
            continue
        picked.append(skill_id)
    out: dict[str, bool] = {}
    for skill_id in picked[: max(0, cap)]:
        out[skill_id] = True
    return out


def _effective_expertise_map(expertise_map: dict[str, Any], class_id: str, level: int) -> dict[str, bool]:
    if class_id == "rogue" and level >= 1:
        return {str(k): bool(v) for k, v in expertise_map.items() if v}
    if class_id == "bard" and level >= 3:
        return {str(k): bool(v) for k, v in expertise_map.items() if v}
    return {}


def _scrub_homebrew_details(hb: dict[str, Any]) -> dict[str, Any]:
    out = flatten_homebrew_details(hb)
    if out.get("skill_proficiency_choose") is None and out.get("optional_skill_proficiency_choose") is not None:
        out["skill_proficiency_choose"] = out["optional_skill_proficiency_choose"]
    return out


def _normalize_homebrew_spell_catalog(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        lvl = max(0, min(int(item.get("level", item.get("spell_level", 0))), 9))
        slug_in = str(item.get("index", "")).strip().lower()
        base_slug = slug_in or _slug(name) or "spell"
        key = f"hbspell:{base_slug}"
        suffix = 0
        while key in used_keys:
            suffix += 1
            key = f"hbspell:{base_slug}_{suffix}"
        used_keys.add(key)
        desc_raw = item.get("desc", item.get("description", ""))
        if isinstance(desc_raw, list):
            desc_arr = [str(p).strip() for p in desc_raw if str(p).strip()]
        else:
            desc_arr = [str(desc_raw).strip()] if str(desc_raw).strip() else []
        school_name = str(item.get("school", "")).strip()
        comps: list[str] = []
        comp_v = item.get("components", [])
        if isinstance(comp_v, list):
            for comp in comp_v:
                if isinstance(comp, str):
                    comps.append(comp)
                elif isinstance(comp, dict):
                    comps.append(str(comp.get("name", comp)))
        out.append(
            {
                "index": key,
                "name": name,
                "level": lvl,
                "range": str(item.get("range", "")).strip(),
                "casting_time": str(item.get("casting_time", item.get("castingTime", ""))).strip(),
                "duration": str(item.get("duration", "")).strip(),
                "components": comps,
                "material": str(item.get("material", "")).strip(),
                "school": {"name": school_name} if school_name else {},
                "desc": desc_arr,
                "ritual": bool(item.get("ritual", False)),
                "concentration": bool(item.get("concentration", False)),
            }
        )
    return out


def _spell_list_slug(db: Dnd5eDatabase, spell_list_class_id_in: str, class_id: str) -> str:
    raw = str(spell_list_class_id_in or "").strip()
    if raw:
        resolved = _resolve_class_id(db, raw)
        if resolved:
            return resolved
        slug = _slug(raw)
        if slug:
            return slug
    return str(class_id or "").strip()


SRD_SPELL_LIST_FALLBACK_BY_CLASS: dict[str, str] = {
    # Bundled SRD has no Artificer spell rows — use closest INT arcane list only.
    "artificer": "wizard",
}

# Back-compat alias used in tests / older references.
SRD_CANTRIP_FALLBACK_BY_CLASS = SRD_SPELL_LIST_FALLBACK_BY_CLASS


def _srd_class_spell_pool(
    db: Dnd5eDatabase,
    template_class_id: str,
    spell_level: int,
) -> tuple[list[dict[str, Any]], str]:
    """Return SRD spells for one class list at a level — never merge Warlock/Wizard/etc."""
    tpl = str(template_class_id or "").strip().lower()
    if not tpl:
        return [], ""
    pool = db.list_spells_for(tpl, spell_level)
    if pool:
        return pool, tpl
    fallback = SRD_SPELL_LIST_FALLBACK_BY_CLASS.get(tpl, "")
    if fallback:
        pool = db.list_spells_for(fallback, spell_level)
        if pool:
            return pool, fallback
    return [], tpl


def _srd_cantrip_pool(db: Dnd5eDatabase, template_class_id: str) -> tuple[list[dict[str, Any]], str]:
    return _srd_class_spell_pool(db, template_class_id, 0)


def _catalog_spell_name_slugs(catalog: list[dict[str, Any]]) -> set[str]:
    slugs: set[str] = set()
    for spell in catalog:
        if not isinstance(spell, dict):
            continue
        name = str(spell.get("name", "")).strip()
        if name:
            slugs.add(_slug(name))
        idx = str(spell.get("index", "")).strip().lower()
        if idx:
            base = idx.removeprefix("hbspell:").removeprefix("srdfallback:")
            if base:
                slugs.add(_slug(base))
    return slugs


def _srd_spell_to_catalog_entry(spell: dict[str, Any], source: str) -> dict[str, Any]:
    srd_idx = str(spell.get("index", "")).strip()
    lvl = max(0, int(spell.get("level", 0)))
    desc_raw = spell.get("desc") or []
    if isinstance(desc_raw, list):
        desc_arr = [str(p).strip() for p in desc_raw if str(p).strip()]
    else:
        desc_arr = [str(desc_raw).strip()] if str(desc_raw).strip() else []
    school = spell.get("school") if isinstance(spell.get("school"), dict) else {}
    return {
        "index": f"hbspell:srdfallback:{srd_idx}",
        "name": str(spell.get("name", srd_idx)),
        "level": lvl,
        "range": str(spell.get("range", "")).strip(),
        "casting_time": str(spell.get("casting_time", "")).strip(),
        "duration": str(spell.get("duration", "")).strip(),
        "components": list(spell.get("components") or []),
        "material": str(spell.get("material", "")).strip(),
        "school": school,
        "desc": desc_arr,
        "ritual": bool(spell.get("ritual", False)),
        "concentration": bool(spell.get("concentration", False)),
        "srd_fallback": True,
        "cantrip_source_class": source,
    }


def _merge_srd_spells_into_catalog(
    catalog: list[dict[str, Any]],
    *,
    pool: list[dict[str, Any]],
    source: str,
    used_keys: set[str],
    used_names: set[str],
) -> list[dict[str, Any]]:
    out = list(catalog)
    for spell in pool:
        if not isinstance(spell, dict):
            continue
        srd_idx = str(spell.get("index", "")).strip()
        if not srd_idx:
            continue
        name_slug = _slug(str(spell.get("name", "")))
        if name_slug and name_slug in used_names:
            continue
        key = f"hbspell:srdfallback:{srd_idx}"
        if key in used_keys:
            continue
        used_keys.add(key)
        if name_slug:
            used_names.add(name_slug)
        out.append(_srd_spell_to_catalog_entry(spell, source))
    return out


def _supplement_homebrew_spell_catalog(
    catalog: list[dict[str, Any]],
    *,
    spellcasting: dict[str, Any],
    template_class_id: str,
    db: Dnd5eDatabase,
) -> tuple[list[dict[str, Any]], str]:
    """Merge template-class SRD spells into homebrew catalog so pickers always offer real choice."""
    sc = spellcasting if isinstance(spellcasting, dict) else {}
    pool_source = str(template_class_id or "").strip().lower()
    out = list(catalog)
    used_keys = {str(s.get("index", "")).strip() for s in out if s.get("index")}
    used_names = _catalog_spell_name_slugs(out)

    cantrips_known = int(sc.get("cantrips_known") or 0)
    if cantrips_known > 0:
        pool, source = _srd_class_spell_pool(db, template_class_id, 0)
        if source:
            pool_source = source
        out = _merge_srd_spells_into_catalog(
            out,
            pool=pool,
            source=source or pool_source,
            used_keys=used_keys,
            used_names=used_names,
        )

    model = str(sc.get("model", "prepared"))
    leveled_cap = (
        int(sc.get("spells_known") or 0)
        if model == "known"
        else int(sc.get("spells_prepared_estimate") or 0)
    )
    max_lvl = max(0, int(sc.get("max_castable_spell_level") or 0))
    if leveled_cap > 0 and max_lvl > 0:
        for lvl in range(1, max_lvl + 1):
            pool, source = _srd_class_spell_pool(db, template_class_id, lvl)
            if source:
                pool_source = source
            out = _merge_srd_spells_into_catalog(
                out,
                pool=pool,
                source=source or pool_source,
                used_keys=used_keys,
                used_names=used_names,
            )

    return out, pool_source


def _supplement_homebrew_cantrips(
    catalog: list[dict[str, Any]],
    *,
    cantrips_known: int,
    template_class_id: str,
    db: Dnd5eDatabase,
) -> tuple[list[dict[str, Any]], str]:
    return _supplement_homebrew_spell_catalog(
        catalog,
        spellcasting={"cantrips_known": cantrips_known, "model": "prepared"},
        template_class_id=template_class_id,
        db=db,
    )


def _detect_homebrew_pending_choices(
    class_features: list[Any],
    racial_traits: list[Any],
) -> list[dict[str, Any]]:
    choices: list[dict[str, Any]] = []
    for trait in racial_traits:
        if not isinstance(trait, dict):
            continue
        name = str(trait.get("name", "")).strip()
        if not name:
            continue
        desc = str(trait.get("desc", trait.get("description", ""))).strip()
        tid = _slug(name) or "trait"
        if re.search(r"skill.*your choice", desc, re.I):
            choices.append(
                {
                    "id": f"trait:{tid}",
                    "label": name,
                    "type": "skill_any",
                    "hint": desc,
                }
            )
        if re.search(r"language.*your choice", desc, re.I):
            choices.append(
                {
                    "id": f"trait:{tid}:language",
                    "label": f"{name} — language",
                    "type": "text",
                    "hint": desc,
                }
            )
    for feat in class_features:
        if not isinstance(feat, dict):
            continue
        name = str(feat.get("name", "")).strip()
        if not name:
            continue
        desc = str(feat.get("desc", feat.get("description", ""))).strip()
        match = re.search(r"choose from:\s*([^.]+)", desc, re.I)
        if not match:
            continue
        raw_opts = re.split(r",|\bor\b", match.group(1))
        options = [o.strip() for o in raw_opts if o.strip()]
        if not options:
            continue
        choices.append(
            {
                "id": f"feature:{_slug(name)}",
                "label": name,
                "type": "enum",
                "options": options,
                "hint": desc,
            }
        )
    return choices


def _merge_numeric_ability_maps(base: dict[str, int], extra: dict[str, Any]) -> dict[str, int]:
    out = dict(base)
    for key, val in extra.items():
        slug = _normalize_ability_slug_for_save(str(key))
        if slug:
            out[slug] = int(out.get(slug, 0)) + _parse_numeric_bonus(val)
    return out


def _parse_numeric_bonus(val: Any) -> int:
    if isinstance(val, bool):
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    text = str(val or "").strip().replace("+", "")
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def _normalize_ability_slug_for_save(value: str) -> str:
    t = str(value or "").lower().strip()
    aliases = {
        "strength": "str",
        "dexterity": "dex",
        "constitution": "con",
        "intelligence": "int",
        "wisdom": "wis",
        "charisma": "cha",
    }
    if t in aliases:
        return aliases[t]
    if t in ABILITY_KEYS:
        return t
    for key in ABILITY_KEYS:
        if t == key or t.startswith(key):
            return key
    return ""


def _normalize_save_prof_list(arr: list[Any]) -> list[str]:
    out: list[str] = []
    for value in arr:
        slug = _normalize_ability_slug_for_save(str(value))
        if slug and slug not in out:
            out.append(slug)
    return out


def _normalize_slot_levels(raw: Any) -> dict[int, int]:
    out: dict[int, int] = {}
    if not isinstance(raw, dict):
        return out
    for key, val in raw.items():
        tier = int(str(key))
        count = int(val)
        if 1 <= tier <= 9 and count > 0:
            out[tier] = count
    return out


def _int_homebrew_field(raw: Any, default: int = 0) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _spellcasting_from_homebrew(
    hb_sc: dict[str, Any],
    mods: dict[str, int],
    prof_bonus: int,
    level: int,
) -> dict[str, Any]:
    ability = _normalize_ability_slug_for_save(str(hb_sc.get("ability", "int"))) or "int"
    ab_mod = int(mods.get(ability, 0))
    model = str(hb_sc.get("model", "prepared"))
    spells_known = _int_homebrew_field(hb_sc.get("spells_known"), -1)
    spells_prepared_estimate = _int_homebrew_field(hb_sc.get("spells_prepared_estimate"), -1)
    out: dict[str, Any] = {
        "has": bool(hb_sc.get("has", False)),
        "ability": ability,
        "spell_save_dc": 8 + prof_bonus + ab_mod,
        "spell_attack_mod": prof_bonus + ab_mod,
        "cantrips_known": _int_homebrew_field(hb_sc.get("cantrips_known"), 0),
        "spells_known": spells_known,
        "spells_prepared_estimate": spells_prepared_estimate,
        "slots_by_level": _normalize_slot_levels(hb_sc.get("slots_by_level", {})),
        "model": model,
        "pact_magic": bool(hb_sc.get("pact_magic", False)),
    }
    slots = out["slots_by_level"]
    out["max_castable_spell_level"] = max(slots.keys(), default=0)
    if model == "known" and out["spells_known"] < 0:
        out["spells_known"] = 0
    if spells_prepared_estimate <= 0 and model == "prepared":
        out["spells_prepared_estimate"] = max(1, level + ab_mod)
    note = str(hb_sc.get("caster_progression_note", hb_sc.get("note", ""))).strip()
    out["caster_progression_note"] = note
    return out


def _append_llm_features(target: list[Any], src_raw: Any) -> None:
    if not isinstance(src_raw, list):
        return
    for item in src_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        target.append({"name": name, "level": int(item.get("level", 0)), "desc": str(item.get("desc", ""))})


def _slug(label: str) -> str:
    s = str(label or "").lower().strip()
    parts = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return re.sub(r"-+", "-", parts)


def _resolve_class_id(db: Dnd5eDatabase, label: str) -> str:
    if not label:
        return ""
    slug = _slug(label)
    for cls in db.list_classes():
        if isinstance(cls, dict) and str(cls.get("index", "")) == slug:
            return slug
    return ""


def _resolve_subclass_id(db: Dnd5eDatabase, class_id: str, label: str) -> str:
    if not class_id or not label:
        return ""
    slug = _slug(label)
    for sub in db.list_subclasses_for(class_id):
        sid = str(sub.get("index", ""))
        if sid and (slug == sid or sid in slug):
            return sid
    return ""


def _resolve_race_id(db: Dnd5eDatabase, label: str) -> str:
    if not label:
        return ""
    slug = _slug(label)
    for race in db.list_races():
        if isinstance(race, dict) and str(race.get("index", "")) == slug:
            return slug
    return ""


def _resolve_subrace_id(db: Dnd5eDatabase, race_id: str, label: str) -> str:
    if not race_id or not label:
        return ""
    slug = _slug(label)
    race = db.get_race(race_id)
    for sub in race.get("subraces_detail") or []:
        if not isinstance(sub, dict):
            continue
        sid = str(sub.get("index", ""))
        if slug == sid or sid in slug:
            return sid
    return ""


def _normalize_abilities(src: dict[str, Any]) -> dict[str, int]:
    return {k: int(src.get(k, 10)) for k in ABILITY_KEYS}


def _apply_race_bonuses(base: dict[str, int], race_bonuses: dict[str, int]) -> dict[str, int]:
    out = dict(base)
    for key, bonus in race_bonuses.items():
        out[str(key)] = int(out.get(key, 10)) + int(bonus)
    return out


def _apply_asi(
    base_abilities: dict[str, int],
    asi_choices: dict[Any, Any],
    level: int,
    class_id: str,
) -> dict[str, Any]:
    out = dict(base_abilities)
    increments = {k: 0 for k in ABILITY_KEYS}
    feats: list[dict[str, Any]] = []
    valid_levels = set(_asi_levels_reached(class_id, level))
    for key, entry_raw in asi_choices.items():
        lvl = int(str(key))
        if lvl not in valid_levels or not isinstance(entry_raw, dict):
            continue
        entry = entry_raw
        kind = str(entry.get("kind", "none"))
        if kind == "plus2":
            ab = str(entry.get("ability", "")).lower()
            if ab in ABILITY_KEYS:
                increments[ab] += 2
        elif kind == "plus1plus1":
            for ab_raw in (entry.get("abilities") or [])[:2]:
                ab2 = str(ab_raw).lower()
                if ab2 in ABILITY_KEYS:
                    increments[ab2] += 1
        elif kind == "feat":
            feat_name = str(entry.get("feat", "")).strip()
            if feat_name:
                feats.append({"level": lvl, "name": feat_name})
    for key in ABILITY_KEYS:
        out[key] = min(int(out.get(key, 10)) + int(increments.get(key, 0)), 20)
    return {"abilities": out, "increments": increments, "feats": feats}


def _compute_modifiers(abilities: dict[str, int]) -> dict[str, int]:
    return {k: math.floor((int(abilities.get(k, 10)) - 10) / 2) for k in ABILITY_KEYS}


def _class_saving_throws(db: Dnd5eDatabase, class_id: str) -> list[str]:
    if not class_id:
        return []
    cls = db.get_class_data(class_id)
    out: list[str] = []
    for save in cls.get("saving_throws") or []:
        if isinstance(save, dict):
            idx = str(save.get("index", "")).lower()
            if idx:
                out.append(idx)
    return out


def _compute_saving_throws(
    abilities: dict[str, int],
    mods: dict[str, int],
    prof_bonus: int,
    profs: list[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ABILITY_KEYS:
        proficient = key in profs
        mod = int(mods.get(key, 0))
        total = mod + (prof_bonus if proficient else 0)
        out.append(
            {
                "ability": key,
                "modifier": total,
                "modifier_str": f"+{total}" if total >= 0 else str(total),
                "proficient": proficient,
            }
        )
    return out


def _compute_skills(
    abilities: dict[str, int],
    mods: dict[str, int],
    prof_bonus: int,
    prof_map: dict[str, Any],
    expertise_map: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for skill_id in SKILL_ORDER:
        ability = SKILL_ABILITIES[skill_id]
        base_mod = int(mods.get(ability, 0))
        proficient = bool(prof_map.get(skill_id, False))
        has_expertise = bool(expertise_map.get(skill_id, False)) and proficient
        bonus = prof_bonus * 2 if has_expertise else (prof_bonus if proficient else 0)
        total = base_mod + bonus
        out.append(
            {
                "index": skill_id,
                "name": _skill_display_name(skill_id),
                "ability": ability,
                "modifier": total,
                "modifier_str": f"+{total}" if total >= 0 else str(total),
                "proficient": proficient,
                "expertise": has_expertise,
            }
        )
    return out


def _skill_modifier(skills: list[dict[str, Any]], skill_id: str) -> int:
    for row in skills:
        if row.get("index") == skill_id:
            return int(row.get("modifier", 0))
    return 0


def _skill_display_name(skill_id: str) -> str:
    if skill_id == "animal-handling":
        return "Animal Handling"
    if skill_id == "sleight-of-hand":
        return "Sleight of Hand"
    return " ".join(part.capitalize() for part in skill_id.split("-"))


def _compute_hp(
    method: str,
    rolled_total: int,
    hit_die: int,
    con_mod: int,
    level: int,
    racial_bonus_per_level: int,
) -> int:
    if level <= 0 or hit_die <= 0:
        return 0
    if method == "rolled":
        return max(1, rolled_total + racial_bonus_per_level * level)
    if method == "max":
        hp_max = hit_die + con_mod + racial_bonus_per_level
        if level > 1:
            hp_max += (hit_die + con_mod + racial_bonus_per_level) * (level - 1)
        return max(1, hp_max)
    hp_first = hit_die + con_mod + racial_bonus_per_level
    avg_per_next = hit_die // 2 + 1 + con_mod + racial_bonus_per_level
    return max(1, hp_first + avg_per_next * (level - 1))


def _racial_hp_bonus_per_level(race_id: str, subrace_id: str) -> int:
    if race_id == "dwarf" and subrace_id == "hill":
        return 1
    return 0


def _compute_spellcasting(
    db: Dnd5eDatabase,
    class_id: str,
    level: int,
    mods: dict[str, int],
    prof_bonus: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "has": False,
        "ability": "",
        "spell_save_dc": 0,
        "spell_attack_mod": 0,
        "cantrips_known": 0,
        "spells_known": -1,
        "spells_prepared_estimate": -1,
        "slots_by_level": {},
        "model": "",
    }
    if not class_id or class_id not in CLASS_SPELLCASTING_ABILITY:
        return out
    ability = CLASS_SPELLCASTING_ABILITY[class_id]
    ab_mod = int(mods.get(ability, 0))
    lvl_row = db.get_class_level(class_id, level)
    sc = dict(lvl_row.get("spellcasting") or {})
    out["has"] = bool(sc)
    out["ability"] = ability
    out["spell_save_dc"] = 8 + prof_bonus + ab_mod
    out["spell_attack_mod"] = prof_bonus + ab_mod
    out["model"] = CLASS_SPELL_MODEL.get(class_id, "prepared")
    slots: dict[int, int] = {}
    for i, key in enumerate(SPELL_SLOT_KEYS):
        count = int(sc.get(key, 0))
        if count > 0:
            slots[i + 1] = count
    out["slots_by_level"] = slots
    out["cantrips_known"] = int(sc.get("cantrips_known", 0))
    if "spells_known" in sc:
        out["spells_known"] = int(sc.get("spells_known", 0))
    if out["model"] == "prepared":
        divisor = 2 if class_id in ("paladin", "ranger") else 1
        out["spells_prepared_estimate"] = max(1, level // divisor + ab_mod)
    out["max_castable_spell_level"] = max(slots.keys(), default=0)
    out["pact_magic"] = class_id == "warlock"
    out["caster_progression_note"] = _caster_progression_note(class_id, level, out)
    return out


def _caster_progression_note(class_id: str, level: int, sc_out: dict[str, Any]) -> str:
    if class_id == "warlock":
        slots = sc_out.get("slots_by_level") or {}
        if not slots:
            return "Warlock: pact spell slots scale together — all expended slots are the same spell level."
        top = max(slots.keys())
        count = int(slots.get(top, 0))
        return (
            f"Warlock pact magic: treat all {count} pact slot(s) as "
            f"{_ordinal_spell_level(top)}-level for upcasting rules."
        )
    if class_id in ("ranger", "paladin"):
        return "Half-caster: spell slots lag behind full casters; max leveled spell tier follows your slot table above."
    return ""


def _ordinal_spell_level(n: int) -> str:
    if n == 1:
        return "1st"
    if n == 2:
        return "2nd"
    if n == 3:
        return "3rd"
    return f"{n}th"


def _collect_class_specific(db: Dnd5eDatabase, class_id: str, level: int) -> dict[str, Any]:
    if not class_id:
        return {}
    row = db.get_class_level(class_id, level)
    spec = row.get("class_specific")
    return dict(spec) if isinstance(spec, dict) else {}


def _asi_levels_reached(class_id: str, level: int) -> list[int]:
    base_levels = [4, 8, 12, 16, 19]
    out = [lvl for lvl in base_levels if lvl <= level]
    if not class_id:
        return out
    if class_id == "fighter":
        if level >= 6:
            out.append(6)
        if level >= 14:
            out.append(14)
    elif class_id == "rogue" and level >= 10:
        out.append(10)
    return sorted(set(out))


def _race_speed(db: Dnd5eDatabase, race_id: str, subrace_id: str) -> int:
    if not race_id:
        return 30
    race = db.get_race(race_id)
    speed = int(race.get("speed", 30))
    if subrace_id:
        sub = db.get_subrace(race_id, subrace_id)
        if "speed" in sub:
            speed = int(sub.get("speed", speed))
    return speed


def _race_size(db: Dnd5eDatabase, race_id: str, _subrace_id: str) -> str:
    if not race_id:
        return "Medium"
    return str(db.get_race(race_id).get("size", "Medium"))


def _race_languages(db: Dnd5eDatabase, race_id: str, subrace_id: str) -> list[str]:
    if not race_id:
        return []
    out: list[str] = []
    race = db.get_race(race_id)
    for lang in race.get("languages") or []:
        if isinstance(lang, dict):
            name = str(lang.get("name", "")).strip()
            if name:
                out.append(name)
    if subrace_id:
        sub = db.get_subrace(race_id, subrace_id)
        for lang in sub.get("languages") or []:
            if isinstance(lang, dict):
                name = str(lang.get("name", "")).strip()
                if name and name not in out:
                    out.append(name)
    return out


def _class_skill_choice_hint(db: Dnd5eDatabase, class_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {"count": 0, "options": [], "option_skill_ids": []}
    if not class_id:
        return out
    cls = db.get_class_data(class_id)
    for pc in cls.get("proficiency_choices") or []:
        if not isinstance(pc, dict):
            continue
        desc = str(pc.get("desc", "")).lower()
        is_skills = "skill" in desc
        if not is_skills:
            from_dict = pc.get("from")
            if isinstance(from_dict, dict):
                opts = from_dict.get("options") or []
                if opts and isinstance(opts[0], dict):
                    item = (opts[0].get("item") or {}) if isinstance(opts[0], dict) else {}
                    url = str(item.get("url", ""))
                    is_skills = "/skills/" in url
        if not is_skills:
            continue
        out["count"] = int(pc.get("choose", 0))
        names: list[str] = []
        skill_ids: list[str] = []
        seen: set[str] = set()
        from_block = pc.get("from") or {}
        for opt in from_block.get("options") or []:
            if not isinstance(opt, dict):
                continue
            item = opt.get("item") or {}
            name = str(item.get("name", ""))
            if name.startswith("Skill: "):
                name = name[7:]
            sid = _skill_id_from_proficiency_item(item)
            if not sid and name:
                sid = _coerce_skill_slug_from_label(name)
            if sid and sid not in seen:
                seen.add(sid)
                skill_ids.append(sid)
            if name:
                names.append(name)
        out["options"] = names
        out["option_skill_ids"] = skill_ids
        break
    return out


def skill_on_class_skill_list(skill_id: str, hint: dict[str, Any]) -> bool:
    ids = hint.get("option_skill_ids") or []
    sid = str(skill_id).strip()
    if ids:
        return sid in ids
    for opt in hint.get("options") or []:
        if _coerce_skill_slug_from_label(str(opt)) == sid:
            return True
    return False


def _skill_id_from_proficiency_item(item: dict[str, Any]) -> str:
    idx = str(item.get("index", "")).lower().strip()
    if idx.startswith("skill-"):
        rest = idx[6:]
        if rest in SKILL_ABILITIES:
            return rest
    url = str(item.get("url", "")).lower()
    pos = url.rfind("/skill-")
    if pos != -1:
        slug = url[pos + 7 :].replace("skill-", "")
        if slug in SKILL_ABILITIES:
            return slug
    return ""


def _coerce_skill_slug_from_label(label: str) -> str:
    raw = str(label or "").strip()
    if not raw:
        return ""
    t = raw.lower().replace("’", "'")
    if t in SKILL_ABILITIES:
        return t
    hy = t.replace(" ", "-")
    if hy in SKILL_ABILITIES:
        return hy
    for key in SKILL_ABILITIES:
        if t == key.replace("-", " ") or hy == key:
            return key
    return ""


def _option_skill_ids_from_option_strings(opt_names: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in opt_names:
        sid = _coerce_skill_slug_from_label(name)
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _srd_mean_class_skill_choose(db: Dnd5eDatabase) -> int:
    classes = db.list_classes()
    if not classes:
        return 2
    total = 0
    count = 0
    for cls in classes:
        if not isinstance(cls, dict):
            continue
        class_id = str(cls.get("index", "")).strip()
        if not class_id:
            continue
        hint = _class_skill_choice_hint(db, class_id)
        cnt = int(hint.get("count", 0))
        if cnt > 0:
            total += cnt
            count += 1
    if count <= 0:
        return 2
    return round(total / count)


def _srd_mean_race_trait_skill_choose(db: Dnd5eDatabase) -> int:
    races = db.list_races()
    if not races:
        return 0
    total = 0
    count = 0
    for race in races:
        if not isinstance(race, dict):
            continue
        race_id = str(race.get("index", "")).strip()
        if not race_id:
            continue
        total += _race_trait_skill_choice_count(db, race_id, "")
        count += 1
    if count <= 0:
        return 0
    return round(total / count)


def _proficiency_choice_is_skill_only(pc: dict[str, Any]) -> bool:
    from_raw = pc.get("from")
    if not isinstance(from_raw, dict):
        return False
    opts = from_raw.get("options") or []
    if not opts:
        return False
    for opt in opts:
        if not isinstance(opt, dict):
            return False
        item = opt.get("item") or {}
        url = str(item.get("url", "")).lower()
        idx = str(item.get("index", "")).lower()
        name = str(item.get("name", ""))
        is_skill = idx.startswith("skill-") or "/proficiencies/skill-" in url or name.startswith("Skill: ")
        if not is_skill:
            return False
    return True


def _race_trait_skill_choice_count(db: Dnd5eDatabase, race_id: str, subrace_id: str) -> int:
    if not race_id:
        return 0
    seen: set[str] = set()
    total = 0
    for ref in db.list_traits_for(race_id, subrace_id):
        if not isinstance(ref, dict):
            continue
        tid = str(ref.get("index", "")).strip().lower()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        trait = db.get_trait(tid)
        pc_raw = trait.get("proficiency_choices")
        if not isinstance(pc_raw, dict):
            continue
        choose_n = int(pc_raw.get("choose", 0))
        if choose_n <= 0 or not _proficiency_choice_is_skill_only(pc_raw):
            continue
        total += choose_n
    return total
