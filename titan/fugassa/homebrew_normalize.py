"""Flatten nested LLM homebrew payloads to the schema `build()` expects."""

from __future__ import annotations

from typing import Any

_CLASS_HOIST_KEYS = (
    "hit_die",
    "saving_throw_profs",
    "skill_proficiency_options",
    "skill_proficiency_choose",
    "optional_skill_proficiency_choose",
    "class_features",
    "subclass_features",
    "spellcasting",
    "class_resources",
    "class_name",
)

_RACE_HOIST_KEYS = (
    "racial_traits",
    "ability_bonuses_race",
    "speed",
    "size",
    "languages",
    "skill_proficiency_bonus_race",
    "race_name",
)


def _is_empty_homebrew_value(val: Any) -> bool:
    return val is None or val == "" or val == [] or val == {}


def flatten_homebrew_details(hb: dict[str, Any] | None) -> dict[str, Any]:
    """Hoist nested LLM `class` / `race` blobs to flat keys."""
    if not isinstance(hb, dict):
        return {}
    out = {k: v for k, v in hb.items() if not str(k).startswith("_")}

    class_blob = out.pop("class", None)
    if isinstance(class_blob, dict):
        for key in _CLASS_HOIST_KEYS:
            if key in class_blob and not _is_empty_homebrew_value(class_blob[key]):
                out[key] = class_blob[key]
        if class_blob.get("name") and _is_empty_homebrew_value(out.get("class_name")):
            out["class_name"] = class_blob["name"]

    race_blob = out.pop("race", None)
    if isinstance(race_blob, dict):
        for key in _RACE_HOIST_KEYS:
            if key in race_blob and not _is_empty_homebrew_value(race_blob[key]):
                out[key] = race_blob[key]
        if race_blob.get("name") and _is_empty_homebrew_value(out.get("race_name")):
            out["race_name"] = race_blob["name"]

    if out.get("skill_proficiency_choose") is None and out.get("optional_skill_proficiency_choose") is not None:
        out["skill_proficiency_choose"] = out["optional_skill_proficiency_choose"]
    return out
