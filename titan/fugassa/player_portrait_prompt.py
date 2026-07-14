"""Resolve player hero portrait prompts from SQL, wizard snapshot, or assets."""

from __future__ import annotations

import sqlite3
from typing import Any

from titan.fugassa.wizard_json import (
    PORTRAIT_SD_NEGATIVE_BASE,
    merge_portrait_sd_prompts,
    parse_portrait_sd_prompt_text,
)

# Mirrors static/js/fugassa/wizard/helpers.js PORTRAIT_ROW_OPTIONS / LABELS.
_PORTRAIT_ROW_OPTIONS: dict[str, list[str]] = {
    "height": ["—", "Very short", "Short", "Average height", "Tall", "Very tall", "Custom"],
    "build": ["—", "Slender", "Average", "Athletic", "Stocky", "Heavyset", "Custom"],
    "muscle": ["—", "Low muscle tone", "Average muscle", "Well-defined", "Very muscular", "Custom"],
    "hair_style": [
        "—",
        "Straight",
        "Wavy",
        "Curly",
        "Coily",
        "Braided",
        "Dreadlocks",
        "Ponytail",
        "Bun / updo",
        "Mohawk",
        "Slicked back",
        "Messy / tousled",
        "Shaved sides / undercut",
        "Custom",
    ],
    "hair_length": ["—", "Bald / shaved", "Buzz cut", "Short", "Shoulder-length", "Mid-back", "Waist+", "Custom"],
    "hair_color": [
        "—",
        "Black",
        "Dark brown",
        "Brown",
        "Auburn / red",
        "Blonde",
        "Grey / white",
        "Dyed / unnatural",
        "Custom",
    ],
    "skin_tone": [
        "—",
        "Very fair",
        "Fair",
        "Medium",
        "Olive",
        "Brown",
        "Dark brown",
        "Fantasy tint (blue/green/etc.)",
        "Custom",
    ],
    "facial_hair": [
        "—",
        "Clean-shaven",
        "Light stubble",
        "Heavy stubble",
        "Short beard",
        "Full beard",
        "Goatee",
        "Moustache only",
        "Sideburns",
        "Custom",
    ],
    "accessories": [
        "—",
        "None notable",
        "Glasses",
        "Jewelry",
        "Scars / tattoos",
        "Hat / hood",
        "Tech / cyber detail",
        "Custom",
    ],
    "ethnic_appearance": [
        "—",
        "East Asian",
        "South Asian",
        "African",
        "Middle Eastern",
        "European",
        "Latin",
        "Pacific / Indigenous",
        "Mixed / ambiguous",
        "Custom",
    ],
}
_PORTRAIT_ROW_LABELS: dict[str, str] = {
    "height": "Height",
    "build": "Build",
    "muscle": "Muscle",
    "hair_style": "Hair style",
    "hair_length": "Hair length",
    "hair_color": "Hair color",
    "skin_tone": "Skin tone",
    "facial_hair": "Facial hair",
    "accessories": "Accessories",
    "ethnic_appearance": "Ethnic / regional look",
}


def portrait_appearance_to_text(appearance: dict[str, Any] | None) -> str:
    """Convert wizard portrait_appearance rows to multiline text (JS portraitAppearanceToText)."""
    root = appearance if isinstance(appearance, dict) else {}
    rows = root.get("rows") if isinstance(root.get("rows"), dict) else {}
    lines: list[str] = []
    for key, options in _PORTRAIT_ROW_OPTIONS.items():
        row = rows.get(key) if isinstance(rows.get(key), dict) else {}
        idx = max(0, min(len(options) - 1, int(row.get("i") or 0)))
        if idx == 0:
            continue
        label = _PORTRAIT_ROW_LABELS.get(key, key)
        if idx == len(options) - 1:
            custom = str(row.get("t") or "").strip()
            if custom:
                lines.append(f"{label}: {custom}")
            continue
        lines.append(f"{label}: {options[idx]}")
    notes = str(root.get("notes") or "").strip()
    if notes:
        lines.append(f"Player notes: {notes}")
    return "\n".join(lines)


def format_portrait_sd_prompt_text(positive: str, negative: str = "") -> str:
    pos = str(positive or "").strip()
    neg = str(negative or "").strip()
    if not pos:
        return ""
    parts = [f"Positive\n{pos}"]
    if neg:
        parts.append(f"Negative\n{neg}")
    return "\n\n".join(parts)


def _hero_profile_from_party(game_state: dict[str, Any]) -> str:
    party = game_state.get("party") or []
    hero = party[0] if party and isinstance(party[0], dict) else {}
    bits = [
        str(hero.get("race") or "").strip(),
        str(hero.get("character_class") or "").strip(),
        f"age {hero.get('age')}".strip() if hero.get("age") else "",
        str(hero.get("gender") or "").strip(),
    ]
    return ", ".join(b for b in bits if b)


def _hero_profile_from_draft(draft: dict[str, Any]) -> str:
    bits = [
        str(draft.get("player_race_custom") or "").strip(),
        str(draft.get("player_class_custom") or "").strip(),
        f"age {draft.get('player_age')}".strip() if draft.get("player_age") else "",
    ]
    return ", ".join(b for b in bits if b)


def _campaign_style(*, draft: dict[str, Any] | None, game_state: dict[str, Any] | None) -> tuple[str, str, str]:
    wp = (game_state or {}).get("world_profile") if isinstance(game_state, dict) else {}
    wp = wp if isinstance(wp, dict) else {}
    theme = str(wp.get("theme") or "").strip()
    style = str(wp.get("image_style") or "").strip()
    name = ""
    if isinstance(game_state, dict):
        party = game_state.get("party") or []
        if party and isinstance(party[0], dict):
            name = str(party[0].get("name") or "").strip()
    if isinstance(draft, dict):
        from titan.fugassa.game_bootstrap import resolve_theme

        if not theme:
            theme = resolve_theme(draft)
        if not style:
            style = str(draft.get("image_style") or "").strip() or theme
        if not name:
            name = str(draft.get("player_name") or "").strip()
    return name or "Hero", theme or "fantasy", style or theme or "fantasy"


def synthesize_portrait_prompts(
    *,
    player_name: str,
    theme: str,
    style: str,
    appearance: dict[str, Any] | None,
    character_profile: str = "",
) -> tuple[str, str]:
    """Deterministic SD prompts from wizard appearance rows + sheet facts."""
    appearance_text = portrait_appearance_to_text(appearance)
    if not appearance_text and not character_profile:
        return "", ""
    subject_parts = [
        character_profile.replace("\n", ", ").strip(),
        appearance_text.replace("\n", ", ").strip(),
    ]
    subject = ", ".join(p for p in subject_parts if p)
    merged = merge_portrait_sd_prompts(theme, player_name, style, subject, "")
    return str(merged.get("positive_prompt") or "").strip(), str(merged.get("negative_prompt") or "").strip()


def _prompts_from_stored_wizard_text(game_state: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(game_state, dict):
        return "", ""
    snap = game_state.get("wizard_draft_snapshot")
    if not isinstance(snap, dict):
        snap = {}
    appearance = snap.get("portrait_appearance")
    if isinstance(appearance, dict):
        pos = str(appearance.get("positive_prompt") or appearance.get("prompt") or "").strip()
        neg = str(appearance.get("negative_prompt") or "").strip()
        if pos:
            return pos, neg or PORTRAIT_SD_NEGATIVE_BASE
    raw = str(game_state.get("portrait_sd_prompt_text") or snap.get("portrait_sd_prompt_text") or "").strip()
    if not raw:
        return "", ""
    parsed = parse_portrait_sd_prompt_text(raw)
    pos = str(parsed.get("positive_prompt") or "").strip()
    neg = str(parsed.get("negative_prompt") or "").strip()
    return pos, neg or PORTRAIT_SD_NEGATIVE_BASE


def resolve_portrait_prompts_from_sources(
    *,
    draft: dict[str, Any] | None = None,
    game_state: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Return (positive, negative, combined portrait_sd_prompt_text)."""
    pos, neg = _prompts_from_stored_wizard_text(game_state)
    combined = ""
    if pos:
        raw = ""
        if isinstance(game_state, dict):
            snap = game_state.get("wizard_draft_snapshot")
            snap = snap if isinstance(snap, dict) else {}
            raw = str(game_state.get("portrait_sd_prompt_text") or snap.get("portrait_sd_prompt_text") or "").strip()
        if raw:
            combined = raw
        else:
            combined = format_portrait_sd_prompt_text(pos, neg)
        return pos, neg, combined

    if isinstance(draft, dict):
        raw = str(draft.get("portrait_sd_prompt_text") or "").strip()
        if raw:
            parsed = parse_portrait_sd_prompt_text(raw)
            pos = str(parsed.get("positive_prompt") or "").strip()
            neg = str(parsed.get("negative_prompt") or "").strip()
            if pos:
                return pos, neg, raw

    appearance = None
    if isinstance(game_state, dict):
        snap = game_state.get("wizard_draft_snapshot")
        if isinstance(snap, dict) and isinstance(snap.get("portrait_appearance"), dict):
            appearance = snap.get("portrait_appearance")
    if appearance is None and isinstance(draft, dict) and isinstance(draft.get("portrait_appearance"), dict):
        appearance = draft.get("portrait_appearance")

    name, theme, style = _campaign_style(draft=draft, game_state=game_state)
    profile = _hero_profile_from_party(game_state) if isinstance(game_state, dict) else ""
    if not profile and isinstance(draft, dict):
        profile = _hero_profile_from_draft(draft)
    pos, neg = synthesize_portrait_prompts(
        player_name=name,
        theme=theme,
        style=style,
        appearance=appearance,
        character_profile=profile,
    )
    combined = format_portrait_sd_prompt_text(pos, neg)
    return pos, neg, combined


def prompt_from_wizard_state(game_state: dict[str, Any] | None) -> tuple[str, str]:
    pos, neg = _prompts_from_stored_wizard_text(game_state)
    if pos:
        return pos, neg
    pos, neg, _combined = resolve_portrait_prompts_from_sources(game_state=game_state)
    return pos, neg


def is_generic_auto_portrait_prompt(text: str) -> bool:
    t = str(text or "").lower()
    return "rpg character art" in t and "waist-up" in t


def resolve_player_portrait_prompt(
    db_path: str,
    player_character_id: int,
    game_state: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Best available portrait prompt for the hero (wizard text beats generic assets)."""
    stored_pos = ""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT portrait_prompt FROM player_characters WHERE id = ?",
            (int(player_character_id),),
        ).fetchone()
        if row:
            stored_pos = str(row["portrait_prompt"] or "").strip()
    finally:
        conn.close()

    wizard_pos, wizard_neg = prompt_from_wizard_state(game_state)
    if wizard_pos and stored_pos and is_generic_auto_portrait_prompt(stored_pos):
        return wizard_pos, wizard_neg
    if stored_pos:
        return stored_pos, wizard_neg or PORTRAIT_SD_NEGATIVE_BASE
    if wizard_pos:
        return wizard_pos, wizard_neg or PORTRAIT_SD_NEGATIVE_BASE

    from titan.fugassa.db import asset_repository

    active = asset_repository.get_active_asset(
        db_path,
        entity_type="player_character",
        entity_id=int(player_character_id),
        asset_type="portrait",
    )
    if active:
        pos = str(active.get("prompt") or "").strip()
        neg = str(active.get("negative_prompt") or "").strip()
        return pos, neg or PORTRAIT_SD_NEGATIVE_BASE
    return "", ""
