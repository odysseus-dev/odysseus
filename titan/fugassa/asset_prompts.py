"""Deterministic SD prompt templates — ADR §L4."""

from __future__ import annotations

import re
from typing import Any


_DEFAULT_NEGATIVE = (
    "blurry, low quality, bad anatomy, watermark, signature, text overlay, "
    "modern, UI, crowd, duplicate"
)


def default_negative(campaign_negative: str | None = None) -> str:
    extra = str(campaign_negative or "").strip()
    return f"{_DEFAULT_NEGATIVE}, {extra}" if extra else _DEFAULT_NEGATIVE


def normalize_theme_key(theme: str) -> str:
    return re.sub(r"\s+", " ", str(theme or "fantasy").strip().lower())


# Facet detection — campaigns may combine genres (e.g. dark fantasy + dystopian future).
_FACET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "dark_fantasy": (
        "dark fantasy", "gothic fantasy", "grimdark",
        "temná fantasy", "temná fantazie", "gotick", "grimdark",
    ),
    "fantasy": ("high fantasy", "medieval fantasy", "středověk"),
    "dystopian": (
        "dystop", "dystopian", "post-apocalyptic", "post apocalyptic",
        "dystopick", "postapokalypt",
    ),
    "sci_fi": (
        "sci-fi", "sci fi", "science fiction", "futuristic",
        "vědecko", "futuristick",
    ),
    "cyberpunk": ("cyberpunk", "neon-noir", "neon noir", "kyberpunk"),
    "modern": ("modern", "present day", "contemporary", "slice of life", "současnost", "moderní"),
}

_FACET_ANCHOR: dict[str, str] = {
    "dark_fantasy": "dark fantasy, gothic arcane atmosphere, worn robes, leather, torchlit shadows",
    "fantasy": "high fantasy, medieval fantasy elements, cloaks and tunics",
    "dystopian": "dystopian future, industrial decay, grim urban ruins, authoritarian mood",
    "sci_fi": "science fiction, futuristic technology, cinematic sci-fi environment",
    "cyberpunk": "cyberpunk accents, neon spill, tech-worn dystopia",
    "modern": "contemporary setting, present-day urban environment",
}

_HYBRID_ANCHORS: dict[frozenset[str], str] = {
    frozenset({"dark_fantasy", "dystopian"}): (
        "dark fantasy dystopian blend, gothic arcane meets industrial future, ornate decay, "
        "rusted megastructures, torchlight and neon spill, fantasy clothing with dystopian tech-wear"
    ),
    frozenset({"dark_fantasy", "sci_fi"}): (
        "dark fantasy science-fiction blend, arcane horror with futuristic ruins, "
        "gothic architecture and advanced decayed technology"
    ),
    frozenset({"fantasy", "dystopian"}): (
        "fantasy dystopian blend, medieval motifs in ruined future cityscape, "
        "magical atmosphere with industrial collapse"
    ),
    frozenset({"dystopian", "cyberpunk"}): (
        "dystopian cyberpunk, neon-lit urban decay, authoritarian megacity, grim future"
    ),
}

_UNIVERSAL_SCENE_NEG = (
    "tie, necktie, business suit, corporate office attire, stock photo portrait, plain office worker"
)

KNOWN_THEME_FACETS = frozenset(_FACET_KEYWORDS.keys())


def sanitize_theme_facets(raw: Any) -> list[str]:
    """Keep known facet IDs in stable order."""
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(",")]
    if not isinstance(raw, (list, tuple, frozenset, set)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        facet = str(item or "").strip().lower().replace("-", "_").replace(" ", "_")
        if facet in KNOWN_THEME_FACETS and facet not in seen:
            seen.add(facet)
            out.append(facet)
    return out


def detect_theme_facets(theme: str, *, stored: list[str] | None = None) -> frozenset[str]:
    cleaned = sanitize_theme_facets(stored)
    if cleaned:
        return frozenset(cleaned)
    key = normalize_theme_key(theme)
    found: set[str] = set()
    for facet, keywords in _FACET_KEYWORDS.items():
        if facet == "fantasy":
            continue
        if any(kw in key for kw in keywords):
            found.add(facet)
    if "fantasy" in key and "dark_fantasy" not in found:
        found.add("fantasy")
    if not found:
        found.add("fantasy")
    return frozenset(found)


def resolve_scene_theme_facets(
    theme: str,
    *,
    world_profile: dict[str, Any] | None = None,
) -> frozenset[str]:
    """Prefer persisted wizard facets; fall back to keyword detection."""
    from titan.fugassa.theme_facet_engine import resolve_theme_facets

    wp = world_profile if isinstance(world_profile, dict) else {}
    stored = wp.get("theme_facets")
    world_info = str(wp.get("world_information") or "")
    return resolve_theme_facets(
        theme,
        stored=stored if isinstance(stored, list) else None,
        world_information=world_info,
    )


def scene_theme_bundle(
    theme: str,
    world_profile: dict[str, Any] | None = None,
) -> tuple[str, frozenset[str]]:
    """English SD label + facet set from world_profile (wizard-normalized when present)."""
    from titan.fugassa.theme_facet_engine import theme_label_for_prompts

    wp = world_profile if isinstance(world_profile, dict) else {}
    return theme_label_for_prompts(theme, wp), resolve_scene_theme_facets(theme, world_profile=wp)


def _hybrid_anchor(facets: frozenset[str]) -> str | None:
    best: str | None = None
    best_len = 0
    for hybrid_key, anchor in _HYBRID_ANCHORS.items():
        if hybrid_key.issubset(facets) and len(hybrid_key) > best_len:
            best = anchor
            best_len = len(hybrid_key)
    return best


def theme_scene_positive_anchor(
    theme: str,
    *,
    style_hint: str = "",
    facets: frozenset[str] | None = None,
    theme_label: str | None = None,
) -> str:
    facets = facets or detect_theme_facets(theme)
    anchor = _hybrid_anchor(facets)
    if not anchor:
        anchor = ", ".join(_FACET_ANCHOR[f] for f in sorted(facets) if f in _FACET_ANCHOR)
    label = normalize_theme_key(theme_label or theme)
    parts: list[str] = []
    if label and label not in anchor.lower():
        parts.append(label)
    hint = str(style_hint or "").strip()
    if hint and hint.lower() not in label and hint.lower() not in anchor.lower():
        parts.append(hint)
    if anchor:
        parts.append(anchor)
    return ", ".join(p for p in parts if p)


def theme_scene_negative_extras(theme: str, *, facets: frozenset[str] | None = None) -> str:
    facets = facets or detect_theme_facets(theme)
    neg_terms = {t.strip() for t in _UNIVERSAL_SCENE_NEG.split(",") if t.strip()}
    if "modern" not in facets:
        neg_terms.update({"contemporary fashion", "smartphone", "jeans and t-shirt"})
    if "dystopian" not in facets and "sci_fi" not in facets and "cyberpunk" not in facets:
        neg_terms.update({"cyberpunk city", "neon skyscrapers", "generic sci-fi cityscape"})
    if "fantasy" not in facets and "dark_fantasy" not in facets:
        neg_terms.update({"medieval castle only", "fantasy armor only", "magic sparkles only"})
    return ", ".join(sorted(neg_terms))


def apply_theme_to_scene_prompt(
    prompt: str,
    theme: str,
    *,
    style_hint: str = "",
    facets: frozenset[str] | None = None,
    theme_label: str | None = None,
) -> str:
    """Front-load genre anchor so CLIP + checkpoint stay on-campaign."""
    anchor = theme_scene_positive_anchor(
        theme, style_hint=style_hint, facets=facets, theme_label=theme_label,
    )
    if not anchor:
        return str(prompt or "").strip()[:500]
    s = re.sub(r"\s+", " ", str(prompt or "").strip())
    low = s.lower()
    label = normalize_theme_key(theme)
    if label and label in low and any(
        tok in low for tok in ("gothic", "dystopian", "medieval", "cyberpunk", "futuristic")
    ):
        return s[:500]
    if label and label in low and any(
        tok.strip() in low for tok in anchor.split(",")[:2] if tok.strip()
    ):
        return s[:500]
    return f"{anchor}, {s}".strip(" ,")[:500]


def merge_scene_theme_negative(
    negative: str,
    theme: str,
    *,
    facets: frozenset[str] | None = None,
) -> str:
    extra = theme_scene_negative_extras(theme, facets=facets)
    if not extra:
        return str(negative or "").strip()
    base = str(negative or "").strip()
    seen = {t.strip().lower() for t in base.split(",") if t.strip()}
    additions = []
    for term in extra.split(","):
        t = term.strip()
        if t and t.lower() not in seen:
            additions.append(t)
            seen.add(t.lower())
    if not additions:
        return base
    return base + (", " + ", ".join(additions) if base else ", ".join(additions))


def prose_to_tags(text: str, *, max_chars: int = 480) -> str:
    """Turn prose location notes into comma-separated SD tags."""
    s = re.sub(r"\s+", " ", str(text or "").strip())
    if not s:
        return ""
    if s.count(",") >= 2:
        return s[:max_chars]
    s = re.sub(r"[.;]\s+", ", ", s)
    s = re.sub(r"\s+and\s+", ", ", s, flags=re.IGNORECASE)
    parts = [p.strip(" .") for p in s.split(",") if p.strip(" .")]
    return ", ".join(parts)[:max_chars]


def normalize_tag_prompt(text: str, *, max_chars: int = 900) -> str:
    """Ensure scene prompts stay tag-like for SD backends."""
    s = str(text or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    if s.count(",") >= 2:
        return s[:max_chars]
    return prose_to_tags(s, max_chars=max_chars)


_PORTRAIT_COMPOSITION_LEAK = re.compile(
    r"\b(?:waist-up portrait|single character(?: portrait)?|head and shoulders|"
    r"close-up portrait|portrait shot|solo portrait|character portrait)\b",
    re.IGNORECASE,
)

_NSFW_LEAK = re.compile(
    r"\b(?:nude|naked|bare(?:-|\s)?breasts?|nipples?|areola|genitals?|pussy|cock|penis|"
    r"sex|sexual|erotic|porn|explicit|topless)\b",
    re.IGNORECASE,
)


def _allow_nsfw() -> bool:
    """
    Explicit scenes are allowed by default in Fugassa.
    Set FUGASSA_BLOCK_NSFW_SCENES=1 to force PG-13 prompts.
    """
    return str(__import__("os").environ.get("FUGASSA_BLOCK_NSFW_SCENES", "")).strip().lower() not in ("1", "true", "yes")


def sanitize_scene_action_for_image(text: str) -> str:
    """Normalize whitespace; optionally redact explicit terms when blocking is enabled."""
    s = re.sub(r"\s+", " ", str(text or "").strip())
    if not s:
        return ""
    if not _allow_nsfw():
        s = _NSFW_LEAK.sub("", s)
    return s.strip(" ,")[:600]


def sanitize_scene_generation_prompt(
    prompt: str,
    *,
    cast_count: int = 1,
    has_hero: bool = True,
    supporting_count: int = 0,
) -> str:
    """
    SDXL CLIP reads ~77 tokens — front-load hero + composition; drop portrait framing.

    Chat scenes: player hero is the focal subject; location is backdrop; NPCs stay
    visually separate and subordinate.
    """
    s = re.sub(r"\s+", " ", str(prompt or "").strip())
    s = _PORTRAIT_COMPOSITION_LEAK.sub("", s)
    if not _allow_nsfw():
        s = _NSFW_LEAK.sub("", s)
    s = re.sub(r",\s*,", ", ", s).strip(" ,")
    low = s.lower()
    npc_n = max(0, int(supporting_count))
    total_cast = max(cast_count, 1 + npc_n if has_hero else npc_n)

    if has_hero and npc_n >= 1:
        prefix = (
            "medium wide shot, hero in foreground as focal subject, "
            "distinct supporting characters in midground or background, "
            "environmental RPG scene, "
        )
        if "hero in foreground" not in low:
            s = prefix + s
    elif has_hero and total_cast <= 1:
        prefix = "cinematic RPG scene, hero as focal subject, medium wide shot, "
        if "hero as focal subject" not in low:
            s = prefix + s
    elif total_cast >= 2:
        if "wide" not in low and "multiple" not in low:
            s = (
                "wide cinematic shot, multiple distinct characters in frame, "
                "environmental RPG scene, full scene visible, "
                + s
            )
    elif "cinematic" not in low and "wide" not in low and "medium shot" not in low:
        s = "cinematic RPG scene, medium wide shot, " + s
    return s[:500].rstrip(" ,")


_COMPOSITION_ONLY_TAGS = re.compile(
    r"\b(?:medium wide shot|wide cinematic shot|medium shot|environmental RPG scene|"
    r"cinematic RPG scene|hero in foreground as focal subject|hero as focal subject|"
    r"distinct supporting characters in midground or background|"
    r"multiple distinct characters in frame|full scene visible)\b",
    re.IGNORECASE,
)

_STYLE_REFINEMENT: dict[str, str] = {
    "anime": "cel shading, clean linework, vibrant colors, polished anime illustration",
    "realistic": "cinematic lighting, natural materials, sharp detail, painterly realism",
    "pixelart": "pixel art, crisp pixels, limited palette, polished retro game art",
    "krea": "cinematic lighting, rich materials, atmospheric depth, cohesive composition",
}

_FANTASY_THEME_RE = re.compile(
    r"\b(?:fantasy|medieval|gothic|dark fantasy|dystop|dystopian|sci-fi|futuristic)\b",
    re.I,
)


def _refinement_style_tail(style: str, theme: str, *, facets: frozenset[str] | None = None) -> str:
    st = str(style or "").strip().lower()
    facets = facets or detect_theme_facets(theme)
    if st == "realistic" and facets & {"dark_fantasy", "fantasy", "dystopian", "sci_fi"}:
        if facets & {"dark_fantasy", "dystopian"}:
            return (
                "cinematic dark fantasy dystopia, painterly realism, dramatic lighting, rich materials"
            )
        return "cinematic fantasy art, painterly realism, dramatic lighting, rich materials"
    return _STYLE_REFINEMENT.get(st, _STYLE_REFINEMENT["anime"])


def build_scene_refinement_prompt(
    composition_prompt: str,
    *,
    style: str = "anime",
    theme: str = "fantasy",
    style_hint: str = "",
    facets: frozenset[str] | None = None,
    theme_label: str | None = None,
) -> str:
    """
    Pass-2 ControlNet prompt: materials, lighting, style — not spatial layout.

    ControlNet canny from pass1 already locks composition; repeating shot/framing
    tags fights the control map (see standard CN two-pass workflows).
    """
    s = _COMPOSITION_ONLY_TAGS.sub("", str(composition_prompt or ""))
    s = re.sub(r",\s*,", ", ", s).strip(" ,")
    parts = [p.strip() for p in s.split(",") if p.strip()]
    substance = ", ".join(parts[:20])
    anchor = theme_scene_positive_anchor(
        theme, style_hint=style_hint, facets=facets, theme_label=theme_label,
    )
    refine = _refinement_style_tail(style, theme, facets=facets)
    tail = f"{refine}, highly detailed, cohesive lighting, sharp focus"
    if anchor.lower() not in substance.lower():
        substance = f"{anchor}, {substance}".strip(" ,")
    out = f"{substance}, {tail}" if substance else f"{anchor}, {tail}"
    return out[:480].rstrip(" ,")


def build_portrait_prompt(
    *,
    name: str,
    race: str = "",
    class_role: str = "",
    appearance: str = "",
    theme: str = "fantasy",
) -> str:
    parts = [
        f"single character portrait, {name}",
        race,
        class_role,
        appearance,
        f"{theme} RPG character art, waist-up, detailed",
    ]
    return ", ".join(p for p in parts if p and str(p).strip())


def build_scene_prompt(
    *,
    location_name: str,
    description: str = "",
    biome: str = "",
    time_of_day: str = "day",
    weather: str = "clear",
    theme: str = "fantasy",
) -> str:
    desc_tags = prose_to_tags(description)
    parts = [
        f"{theme} RPG environment",
        theme,
        location_name,
        biome,
        desc_tags,
        time_of_day,
        weather,
        "first person view",
        "atmospheric lighting",
        "environment concept art",
        "no characters in foreground",
        "no text overlay",
    ]
    return ", ".join(p for p in parts if p and str(p).strip())


def build_chat_scene_prompt(
    *,
    scene_action: str,
    location_name: str = "",
    time_of_day: str = "day",
    weather: str = "clear",
    theme: str = "fantasy",
    scene_characters: str = "",
) -> str:
    """Per-turn chat scene — hero focal, action second, location as sparse backdrop."""
    action = sanitize_scene_action_for_image(scene_action)
    backdrop = str(location_name or "").strip()
    cast_text = str(scene_characters or "").strip()
    hero_line = ""
    support_lines: list[str] = []
    section = ""
    for line in cast_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("HERO"):
            section = "hero"
            continue
        if upper.startswith("SUPPORTING"):
            section = "supporting"
            continue
        if stripped.startswith("- "):
            if section == "hero" and not hero_line:
                hero_line = stripped.lstrip("- ").strip()
            elif section == "supporting":
                support_lines.append(stripped.lstrip("- ").strip())
    cast_count = (1 if hero_line else 0) + len(support_lines)
    if cast_count == 0 and cast_text:
        cast_count = max(1, len([ln for ln in cast_text.splitlines() if ln.strip().startswith("-")]))

    parts = [f"{theme} RPG scene"]
    if hero_line:
        parts.append(f"focal hero: {hero_line}")
    if action:
        parts.append(action)
    if support_lines:
        parts.append("supporting cast: " + "; ".join(support_lines[:3]))
    if backdrop:
        parts.append(f"background environment: {backdrop}")
    parts.extend([time_of_day, weather, "atmospheric lighting", "no text overlay"])
    raw = ", ".join(p for p in parts if p and str(p).strip())
    return sanitize_scene_generation_prompt(
        raw,
        cast_count=max(1, cast_count),
        has_hero=bool(hero_line),
        supporting_count=len(support_lines),
    )


def prompt_for_asset_request(
    req: dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
    theme: str = "fantasy",
) -> tuple[str, str]:
    asset_type = str(req.get("asset_type") or "scene")
    seed = req.get("prompt_seed") if isinstance(req.get("prompt_seed"), dict) else {}
    state = state or {}
    wt = state.get("world_time") or {}

    if asset_type == "portrait":
        pos = build_portrait_prompt(
            name=str(seed.get("name") or "character"),
            race=str(seed.get("race") or ""),
            class_role=str(seed.get("class") or ""),
            appearance=str(seed.get("appearance") or ""),
            theme=theme,
        )
    elif str(seed.get("scene_kind") or "") == "chat_message" or (
        str(seed.get("scene_action") or "").strip()
        and str(seed.get("scene_kind") or "") != "location"
    ):
        loc = state.get("location_state") or {}
        pos = build_chat_scene_prompt(
            scene_action=str(seed.get("scene_action") or seed.get("description") or ""),
            location_name=str(seed.get("name") or loc.get("name") or ""),
            time_of_day=str(seed.get("time") or wt.get("time_of_day") or "day"),
            weather=str(seed.get("weather") or wt.get("weather") or "clear"),
            theme=theme,
            scene_characters=str(seed.get("scene_characters") or ""),
        )
    else:
        loc = state.get("location_state") or {}
        pos = build_scene_prompt(
            location_name=str(seed.get("name") or loc.get("name") or "unknown place"),
            description=str(seed.get("description") or loc.get("description") or ""),
            biome=str(seed.get("biome") or ""),
            time_of_day=str(seed.get("time") or wt.get("time_of_day") or "day"),
            weather=str(seed.get("weather") or wt.get("weather") or "clear"),
            theme=theme,
        )
    return pos, default_negative()
