"""Strict wizard-time starting property — engine-owned, not freeform LLM layout."""

from __future__ import annotations

import re
from typing import Any

from titan.fugassa.starting_wealth import wealth_tier_for_background

_SOVEREIGN_KEYWORDS = (
    "king of",
    "queen of",
    "emperor of",
    "empress of",
    "ruler of",
    "monarch of",
    "vládce",
    "král ",
    "královna",
    "císař",
    "císařovna",
    "reigns over",
    "rules the kingdom",
    "rules the realm",
)

_RURAL_KEYWORDS = (
    "farmer",
    "hermit",
    "woodcutter",
    "shepherd",
    "farmhand",
    "peasant farmer",
    "farmstead",
    "venkov",
    "statek",
)

_EXPLICIT_HOME_KEYWORDS = (
    "owns a small",
    "owns a modest",
    "rents a room",
    "rents a flat",
    "rents an apartment",
    "small cottage",
    "modest apartment",
    "townhouse",
    "family home",
    "inherited a",
    "vlastní malý",
    "vlastní skromný",
)


def _combined_text(*parts: str) -> str:
    return " ".join(str(p or "").strip() for p in parts if str(p or "").strip()).lower()


def is_sovereign_ruler(*texts: str) -> bool:
    """Kings/emperors rule realms — personal property holdings are inappropriate."""
    blob = _combined_text(*texts)
    if not blob:
        return False
    if any(kw in blob for kw in _SOVEREIGN_KEYWORDS):
        return True
    if re.search(r"\b(king|queen|emperor|empress|monarch)\b", blob):
        if re.search(r"\b(of|over|realm|kingdom|empire|království)\b", blob):
            return True
    return False


def _is_rural_background(background: str) -> bool:
    bg = str(background or "").lower()
    return any(kw in bg for kw in _RURAL_KEYWORDS)


def _explicit_home_in_backstory(*texts: str) -> bool:
    blob = _combined_text(*texts)
    return any(kw in blob for kw in _EXPLICIT_HOME_KEYWORDS)


def propose_starting_property(
    *,
    background: str,
    backstory: str = "",
    world_information: str = "",
    hero_name: str = "Hero",
    wealth_tier: int | None = None,
) -> dict[str, Any] | None:
    """
    Return a modest starting property proposal, or None.

    Rules (strict):
    - Tier 0–2: usually nothing (tier 2 only if backstory explicitly mentions a home)
    - Tier 3 (merchant/landowner): small apartment or modest cottage
    - Tier 4 (noble/wealthy): modest townhouse or small estate — never a castle
    - Sovereign rulers: no personal holding (realm is the asset)
    """
    if is_sovereign_ruler(background, backstory, world_information):
        return {
            "granted": False,
            "reason": "sovereign",
            "narrative_note": (
                "Character rules a realm or kingdom — political domain, not a personal deed."
            ),
        }

    tier = wealth_tier if wealth_tier is not None else wealth_tier_for_background(background)
    explicit = _explicit_home_in_backstory(background, backstory)

    if tier <= 1:
        return None
    if tier == 2 and not explicit:
        return None

    rural = _is_rural_background(background)
    hero = str(hero_name or "Hero").strip() or "Hero"

    if tier == 2:
        kind = "cottage" if rural else "townhouse"
        return _holding_proposal(
            hero_name=hero,
            property_kind=kind,
            prestige=0,
            bedrooms=1,
            deed_via="rental" if "rent" in _combined_text(background, backstory) else "modest ownership",
            summary=f"A very modest {kind.replace('_', ' ')} tied to the character's humble means.",
        )

    if tier == 3:
        kind = "cottage" if rural else "townhouse"
        return _holding_proposal(
            hero_name=hero,
            property_kind=kind,
            prestige=1,
            bedrooms=1,
            deed_via="ownership",
            summary=f"A modest {kind.replace('_', ' ')} — practical shelter, not a status symbol.",
        )

    # tier 4 — still restrained
    kind = "estate" if rural else "townhouse"
    return _holding_proposal(
        hero_name=hero,
        property_kind=kind,
        prestige=2,
        bedrooms=2 if kind == "townhouse" else 3,
        deed_via="inheritance",
        summary=(
            f"A comfortable but not extravagant {kind.replace('_', ' ')} — "
            "family-scale, no fortress or palace."
        ),
    )


def _holding_proposal(
    *,
    hero_name: str,
    property_kind: str,
    prestige: int,
    bedrooms: int,
    deed_via: str,
    summary: str,
) -> dict[str, Any]:
    label = {
        "townhouse": "Modest Townhouse",
        "cottage": "Small Cottage",
        "estate": "Small Country Estate",
    }.get(property_kind, "Modest Residence")
    name = f"{hero_name}'s {label}"
    code_base = re.sub(r"[^a-z0-9]+", "_", hero_name.lower()).strip("_") or "hero"
    return {
        "granted": True,
        "code": f"{code_base}_residence",
        "name": name,
        "property_kind": property_kind,
        "title_status": "owned",
        "acquired_via": deed_via,
        "deed_summary": summary,
        "specs": {
            "prestige": prestige,
            "comfort": min(3, prestige + 1),
            "bedrooms": bedrooms,
            "modest": True,
            "wizard_seeded": True,
        },
    }
