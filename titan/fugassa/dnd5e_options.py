"""Wizard character option lists — mirrors static/js/fugassa/wizard/dnd5eOptions.js."""

from __future__ import annotations

GENDER_CHOICES = ["Man", "Woman", "Custom"]

RACE_CHOICES = [
    "Aarakocra", "Aasimar", "Bugbear", "Dragonborn", "Dwarf", "Elf", "Firbolg",
    "Genasi", "Gnome", "Goblin", "Goliath", "Halfling", "Half-Elf", "Half-Orc",
    "Harengon", "Hobgoblin", "Human", "Kenku", "Kobold", "Lizardfolk", "Orc",
    "Tabaxi", "Tiefling", "Tortle", "Triton", "Yuan-ti Pureblood",
    "Changeling", "Kalashtar", "Shifter", "Warforged",
    "Custom",
]

CLASS_CHOICES = [
    "Artificer", "Barbarian", "Bard", "Cleric", "Druid", "Fighter",
    "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard",
    "Custom",
]

SUBCLASS_BY_CLASS: dict[str, list[str]] = {
    "Artificer": ["Alchemist", "Armorer", "Artillerist", "Battle Smith"],
    "Barbarian": [
        "Path of the Berserker", "Path of the Totem Warrior", "Path of the Ancestral Guardian",
        "Path of the Storm Herald", "Path of the Zealot", "Path of Wild Magic", "Path of the Beast",
        "Path of the Battlerager", "Path of the Giant",
    ],
    "Bard": [
        "College of Lore", "College of Valor", "College of Glamour", "College of Swords",
        "College of Whispers", "College of Eloquence", "College of Spirits",
    ],
    "Cleric": [
        "Knowledge Domain", "Life Domain", "Light Domain", "Nature Domain", "Tempest Domain",
        "Trickery Domain", "War Domain", "Death Domain", "Arcana Domain", "Forge Domain",
        "Grave Domain", "Order Domain", "Peace Domain", "Twilight Domain",
    ],
    "Druid": [
        "Circle of the Land", "Circle of the Moon", "Circle of Dreams", "Circle of the Shepherd",
        "Circle of Spores", "Circle of Stars", "Circle of Wildfire",
    ],
    "Fighter": [
        "Champion", "Battle Master", "Eldritch Knight", "Purple Dragon Knight (Banneret)",
        "Arcane Archer", "Cavalier", "Samurai", "Psi Warrior", "Rune Knight", "Echo Knight",
    ],
    "Monk": [
        "Way of the Open Hand", "Way of Shadow", "Way of the Four Elements", "Way of the Drunken Master",
        "Way of the Kensei", "Way of the Sun Soul", "Way of the Astral Self", "Way of Mercy",
    ],
    "Paladin": [
        "Oath of Devotion", "Oath of the Ancients", "Oath of Vengeance", "Oath of Conquest",
        "Oath of Redemption", "Oath of Glory", "Oath of the Watchers", "Oathbreaker",
    ],
    "Ranger": [
        "Hunter", "Beast Master", "Gloom Stalker", "Horizon Walker", "Monster Slayer",
        "Fey Wanderer", "Swarmkeeper", "Drakewarden",
    ],
    "Rogue": [
        "Thief", "Assassin", "Arcane Trickster", "Inquisitive", "Mastermind", "Scout",
        "Swashbuckler", "Phantom", "Soulknife",
    ],
    "Sorcerer": [
        "Draconic Bloodline", "Wild Magic", "Divine Soul", "Shadow Magic", "Storm Sorcery",
        "Aberrant Mind", "Clockwork Soul",
    ],
    "Warlock": [
        "The Fiend", "The Great Old One", "The Archfey", "The Celestial", "The Hexblade",
        "The Fathomless", "The Genie", "The Undead", "The Undying",
    ],
    "Wizard": [
        "School of Abjuration", "School of Conjuration", "School of Divination", "School of Enchantment",
        "School of Evocation", "School of Illusion", "School of Necromancy", "School of Transmutation",
        "Bladesinging", "War Magic", "Chronurgy Magic", "Graviturgy Magic",
    ],
}


# 5e hit dice per class (SRD). Custom/homebrew classes (e.g. a sci-fi
# "Scientist" from a non-fantasy theme) don't match any of these, so they
# fall back to d8 — a "medium" archetype rather than the fixed classes'
# actual dice, which is far closer to reality than the old flat 100 HP.
HIT_DIE_BY_CLASS: dict[str, int] = {
    "Artificer": 8, "Bard": 8, "Cleric": 8, "Druid": 8, "Monk": 8,
    "Rogue": 8, "Warlock": 8,
    "Fighter": 10, "Paladin": 10, "Ranger": 10,
    "Barbarian": 12,
    "Sorcerer": 6, "Wizard": 6,
}
DEFAULT_HIT_DIE = 8

# 5e SRD cumulative XP thresholds (total XP needed to *reach* that level).
XP_THRESHOLDS_BY_LEVEL: dict[int, int] = {
    1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500, 6: 14000, 7: 23000, 8: 34000,
    9: 48000, 10: 64000, 11: 85000, 12: 100000, 13: 120000, 14: 140000,
    15: 165000, 16: 195000, 17: 225000, 18: 265000, 19: 305000, 20: 355000,
}


def ability_modifier(score: int) -> int:
    return (int(score) - 10) // 2


def hit_die_for_class(class_name: str) -> int:
    return HIT_DIE_BY_CLASS.get(str(class_name or "").strip(), DEFAULT_HIT_DIE)


def max_hp_for_level(class_name: str, level: int, con_score: int) -> int:
    """5e-style max HP: full hit die + CON mod at level 1, then average
    hit-die roll + CON mod for every level after that (never below 1 hp per
    level). Applies to `homebrew` rules_mode too — a non-5e campaign still
    needs *some* level/CON-scaled starting HP rather than a fixed number
    that means the same thing for a level-1 rookie and a level-10 veteran.
    """
    die = hit_die_for_class(class_name)
    con_mod = ability_modifier(con_score)
    lvl = max(1, int(level or 1))
    per_level_gain = max(die // 2 + 1 + con_mod, 1)
    return max(die + con_mod, 1) + per_level_gain * (lvl - 1)


def xp_to_next_for_level(level: int) -> int:
    """XP delta needed to go from `level` to `level + 1` (not cumulative —
    a freshly-created level-5 character starts with xp=0 progress *into*
    level 5, so xp_to_next should be the size of the level-5→6 step, not
    the flat 300 that only makes sense for level 1→2).
    """
    lvl = max(1, min(int(level or 1), 20))
    lo = XP_THRESHOLDS_BY_LEVEL.get(lvl, XP_THRESHOLDS_BY_LEVEL[20])
    hi = XP_THRESHOLDS_BY_LEVEL.get(lvl + 1, lo)
    return max(hi - lo, 100)


def xp_level_progress(experience_points: int, level: int) -> dict[str, int | bool]:
    """Progress from current level toward the next (campaign XP is per-level, not lifetime)."""
    lvl = max(1, min(int(level or 1), 20))
    needed = xp_to_next_for_level(lvl)
    progress = max(0, int(experience_points or 0))
    remaining = max(0, needed - progress)
    return {
        "progress": progress,
        "needed": needed,
        "remaining": remaining,
        "eligible": lvl < 20 and progress >= needed,
    }


def _pick(choices: list[str], index: int, custom: str) -> str:
    idx = int(index or 0)
    if 0 <= idx < len(choices) and choices[idx] == "Custom":
        return str(custom or "").strip()
    if 0 <= idx < len(choices):
        return choices[idx]
    return str(custom or "").strip()


def effective_gender(draft: dict) -> str:
    return _pick(GENDER_CHOICES, draft.get("player_gender_idx", 0), draft.get("player_gender_custom", ""))


def effective_race(draft: dict) -> str:
    return _pick(RACE_CHOICES, draft.get("player_race_idx", 16), draft.get("player_race_custom", ""))


def effective_class(draft: dict) -> str:
    return _pick(CLASS_CHOICES, draft.get("player_class_idx", 4), draft.get("player_class_custom", ""))


def effective_subclass(draft: dict) -> str:
    cls = effective_class(draft)
    choices = SUBCLASS_BY_CLASS.get(cls, [])
    custom = str(draft.get("player_subclass_custom", "") or "").strip()
    idx = int(draft.get("player_subclass_idx", 0) or 0)
    if cls == "Custom":
        return custom
    if choices and idx == len(choices) - 1 and custom:
        return custom
    if 0 <= idx < len(choices):
        return choices[idx]
    return custom
