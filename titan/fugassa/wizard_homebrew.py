"""LLM homebrew character sheet sketch — port of Fugassa-II Main.gd iteration 5."""

from __future__ import annotations

from typing import Any

from titan.fugassa import wizard_json as wj
from titan.fugassa.dnd5e_character_builder import build, class_id_from_label, draft_to_build_input
from titan.fugassa.dnd5e_database import get_dnd5e_database
from titan.fugassa.dnd5e_options import CLASS_CHOICES, effective_class, effective_race, effective_subclass

HOMEBREW_SYSTEM_PROMPT = (
    "CRITICAL OUTPUT: Your entire reply must be ONE JSON object. The first character MUST be '{'.\n"
    "Do not output markdown fences, 'Thinking Process', numbered analysis,\n"
    "planning paragraphs, or any characters before '{' or after the final '}'.\n\n"
    "You are a Dungeons & Dragons 5th Edition homebrew designer.\n"
    "Given a non-SRD class and/or race at a specific level, produce a\n"
    "compact but complete JSON sheet sketch. Respond with ONLY that\n"
    "JSON object — no other text.\n"
    "Required keys when applicable: hit_die (int), saving_throw_profs\n"
    "(array of 'str'/'dex'/…), skill_proficiency_options (array of\n"
    "strings), optional skill_proficiency_choose (int),\n"
    "optional skill_proficiency_bonus_race (int),\n"
    "class_features (array of {name, level:int, desc}),\n"
    "subclass_features (same shape), racial_traits (array of\n"
    "{name, desc}), ability_bonuses_race ({str:+1,…}), speed (int),\n"
    "size (string), languages (array of strings), spellcasting\n"
    "({has:bool, ability:'int'|'wis'|'cha', model:'known'|'prepared',\n"
    "cantrips_known:int, spells_known:int, spells_prepared_estimate:int,\n"
    "slots_by_level:{'1':int,…}}), class_resources (arbitrary\n"
    "key/value pairs — for artificer templates include infusions_known:int),\n"
    "optional spell_catalog (array of {name, level:int,\n"
    "desc, school, range, casting_time, duration, components, ritual, concentration}).\n"
    "When spellcasting.has is true and cantrips_known > 0, spell_catalog MUST include\n"
    "at least cantrips_known cantrips at level 0 (not only leveled spells).\n"
    "Cantrips must match the mechanical template class spell list — do not add\n"
    "class-exclusive cantrips from other lists (e.g. no Eldritch Blast unless template is Warlock).\n"
    "Scale the content to the requested level. Omit keys that don't apply.\n"
    "If the user gives a Mechanical template / inspiration class, mirror that\n"
    "class's spell-slot rules, spells-known vs prepared pacing, cantrip counts,\n"
    "and Warlock-like pact cadence even when the flavour name differs."
)


from titan.fugassa.homebrew_normalize import flatten_homebrew_details


def homebrew_sheet_response_format() -> dict[str, Any]:
    class_feature_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "level", "desc"],
        "properties": {
            "name": {"type": "string"},
            "level": {"type": "integer"},
            "desc": {"type": "string"},
        },
    }
    racial_trait_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "desc"],
        "properties": {"name": {"type": "string"}, "desc": {"type": "string"}},
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "dnd5e_homebrew_sheet",
            "strict": False,
            "schema": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "hit_die": {"type": "integer"},
                    "saving_throw_profs": {"type": "array", "items": {"type": "string"}},
                    "skill_proficiency_options": {"type": "array", "items": {"type": "string"}},
                    "skill_proficiency_choose": {"type": "integer"},
                    "skill_proficiency_bonus_race": {"type": "integer"},
                    "ability_bonuses_race": {"type": "object"},
                    "class_features": {"type": "array", "items": class_feature_item},
                    "subclass_features": {"type": "array", "items": class_feature_item},
                    "racial_traits": {"type": "array", "items": racial_trait_item},
                    "speed": {"type": "integer"},
                    "size": {"type": "string"},
                    "languages": {"type": "array", "items": {"type": "string"}},
                    "spellcasting": {"type": "object"},
                    "class_resources": {"type": "object"},
                    "spell_catalog": {"type": "array"},
                },
            },
        },
    }


def mechanical_template_class(draft: dict[str, Any]) -> str:
    pick = int(draft.get("homebrew_llm_template_pick") or 0)
    if pick <= 0:
        return effective_class(draft)
    idx = pick - 1
    if 0 <= idx < len(CLASS_CHOICES):
        return CLASS_CHOICES[idx]
    return effective_class(draft)


def resolve_spell_list_class_id(draft: dict[str, Any]) -> str:
    db = get_dnd5e_database()
    class_label = effective_class(draft)
    cid = class_id_from_label(db, class_label)
    if cid:
        return cid
    tmpl = mechanical_template_class(draft)
    return class_id_from_label(db, tmpl)


def build_homebrew_user_prompt(draft: dict[str, Any], sheet_sync_note: str = "") -> str:
    build_in = draft_to_build_input(draft)
    build_in["homebrew_details"] = {}
    build_in["spell_list_class_id"] = resolve_spell_list_class_id(draft)
    sheet = build(get_dnd5e_database(), build_in)
    abilities = draft.get("abilities") or {}
    lines = [
        "Generate a D&D 5e homebrew sheet sketch for:",
        f"- Class: {effective_class(draft) or '(unspecified)'}",
    ]
    sub = effective_subclass(draft)
    if sub:
        lines.append(f"- Subclass: {sub}")
    lines.append(f"- Race: {effective_race(draft) or '(unspecified)'}")
    subrace = str(draft.get("player_subrace_custom") or "").strip()
    if not subrace and isinstance(draft.get("_subrace_options"), list):
        idx = int(draft.get("player_subrace_idx") or 0)
        opts = draft["_subrace_options"]
        if 0 <= idx < len(opts):
            subrace = str(opts[idx])
    if subrace:
        lines.append(f"- Subrace: {subrace}")
    lines.append(f"- Level: {int(draft.get('level') or 1)}")
    tmpl = mechanical_template_class(draft) or "(infer from class pacing)"
    lines.append(f"- Mechanical template / inspiration class: {tmpl}")
    ab_parts = [f"{k.upper()} {int(abilities.get(k, 10))}" for k in ("str", "dex", "con", "int", "wis", "cha")]
    lines.append(f"- Abilities (before racial bonuses): {', '.join(ab_parts)}")
    lines.append("")
    lines.append("CRITICAL: Start your reply with '{' immediately. One JSON object only.")
    if sheet_sync_note.strip():
        lines.extend(["", "--- Live sheet sync ---", sheet_sync_note.strip()])
    elif sheet.get("is_homebrew_race") is False and sheet.get("resolved", {}).get("race_id"):
        lines.extend(["", "--- SRD race resolved; focus homebrew on class mechanics. ---"])
    return "\n".join(lines)


def normalize_homebrew_payload(parsed: dict[str, Any], *, level: int, mods: dict[str, int]) -> dict[str, Any]:
    out = flatten_homebrew_details(parsed)
    if out.get("skill_proficiency_choose") is None and out.get("optional_skill_proficiency_choose") is not None:
        out["skill_proficiency_choose"] = out["optional_skill_proficiency_choose"]
    applied = out.get("racial_traits_applied")
    if isinstance(applied, dict) and not out.get("racial_traits"):
        traits = applied.get("racial_traits")
        if isinstance(traits, list):
            out["racial_traits"] = traits
        bonuses = applied.get("ability_bonuses_race")
        if isinstance(bonuses, dict) and not out.get("ability_bonuses_race"):
            out["ability_bonuses_race"] = bonuses
        if applied.get("speed") is not None and out.get("speed") is None:
            out["speed"] = applied["speed"]
        if applied.get("size") and not out.get("size"):
            out["size"] = applied["size"]
        if isinstance(applied.get("languages"), list) and not out.get("languages"):
            out["languages"] = applied["languages"]
        if applied.get("skill_proficiency_bonus_race") is not None and out.get("skill_proficiency_bonus_race") is None:
            out["skill_proficiency_bonus_race"] = applied["skill_proficiency_bonus_race"]
    sc = out.get("spellcasting")
    if isinstance(sc, dict) and str(sc.get("model", "")) == "prepared":
        if int(sc.get("spells_prepared_estimate", 0)) <= 0:
            ab_slug = str(sc.get("ability", "int")).lower()
            if ab_slug not in ("str", "dex", "con", "int", "wis", "cha"):
                ab_slug = "int"
            sc["spells_prepared_estimate"] = max(1, level + int(mods.get(ab_slug, 0)))
            out["spellcasting"] = sc
    return out
