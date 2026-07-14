"""Mid-campaign level-up: preview pending picks and apply to game state."""

from __future__ import annotations

from typing import Any

from titan.fugassa.class_mechanics import class_resource_display_lines
from titan.fugassa.dnd5e_character_builder import (
    build,
    draft_to_build_input,
    validate_sheet_input,
)
from titan.fugassa.dnd5e_options import CLASS_CHOICES, RACE_CHOICES, SUBCLASS_BY_CLASS, xp_level_progress, xp_to_next_for_level
from titan.fugassa.dnd5e_database import Dnd5eDatabase, get_dnd5e_database
from titan.fugassa.sheet_persistence import build_sheet_from_draft, sheet_to_game_json


def _choice_idx(choices: list[str], label: str) -> int | None:
    text = str(label or "").strip()
    if not text:
        return None
    for i, choice in enumerate(choices):
        if choice.lower() == text.lower():
            return i
    return None


def _class_id_to_label(class_id: str) -> str:
    slug = str(class_id or "").strip().lower()
    for choice in CLASS_CHOICES:
        if choice.lower().replace(" ", "-") == slug or choice.lower() == slug.replace("-", " "):
            return choice
    return slug.replace("-", " ").title()


def _ability_scores_from_stable(stable: dict[str, Any]) -> dict[str, int]:
    raw = stable.get("abilities") or {}
    out: dict[str, int] = {}
    long_to_short = {
        "strength": "str",
        "dexterity": "dex",
        "constitution": "con",
        "intelligence": "int",
        "wisdom": "wis",
        "charisma": "cha",
    }
    for key, val in raw.items():
        slug = long_to_short.get(str(key).lower(), str(key).lower()[:3])
        if slug in {"str", "dex", "con", "int", "wis", "cha"}:
            out[slug] = int(val)
    return out


def game_state_to_build_draft(state: dict[str, Any], *, target_level: int | None = None) -> dict[str, Any]:
    """Reconstruct a wizard-like draft from gameplay state for sheet rebuild."""
    cs = state.get("character_sheet") or {}
    stable = cs.get("stable_sheet") or {}
    identity = stable.get("identity") or {}
    computed = cs.get("computed") or {}
    resolved = computed.get("resolved") or {}
    snap = state.get("wizard_draft_snapshot") or {}
    party = state.get("party") or []
    hero = party[0] if party else {}

    level = int(target_level if target_level is not None else identity.get("level") or hero.get("level") or 1)
    class_label = str(identity.get("character_class") or hero.get("character_class") or "")
    race_label = str(identity.get("race") or hero.get("race") or "")
    labels = computed.get("labels") or {}
    pre_race = computed.get("abilities_pre_race") if isinstance(computed.get("abilities_pre_race"), dict) else {}

    draft: dict[str, Any] = {
        "player_name": str(identity.get("name") or hero.get("name") or "Hero"),
        "player_age": str(identity.get("age") or snap.get("player_age") or ""),
        "level": level,
        "abilities": {k: int(v) for k, v in pre_race.items()} if pre_race else (
            snap.get("abilities") or _ability_scores_from_stable(stable)
        ),
        "skill_proficiencies": snap.get("skill_proficiencies") or {},
        "expertise": snap.get("expertise") or {},
        "selected_cantrips": snap.get("selected_cantrips") or [],
        "selected_spells_by_level": snap.get("selected_spells_by_level") or {},
        "asi_choices": snap.get("asi_choices") or {},
        "homebrew_choices": snap.get("homebrew_choices") or {},
        "class_mechanic_choices": snap.get("class_mechanic_choices") or {},
        "homebrew_details": snap.get("homebrew_details") or {},
        "spell_list_class_id": computed.get("spell_list_class_id") or snap.get("spell_list_class_id") or "",
        "playstyle_framework": str(state.get("playstyle_framework") or "rules_based"),
        "rules_mode": str(state.get("rules_mode") or "5e-style"),
    }

    if snap.get("player_class_idx") is not None:
        draft["player_class_idx"] = int(snap["player_class_idx"])
    elif resolved.get("class_id"):
        cls_label = str(labels.get("class") or _class_id_to_label(resolved["class_id"]))
        idx = _choice_idx(CLASS_CHOICES, cls_label)
        if idx is not None:
            draft["player_class_idx"] = idx
        elif class_label:
            draft["player_class_custom"] = class_label
    elif class_label:
        idx = _choice_idx(CLASS_CHOICES, class_label)
        if idx is not None:
            draft["player_class_idx"] = idx
        else:
            draft["player_class_custom"] = class_label

    if snap.get("player_race_idx") is not None:
        draft["player_race_idx"] = int(snap["player_race_idx"])
    elif resolved.get("race_id"):
        race_from_id = str(labels.get("race") or _class_id_to_label(resolved["race_id"]))
        idx = _choice_idx(RACE_CHOICES, race_from_id)
        if idx is not None:
            draft["player_race_idx"] = idx
        elif race_label:
            draft["player_race_custom"] = race_label
    elif race_label:
        idx = _choice_idx(RACE_CHOICES, race_label)
        if idx is not None:
            draft["player_race_idx"] = idx
        else:
            draft["player_race_custom"] = race_label

    subclass = str(identity.get("subclass") or hero.get("subclass") or labels.get("subclass") or "")
    if snap.get("player_subclass_idx") is not None:
        draft["player_subclass_idx"] = int(snap["player_subclass_idx"])
    elif subclass:
        cls_for_sub = str(labels.get("class") or class_label or _class_id_to_label(resolved.get("class_id", "")))
        choices = SUBCLASS_BY_CLASS.get(cls_for_sub, [])
        idx = _choice_idx(choices, subclass)
        if idx is not None:
            draft["player_subclass_idx"] = idx
        else:
            draft["player_subclass_custom"] = subclass

    return draft


def level_up_eligible(state: dict[str, Any]) -> bool:
    cs = state.get("character_sheet") or {}
    stable = cs.get("stable_sheet") or {}
    identity = stable.get("identity") or {}
    party = state.get("party") or []
    hero = party[0] if party and isinstance(party[0], dict) else {}
    level = int(identity.get("level") or hero.get("level") or 1)
    if level >= 20:
        return False
    xp = int(hero.get("xp") or 0)
    return bool(xp_level_progress(xp, level).get("eligible"))


def level_up_preview(
    state: dict[str, Any],
    target_level: int,
    *,
    db: Dnd5eDatabase | None = None,
) -> dict[str, Any]:
    database = db or get_dnd5e_database()
    cs = state.get("character_sheet") or {}
    stable = cs.get("stable_sheet") or {}
    identity = stable.get("identity") or {}
    current_level = int(identity.get("level") or 1)
    target = max(1, min(int(target_level), 20))
    if target <= current_level:
        return {
            "ok": False,
            "errors": [f"Target level must be greater than current level ({current_level})."],
            "current_level": current_level,
            "target_level": target,
        }
    hero = (state.get("party") or [{}])[0] if isinstance(state.get("party"), list) else {}
    xp = int((hero if isinstance(hero, dict) else {}).get("xp") or 0)
    progress = xp_level_progress(xp, current_level)
    if not progress.get("eligible"):
        remaining = int(progress.get("remaining") or 0)
        return {
            "ok": False,
            "errors": [f"Need {remaining:,} more XP before leveling up."],
            "current_level": current_level,
            "target_level": target,
            "xp_progress": progress,
        }

    draft = game_state_to_build_draft(state, target_level=target)
    build_input = draft_to_build_input(draft)
    sheet = build(database, build_input)
    validation = validate_sheet_input(draft, db=database)

    return {
        "ok": True,
        "current_level": current_level,
        "target_level": target,
        "sheet": sheet,
        "validation_ok": validation["ok"],
        "validation_errors": validation["errors"],
        "class_mechanic_pickers": sheet.get("class_mechanic_pickers") or [],
        "asi_feat_levels_reached": sheet.get("asi_feat_levels_reached") or [],
        "class_resource_summary": sheet.get("class_resource_summary") or [],
        "hp_new": int(sheet.get("hp") or 0),
        "draft_fragment": {
            "level": target,
            "class_mechanic_choices": draft.get("class_mechanic_choices") or {},
            "asi_choices": draft.get("asi_choices") or {},
            "selected_cantrips": draft.get("selected_cantrips") or [],
            "selected_spells_by_level": draft.get("selected_spells_by_level") or {},
        },
    }


def level_up_apply(
    state: dict[str, Any],
    *,
    target_level: int,
    class_mechanic_choices: dict[str, Any] | None = None,
    asi_choices: dict[str, Any] | None = None,
    selected_cantrips: list[str] | None = None,
    selected_spells_by_level: dict[str, Any] | None = None,
    hp_current: int | None = None,
    db: Dnd5eDatabase | None = None,
) -> dict[str, Any]:
    database = db or get_dnd5e_database()
    preview = level_up_preview(state, target_level, db=database)
    if not preview.get("ok"):
        return preview

    draft = game_state_to_build_draft(state, target_level=target_level)
    if class_mechanic_choices is not None:
        draft["class_mechanic_choices"] = class_mechanic_choices
    if asi_choices is not None:
        draft["asi_choices"] = {**(draft.get("asi_choices") or {}), **asi_choices}
    if selected_cantrips is not None:
        draft["selected_cantrips"] = selected_cantrips
    if selected_spells_by_level is not None:
        draft["selected_spells_by_level"] = selected_spells_by_level

    validation = validate_sheet_input(draft, db=database)
    if not validation["ok"]:
        return {
            "ok": False,
            "errors": validation["errors"],
            "sheet": validation.get("sheet"),
        }

    sheet, build_input = build_sheet_from_draft(draft)
    cs = state.get("character_sheet") or {}
    stable = cs.get("stable_sheet") or {}
    identity = dict(stable.get("identity") or {})
    party = list(state.get("party") or [])
    hero = dict(party[0]) if party else {}
    current_level = int(preview.get("current_level") or identity.get("level") or hero.get("level") or 1)

    identity["level"] = target_level
    hero["level"] = target_level
    spent = xp_to_next_for_level(current_level)
    hero["xp"] = max(0, int(hero.get("xp") or 0) - spent)
    hero["xp_to_next"] = xp_to_next_for_level(target_level)
    if party:
        party[0] = hero

    weapon = (stable.get("inventory") or {}).get("weapon") or hero.get("weapon") or ""
    armor = (stable.get("inventory") or {}).get("armor") or hero.get("armor") or ""
    loc = (cs.get("volatile_state") or {}).get("location") or state.get("location") or ""
    new_hp = int(hp_current if hp_current is not None else sheet.get("hp") or hero.get("max_hp") or 1)

    rebuilt = sheet_to_game_json(
        sheet,
        build_input,
        identity=identity,
        weapon_name=str(weapon),
        armor_name=str(armor),
        loc_name=str(loc),
        hp_current=new_hp,
        inventory_notes=str((stable.get("inventory") or {}).get("notes") or ""),
    )
    rebuilt["computed"] = sheet

    snap = dict(state.get("wizard_draft_snapshot") or {})
    snap["class_mechanic_choices"] = draft.get("class_mechanic_choices") or {}
    snap["asi_choices"] = draft.get("asi_choices") or {}
    snap["selected_cantrips"] = draft.get("selected_cantrips") or []
    snap["selected_spells_by_level"] = draft.get("selected_spells_by_level") or {}
    for key in ("player_class_idx", "player_race_idx", "player_subclass_idx", "abilities", "spell_list_class_id"):
        if draft.get(key) is not None:
            snap[key] = draft[key]

    state["character_sheet"] = rebuilt
    state["party"] = party
    state["wizard_draft_snapshot"] = snap

    return {
        "ok": True,
        "errors": [],
        "state": state,
        "sheet": sheet,
        "class_resource_summary": class_resource_display_lines(sheet.get("class_resources") or {}),
    }
