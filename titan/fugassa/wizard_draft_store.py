"""Wizard draft persistence (global, outside save DB) — Fugassa II flat schema."""

from __future__ import annotations

import json
import os
from typing import Any

from titan.fugassa.paths import WIZARD_DRAFT_PATH, FUGASSA_ROOT
from titan.fugassa.wizard_draft_defaults import default_wizard_draft
from titan.fugassa.homebrew_normalize import flatten_homebrew_details


def _normalize_homebrew_details(hb: dict[str, Any] | None) -> dict[str, Any]:
    out = flatten_homebrew_details(hb)
    applied = out.get("racial_traits_applied")
    if isinstance(applied, dict) and out.get("skill_proficiency_bonus_race") is None:
        race_bonus = applied.get("skill_proficiency_bonus_race")
        if race_bonus is not None:
            out["skill_proficiency_bonus_race"] = race_bonus
    return out


def _normalize_draft(draft: dict[str, Any]) -> dict[str, Any]:
    hb = _normalize_homebrew_details(draft.get("homebrew_details"))
    if hb:
        draft["homebrew_details"] = hb
    return draft


# User selections must replace prior values — shallow merge resurrects unchecked skills.
_REPLACE_ON_PATCH_KEYS = frozenset({
    "skill_proficiencies",
    "expertise",
    "selected_spells_by_level",
    "selected_cantrips",
    "asi_choices",
    "homebrew_choices",
    "class_mechanic_choices",
})


def ensure_layout() -> None:
    os.makedirs(FUGASSA_ROOT, exist_ok=True)


def load() -> dict[str, Any]:
    ensure_layout()
    base = default_wizard_draft()
    try:
        with open(WIZARD_DRAFT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            merged = {**base, **data}
            # Deep-merge known nested dicts
            for key in (
                "abilities",
                "inventory_structured",
                "gear_structured",
                "opening_structured",
                "gm_guides_map",
                "gm_guides_builtin",
                "portrait_appearance",
                "skill_proficiencies",
                "expertise",
                "selected_spells_by_level",
                "selected_cantrips",
                "asi_choices",
                "homebrew_choices",
                "class_mechanic_choices",
                "homebrew_details",
                "sheet_snapshot",
            ):
                if isinstance(base.get(key), dict) and isinstance(data.get(key), dict):
                    merged[key] = {**base[key], **data[key]}
            return _normalize_draft(merged)
    except (OSError, json.JSONDecodeError):
        pass
    return _normalize_draft(dict(base))


def save(patch: dict[str, Any]) -> dict[str, Any]:
    ensure_layout()
    patch = {k: v for k, v in patch.items() if k not in {"image_styles", "_image_styles"}}
    current = load()
    merged = {**current, **patch}
    for key in (
        "abilities",
        "inventory_structured",
        "gear_structured",
        "opening_structured",
        "gm_guides_map",
        "gm_guides_builtin",
        "portrait_appearance",
        "skill_proficiencies",
        "expertise",
            "selected_spells_by_level",
            "selected_cantrips",
            "asi_choices",
            "homebrew_choices",
        "homebrew_choices",
        "homebrew_details",
        "sheet_snapshot",
    ):
        if key not in patch or not isinstance(patch.get(key), dict):
            continue
        if key in _REPLACE_ON_PATCH_KEYS:
            merged[key] = dict(patch[key])
        elif isinstance(current.get(key), dict):
            merged[key] = {**current[key], **patch[key]}
    merged = _normalize_draft(merged)
    with open(WIZARD_DRAFT_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return merged


def clear() -> dict[str, Any]:
    ensure_layout()
    try:
        os.remove(WIZARD_DRAFT_PATH)
    except OSError:
        pass
    return default_wizard_draft()


def is_resumable(draft: dict[str, Any] | None = None) -> bool:
    d = draft if draft is not None else load()
    if int(d.get("unlocked_tab") or 0) > 0:
        return True
    if str(d.get("world_name") or "").strip() and d.get("world_name") != "New Campaign":
        return True
    if str(d.get("world_information") or "").strip():
        return True
    if str(d.get("character_background") or "").strip():
        return True
    if str(d.get("player_name") or "").strip() and d.get("player_name") != "Hero":
        return True
    return False
