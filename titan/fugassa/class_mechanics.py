"""Class-specific creation pickers (infusions, fighting style, favored enemy, …)."""

from __future__ import annotations

import re

from typing import Any

Option = dict[str, Any]
Picker = dict[str, Any]

# Artificer infusions — names match 5e; min_level is artificer level required in RAW (homebrew L1 may waive).
ARTIFICER_INFUSIONS: list[Option] = [
    {"id": "enhanced-weapon", "name": "Enhanced Weapon", "min_level": 2},
    {"id": "enhanced-defense", "name": "Enhanced Defense", "min_level": 2},
    {"id": "enhanced-arcane-focus", "name": "Enhanced Arcane Focus", "min_level": 2},
    {"id": "homunculus-servant", "name": "Homunculus Servant", "min_level": 2},
    {"id": "mind-sharpener", "name": "Mind Sharpener", "min_level": 2},
    {"id": "radiant-weapon", "name": "Radiant Weapon", "min_level": 2},
    {"id": "repulsion-shield", "name": "Repulsion Shield", "min_level": 2},
    {"id": "resistant-armor", "name": "Resistant Armor", "min_level": 2},
    {"id": "returning-weapon", "name": "Returning Weapon", "min_level": 2},
    {"id": "boots-of-the-winding-path", "name": "Boots of the Winding Path", "min_level": 2},
    {"id": "arcane-propulsion-armor", "name": "Arcane Propulsion Armor", "min_level": 2},
    {"id": "armor-of-magical-strength", "name": "Armor of Magical Strength", "min_level": 2},
    {"id": "helm-of-awareness", "name": "Helm of Awareness", "min_level": 2},
    {"id": "replicate-magic-item", "name": "Replicate Magic Item", "min_level": 2},
    {"id": "replicate-alchemy-jug", "name": "Replicate Magic Item: Alchemy Jug", "min_level": 2},
    {"id": "replicate-rope-of-climbing", "name": "Replicate Magic Item: Rope of Climbing", "min_level": 2},
    {"id": "replicate-sending-stones", "name": "Replicate Magic Item: Sending Stones", "min_level": 2},
]

FIGHTING_STYLES: list[Option] = [
    {"id": "archery", "name": "Archery"},
    {"id": "defense", "name": "Defense"},
    {"id": "dueling", "name": "Dueling"},
    {"id": "great-weapon-fighting", "name": "Great Weapon Fighting"},
    {"id": "protection", "name": "Protection"},
    {"id": "two-weapon-fighting", "name": "Two-Weapon Fighting"},
]

FAVORED_ENEMIES: list[Option] = [
    {"id": "aberrations", "name": "Aberrations"},
    {"id": "beasts", "name": "Beasts"},
    {"id": "celestials", "name": "Celestials"},
    {"id": "constructs", "name": "Constructs"},
    {"id": "dragons", "name": "Dragons"},
    {"id": "elementals", "name": "Elementals"},
    {"id": "fey", "name": "Fey"},
    {"id": "fiends", "name": "Fiends"},
    {"id": "giants", "name": "Giants"},
    {"id": "monstrosities", "name": "Monstrosities"},
    {"id": "oozes", "name": "Oozes"},
    {"id": "plants", "name": "Plants"},
    {"id": "undead", "name": "Undead"},
    {"id": "humanoids", "name": "Humanoids (two types)"},
]

FAVORED_TERRAINS: list[Option] = [
    {"id": "arctic", "name": "Arctic"},
    {"id": "coast", "name": "Coast"},
    {"id": "desert", "name": "Desert"},
    {"id": "forest", "name": "Forest"},
    {"id": "grassland", "name": "Grassland"},
    {"id": "mountain", "name": "Mountain"},
    {"id": "swamp", "name": "Swamp"},
    {"id": "underdark", "name": "Underdark"},
]

ELDRITCH_INVOCATIONS: list[Option] = [
    {"id": "agonizing-blast", "name": "Agonizing Blast", "min_level": 2},
    {"id": "armor-of-shadows", "name": "Armor of Shadows", "min_level": 2},
    {"id": "devils-sight", "name": "Devil's Sight", "min_level": 2},
    {"id": "eldritch-sight", "name": "Eldritch Sight", "min_level": 2},
    {"id": "eldritch-spear", "name": "Eldritch Spear", "min_level": 2},
    {"id": "eyes-of-the-rune-keeper", "name": "Eyes of the Rune Keeper", "min_level": 2},
    {"id": "fiendish-vigor", "name": "Fiendish Vigor", "min_level": 2},
    {"id": "mask-of-many-faces", "name": "Mask of Many Faces", "min_level": 2},
    {"id": "misty-visions", "name": "Misty Visions", "min_level": 2},
    {"id": "repelling-blast", "name": "Repelling Blast", "min_level": 2},
    {"id": "thirsting-blade", "name": "Thirsting Blade", "min_level": 5},
    {"id": "lifedrinker", "name": "Lifedrinker", "min_level": 9},
]

METAMAGIC_OPTIONS: list[Option] = [
    {"id": "careful-spell", "name": "Careful Spell", "min_level": 3},
    {"id": "distant-spell", "name": "Distant Spell", "min_level": 3},
    {"id": "empowered-spell", "name": "Empowered Spell", "min_level": 3},
    {"id": "extended-spell", "name": "Extended Spell", "min_level": 3},
    {"id": "heightened-spell", "name": "Heightened Spell", "min_level": 3},
    {"id": "quickened-spell", "name": "Quickened Spell", "min_level": 3},
    {"id": "subtle-spell", "name": "Subtle Spell", "min_level": 3},
    {"id": "twinned-spell", "name": "Twinned Spell", "min_level": 3},
]

# mechanic_id -> definition template
_CLASS_MECHANIC_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "artificer": [
        {
            "id": "infusions",
            "label": "Infusions",
            "type": "multi_pick",
            "cap_key": "infusions_known",
            "default_cap": 0,
            "min_char_level": 1,
            "options": ARTIFICER_INFUSIONS,
            "hint": "Pick infusions known at this level (Artificer / artificer-template classes).",
        },
    ],
    "fighter": [
        {
            "id": "fighting_style",
            "label": "Fighting Style",
            "type": "multi_pick",
            "cap_resolver": "fighter_fighting_styles",
            "min_char_level": 1,
            "options": FIGHTING_STYLES,
            "hint": "Choose fighting style(s). Fighters gain a second at level 10.",
        },
    ],
    "ranger": [
        {
            "id": "favored_enemy",
            "label": "Favored Enemy",
            "type": "multi_pick",
            "cap_key": "favored_enemies",
            "default_cap": 1,
            "min_char_level": 1,
            "options": FAVORED_ENEMIES,
            "hint": "Choose favored enemy type(s).",
        },
        {
            "id": "favored_terrain",
            "label": "Favored Terrain",
            "type": "multi_pick",
            "cap_key": "favored_terrain",
            "default_cap": 1,
            "min_char_level": 1,
            "options": FAVORED_TERRAINS,
            "hint": "Choose favored terrain(s).",
        },
    ],
    "paladin": [
        {
            "id": "fighting_style",
            "label": "Fighting Style",
            "type": "enum",
            "cap": 1,
            "min_char_level": 2,
            "options": FIGHTING_STYLES,
            "hint": "Choose a fighting style (Paladin level 2+).",
        },
    ],
    "rogue": [
        {
            "id": "expertise",
            "label": "Expertise",
            "type": "skill_proficient",
            "cap_resolver": "rogue_expertise",
            "min_char_level": 1,
            "hint": "Choose skills for Expertise (double proficiency).",
        },
    ],
    "bard": [
        {
            "id": "expertise",
            "label": "Expertise",
            "type": "skill_proficient",
            "cap_resolver": "bard_expertise",
            "min_char_level": 3,
            "hint": "Choose skills for Expertise (Bard level 3+).",
        },
    ],
    "barbarian": [],
    "cleric": [],
    "druid": [],
    "monk": [],
    "wizard": [],
    "warlock": [
        {
            "id": "invocations",
            "label": "Eldritch Invocations",
            "type": "multi_pick",
            "cap_key": "invocations_known",
            "default_cap": 0,
            "min_char_level": 2,
            "options": ELDRITCH_INVOCATIONS,
            "hint": "Pick invocations known (Warlock level 2+).",
        },
    ],
    "sorcerer": [
        {
            "id": "metamagic",
            "label": "Metamagic",
            "type": "multi_pick",
            "cap_key": "metamagic_known",
            "default_cap": 0,
            "min_char_level": 3,
            "options": METAMAGIC_OPTIONS,
            "hint": "Pick metamagic options (Sorcerer level 3+).",
        },
    ],
}

# SRD class_specific keys surfaced on sheet (informational counters at current level).
CLASS_RESOURCE_LABELS: dict[str, str] = {
    "rage_count": "Rages per long rest",
    "rage_damage_bonus": "Rage damage bonus",
    "bardic_inspiration_die": "Bardic inspiration die",
    "channel_divinity_charges": "Channel Divinity uses",
    "action_surges": "Action Surges",
    "indomitable_uses": "Indomitable uses",
    "extra_attacks": "Extra attacks",
    "ki_points": "Ki points",
    "sorcery_points": "Sorcery points",
    "invocations_known": "Invocations known",
    "infusions_known": "Infusions known",
    "arcane_recovery_levels": "Arcane Recovery slot levels",
    "favored_enemies": "Favored enemies",
    "favored_terrain": "Favored terrains",
    "fighting_style": "Fighting style",
    "expertise_skills": "Expertise",
}


def _fighting_style_cap(level: int) -> int:
    return 2 if level >= 10 else 1


def _rogue_expertise_cap(level: int) -> int:
    if level >= 6:
        return 4
    if level >= 1:
        return 2
    return 0


def _bard_expertise_cap(level: int) -> int:
    return 2 if level >= 3 else 0


def _resolve_cap_from_template(
    tmpl: dict[str, Any],
    *,
    level: int,
    class_resources: dict[str, Any],
    class_features: list[Any],
) -> int:
    resolver = str(tmpl.get("cap_resolver") or "")
    if resolver == "fighter_fighting_styles":
        return _fighting_style_cap(level)
    if resolver == "rogue_expertise":
        return _rogue_expertise_cap(level)
    if resolver == "bard_expertise":
        return _bard_expertise_cap(level)

    cap = _int_cap(tmpl.get("cap"))
    if cap <= 0 and tmpl.get("cap_key"):
        cap = _int_cap(class_resources.get(str(tmpl["cap_key"])))
    if cap <= 0:
        cap = _int_cap(tmpl.get("default_cap"))
    if tmpl.get("id") == "infusions":
        cap = max(cap, _infusions_cap_from_features(class_features, class_resources, level))
    if tmpl.get("id") == "invocations" and cap <= 0 and level >= 2:
        cap = _int_cap(class_resources.get("invocations_known"))
    if tmpl.get("id") == "metamagic" and cap <= 0 and level >= 3:
        cap = _int_cap(class_resources.get("metamagic_known"))
    return cap


def _skill_options_from_proficiencies(skill_proficiencies: dict[str, Any]) -> list[Option]:
    out: list[Option] = []
    for sid, on in (skill_proficiencies or {}).items():
        if not on:
            continue
        slug = str(sid).strip()
        if not slug:
            continue
        label = slug.replace("-", " ").title()
        out.append({"id": slug, "name": label})
    return sorted(out, key=lambda o: o.get("name", ""))


def _int_cap(val: Any, default: int = 0) -> int:
    try:
        return max(0, int(val))
    except (TypeError, ValueError):
        return default


def _option_name(options: list[Option], opt_id: str) -> str:
    for opt in options:
        if str(opt.get("id", "")) == opt_id:
            return str(opt.get("name", opt_id))
    return opt_id.replace("-", " ").title()


def resolve_mechanic_class_id(
    class_id: str,
    template_class_id: str,
    *,
    is_homebrew_class: bool,
) -> str:
    if class_id:
        return str(class_id).strip().lower()
    if is_homebrew_class and template_class_id:
        return str(template_class_id).strip().lower()
    return ""


def _infusions_cap_from_features(class_features: list[Any], class_resources: dict[str, Any], level: int) -> int:
    cap = _int_cap(class_resources.get("infusions_known"))
    if cap > 0:
        return cap
    for feat in class_features:
        if not isinstance(feat, dict):
            continue
        name = str(feat.get("name", "")).lower()
        if "infusion" not in name:
            continue
        desc = str(feat.get("desc", feat.get("description", "")))
        match = re.search(r"(\d+)\s+infusion", desc, re.I)
        if match:
            return max(0, int(match.group(1)))
    # Official artificer progression (SRD/Tasha) — infusions from level 2.
    if level >= 18:
        return 6
    if level >= 14:
        return 5
    if level >= 10:
        return 4
    if level >= 6:
        return 3
    if level >= 2:
        return 2
    return 0


def _filter_options(options: list[Option], char_level: int, *, relax_min_level: bool) -> list[Option]:
    out: list[Option] = []
    for opt in options:
        min_lvl = _int_cap(opt.get("min_level"), 1)
        if relax_min_level and min_lvl <= 2:
            min_lvl = 1
        if char_level >= min_lvl:
            out.append(dict(opt))
    return out


def class_mechanic_pickers(
    *,
    class_id: str,
    template_class_id: str,
    level: int,
    class_resources: dict[str, Any],
    class_features: list[Any],
    is_homebrew_class: bool,
    skill_proficiencies: dict[str, Any] | None = None,
) -> list[Picker]:
    mechanic_class = resolve_mechanic_class_id(
        class_id,
        template_class_id,
        is_homebrew_class=is_homebrew_class,
    )
    if not mechanic_class:
        return []

    templates = list(_CLASS_MECHANIC_TEMPLATES.get(mechanic_class, []))
    pickers: list[Picker] = []

    # Homebrew artificer-template may declare infusions via class_resources / features only.
    if mechanic_class == "artificer" and is_homebrew_class and not templates:
        templates = list(_CLASS_MECHANIC_TEMPLATES.get("artificer", []))

    relax_min = is_homebrew_class and mechanic_class == "artificer"

    for tmpl in templates:
        min_char = _int_cap(tmpl.get("min_char_level"), 1)
        if level < min_char:
            continue

        cap = _resolve_cap_from_template(
            tmpl,
            level=level,
            class_resources=class_resources,
            class_features=class_features,
        )

        if cap <= 0:
            continue

        ptype = str(tmpl.get("type", "enum"))
        if ptype == "skill_proficient":
            options = _skill_options_from_proficiencies(skill_proficiencies or {})
            if not options:
                continue
        else:
            options = _filter_options(list(tmpl.get("options") or []), level, relax_min_level=relax_min)
        if not options:
            continue

        pickers.append(
            {
                "id": str(tmpl["id"]),
                "label": str(tmpl.get("label", tmpl["id"])),
                "type": ptype,
                "cap": cap,
                "hint": str(tmpl.get("hint", "")),
                "options": options,
                "mechanic_class": mechanic_class,
            }
        )

    return pickers


def normalize_mechanic_choices(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, val in raw.items():
        picker_id = str(key).strip()
        if not picker_id:
            continue
        if isinstance(val, list):
            out[picker_id] = [str(x).strip() for x in val if str(x).strip()]
        elif isinstance(val, str) and val.strip():
            out[picker_id] = [val.strip()]
    return out


def resolve_mechanic_selections(
    pickers: list[Picker],
    choices: dict[str, list[str]],
) -> list[dict[str, Any]]:
    selections: list[dict[str, Any]] = []
    for picker in pickers:
        pid = str(picker.get("id", ""))
        if not pid:
            continue
        picked = list(choices.get(pid) or [])
        if not picked:
            continue
        opt_map = {str(o.get("id", "")): o for o in (picker.get("options") or [])}
        values = [
            {
                "id": oid,
                "name": str(opt_map.get(oid, {}).get("name") or _option_name(picker.get("options") or [], oid)),
            }
            for oid in picked
            if oid in opt_map
        ]
        if values:
            selections.append(
                {
                    "id": pid,
                    "label": str(picker.get("label", pid)),
                    "values": values,
                }
            )
    return selections


def validate_class_mechanic_choices(
    pickers: list[Picker],
    choices_raw: Any,
) -> list[str]:
    choices = normalize_mechanic_choices(choices_raw)
    errors: list[str] = []
    for picker in pickers:
        pid = str(picker.get("id", ""))
        cap = _int_cap(picker.get("cap"))
        label = str(picker.get("label", pid))
        picked = list(choices.get(pid) or [])
        ptype = str(picker.get("type", "enum"))
        valid_ids = {str(o.get("id", "")) for o in (picker.get("options") or [])}
        invalid = [x for x in picked if x not in valid_ids]
        if invalid:
            errors.append(f"{label}: invalid option(s) — {', '.join(invalid)}.")
            continue
        if ptype == "enum":
            if cap > 0 and len(picked) < 1:
                errors.append(f"{label}: pick one in Class mechanics.")
            elif len(picked) > 1:
                errors.append(f"{label}: pick only one.")
        elif ptype in ("multi_pick", "skill_proficient"):
            if cap > 0 and len(picked) < cap:
                errors.append(f"{label}: pick {cap - len(picked)} more ({len(picked)}/{cap}).")
            elif len(picked) > cap:
                errors.append(f"{label}: too many selected ({len(picked)}/{cap}).")
    return errors


def merge_resources_with_mechanics(
    class_resources: dict[str, Any],
    selections: list[dict[str, Any]],
) -> dict[str, Any]:
    out = dict(class_resources or {})
    for sel in selections:
        sid = str(sel.get("id", ""))
        names = [str(v.get("name", "")) for v in (sel.get("values") or []) if v.get("name")]
        if not names:
            continue
        if sid == "infusions":
            out["infusions_known"] = len(names)
            out["infusions"] = names
        elif sid == "invocations":
            out["invocations_known"] = len(names)
            out["invocations"] = names
        elif sid == "metamagic":
            out["metamagic_known"] = len(names)
            out["metamagic"] = names
        elif sid == "favored_enemy":
            out["favored_enemies"] = names
            out["favored_enemy"] = names[0] if len(names) == 1 else names
        elif sid == "favored_terrain":
            out["favored_terrain"] = names if len(names) > 1 else names[0]
        elif sid == "expertise":
            out["expertise_skills"] = names
        elif sid == "fighting_style":
            if len(names) > 1:
                out["fighting_styles"] = names
            out["fighting_style"] = names[0]
        else:
            out[sid] = names if len(names) > 1 else names[0]
    return out


def class_resource_display_lines(class_resources: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if not class_resources:
        return lines
    for key, label in CLASS_RESOURCE_LABELS.items():
        if key not in class_resources:
            continue
        val = class_resources[key]
        if isinstance(val, dict):
            continue
        if isinstance(val, list):
            if val:
                lines.append(f"{label}: {', '.join(str(x) for x in val)}")
        elif val not in (0, None, "", False):
            lines.append(f"{label}: {val}")
    for key in ("infusions", "invocations", "metamagic", "fighting_style", "favored_enemy", "favored_terrain"):
        if key in class_resources and key not in CLASS_RESOURCE_LABELS:
            val = class_resources[key]
            if isinstance(val, list) and val:
                lines.append(f"{key.replace('_', ' ').title()}: {', '.join(str(x) for x in val)}")
            elif isinstance(val, str) and val.strip():
                lines.append(f"{key.replace('_', ' ').title()}: {val}")
    sneak = class_resources.get("sneak_attack")
    if isinstance(sneak, dict):
        dc = sneak.get("dice_count")
        dv = sneak.get("dice_value")
        if dc and dv:
            lines.append(f"Sneak attack: {dc}d{dv}")
    martial = class_resources.get("martial_arts")
    if isinstance(martial, dict):
        dc = martial.get("dice_count")
        dv = martial.get("dice_value")
        if dc and dv:
            lines.append(f"Martial arts: {dc}d{dv}")
    return lines
