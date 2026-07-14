"""LLM + deterministic theme facet normalization for SD genre anchors."""

from __future__ import annotations

import logging
from typing import Any

from titan.fugassa import asset_prompts
from titan.fugassa.asset_prompts import KNOWN_THEME_FACETS, sanitize_theme_facets
from titan.fugassa import wizard_json as wj

log = logging.getLogger("titan.fugassa.theme_facets")

_PRESET_THEME_FACETS: dict[str, tuple[str, ...]] = {
    "fantasy": ("fantasy",),
    "sci-fi": ("sci_fi",),
    "sci fi": ("sci_fi",),
    "modern": ("modern",),
    "present": ("modern",),
    "cyberpunk": ("cyberpunk", "dystopian"),
    "horror": ("dark_fantasy",),
    "western": ("modern",),
}

_THEME_FACET_SYSTEM = (
    "You classify RPG campaign genres for Stable Diffusion image generation. "
    "Output JSON only — no markdown."
)

_THEME_FACET_USER = """\
Campaign theme mode: {theme_mode}
Resolved theme label: {theme}
World information (may be non-English):
{world_information}

Pick 1–3 genre facet IDs that best describe this campaign. Campaigns may combine
genres (e.g. dark fantasy + dystopian future). Use ONLY these facet IDs:
{facet_ids}

Return JSON:
{{"theme_facets": ["facet_id", ...], "theme_label_en": "short English genre label"}}

Rules:
- theme_facets must use only IDs from the list above.
- theme_label_en: 2–8 words in English summarizing the blend (for SD prompts).
- Interpret non-English text by meaning, not literal translation of words.
- If world information contradicts the theme label, include facets from both.
"""


def _preset_facets_for_theme(theme: str) -> list[str] | None:
    key = asset_prompts.normalize_theme_key(theme)
    if key in _PRESET_THEME_FACETS:
        return list(_PRESET_THEME_FACETS[key])
    return None


def detect_theme_facets_from_text(*texts: str) -> frozenset[str]:
    """Keyword facet detection across theme + world_information (any language hints)."""
    combined = " ".join(str(t or "") for t in texts if str(t or "").strip())
    return asset_prompts.detect_theme_facets(combined)


def resolve_theme_facets(
    theme: str,
    *,
    stored: list[str] | None = None,
    world_information: str = "",
) -> frozenset[str]:
    """Prefer wizard-persisted facets; fall back to keyword scan."""
    cleaned = sanitize_theme_facets(stored)
    if cleaned:
        return frozenset(cleaned)
    preset = _preset_facets_for_theme(theme)
    if preset and not str(world_information or "").strip():
        return frozenset(preset)
    found = detect_theme_facets_from_text(theme, world_information)
    if found:
        return found
    return frozenset({"fantasy"})


def theme_facets_from_world_profile(world_profile: dict[str, Any] | None) -> frozenset[str] | None:
    wp = world_profile if isinstance(world_profile, dict) else {}
    stored = sanitize_theme_facets(wp.get("theme_facets"))
    if stored:
        return frozenset(stored)
    return None


def theme_label_for_prompts(
    theme: str,
    world_profile: dict[str, Any] | None = None,
) -> str:
    """English SD label: stored theme_label_en beats raw theme string."""
    wp = world_profile if isinstance(world_profile, dict) else {}
    label_en = str(wp.get("theme_label_en") or "").strip()
    if label_en:
        return label_en
    return str(theme or "fantasy").strip() or "fantasy"


def apply_normalized_theme_to_draft(
    draft: dict[str, Any],
    *,
    theme_facets: list[str],
    theme_label_en: str,
) -> None:
    facets = sanitize_theme_facets(theme_facets)
    if facets:
        draft["theme_facets"] = facets
    label = str(theme_label_en or "").strip()
    if label:
        draft["theme_label_en"] = label


def apply_normalized_theme_to_world_profile(
    world_profile: dict[str, Any],
    *,
    theme_facets: list[str],
    theme_label_en: str = "",
) -> None:
    facets = sanitize_theme_facets(theme_facets)
    if facets:
        world_profile["theme_facets"] = facets
    label = str(theme_label_en or "").strip()
    if label:
        world_profile["theme_label_en"] = label


def ensure_theme_facets_in_state(state: dict[str, Any]) -> bool:
    """
    Lazy backfill for saves created before theme_facets existed.
    Uses keyword detection only (no LLM) — wizard-time normalization is preferred.
    """
    wp = dict(state.get("world_profile") or {})
    if sanitize_theme_facets(wp.get("theme_facets")):
        return False
    theme = str(wp.get("theme") or state.get("theme") or "fantasy")
    world_info = str(wp.get("world_information") or "")
    facets = resolve_theme_facets(theme, world_information=world_info)
    apply_normalized_theme_to_world_profile(
        wp,
        theme_facets=sorted(facets),
        theme_label_en=theme_label_for_prompts(theme, wp),
    )
    state["world_profile"] = wp
    return True


async def normalize_theme_facets_for_wizard(
    draft: dict[str, Any],
    *,
    theme: str,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    """
    LLM-normalize theme → English facet IDs for SD anchors.
    Falls back to presets/keywords when LLM is off or fails.
    """
    theme_mode = str(draft.get("theme_mode") or "Fantasy").strip()
    world_information = str(draft.get("world_information") or "").strip()[:2000]
    prestored = sanitize_theme_facets(draft.get("theme_facets"))
    if prestored:
        label = str(draft.get("theme_label_en") or theme).strip()
        return {"theme_facets": prestored, "theme_label_en": label, "source": "draft"}

    if not llm_enabled:
        facets = resolve_theme_facets(theme, world_information=world_information)
        return {
            "theme_facets": sorted(facets),
            "theme_label_en": theme_label_for_prompts(theme),
            "source": "keyword",
        }

    facet_list = ", ".join(sorted(KNOWN_THEME_FACETS))
    messages = [
        {"role": "system", "content": _THEME_FACET_SYSTEM},
        {
            "role": "user",
            "content": _THEME_FACET_USER.format(
                theme_mode=theme_mode,
                theme=theme,
                world_information=world_information or "(none)",
                facet_ids=facet_list,
            ),
        },
    ]
    try:
        from titan.fugassa.llm_client import chat_completion

        raw = await chat_completion(messages, owner=owner, max_tokens=256, temperature=0.2)
        data = wj.parse_wizard_json_object(raw) or {}
        facets = sanitize_theme_facets(data.get("theme_facets"))
        label = str(data.get("theme_label_en") or "").strip()
        if facets:
            if not label:
                label = theme_label_for_prompts(theme)
            return {"theme_facets": facets, "theme_label_en": label, "source": "llm"}
        log.warning("Theme facet LLM returned no valid facets: %s", raw[:200])
    except Exception as exc:  # noqa: BLE001
        log.warning("Theme facet LLM normalization failed, using keyword fallback: %s", exc)

    facets = resolve_theme_facets(theme, world_information=world_information)
    return {
        "theme_facets": sorted(facets),
        "theme_label_en": theme_label_for_prompts(theme),
        "source": "keyword",
    }
