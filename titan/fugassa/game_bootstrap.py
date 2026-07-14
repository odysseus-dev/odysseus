"""Build Fugassa II game.json from wizard draft (ports Main.gd _create_new_save_from_wizard)."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
from typing import Any

from titan.fugassa.dnd5e_options import (
    ability_modifier,
    effective_class,
    effective_gender,
    effective_race,
    effective_subclass,
    max_hp_for_level,
    xp_to_next_for_level,
)
from titan.fugassa.paths import FUGASSA_ROOT
from titan.fugassa.sheet_persistence import build_sheet_from_draft, sheet_to_game_json
from titan.fugassa.starting_wealth import apply_starting_currency

GAME_JSON = "game.json"

_FREEFORM_PLAYSTYLES = frozenset({"slice_of_life"})


def playstyle_framework(playstyle: str) -> str:
    ps = str(playstyle or "adventure").strip().lower()
    return "freeform" if ps in _FREEFORM_PLAYSTYLES else "rules_based"


def resolve_theme(draft: dict[str, Any]) -> str:
    mode = str(draft.get("theme_mode") or "Fantasy").strip()
    if mode == "Custom":
        custom = str(draft.get("theme_custom") or "").strip()
        return custom or "Fantasy"
    return mode


def default_currency_for_theme(theme: str) -> list[str]:
    t = str(theme or "").lower()
    if "sci" in t:
        return ["credits", "data chips", "reactor cores"]
    if "modern" in t or "present" in t:
        return ["coins", "bills", "certificates"]
    return ["bronze", "silver", "gold"]


def build_initial_game_state(save_name: str, theme: str) -> dict[str, Any]:
    currency = default_currency_for_theme(theme)
    return {
        "save_name": save_name,
        "world_profile": {
            "theme": theme,
            "world_information": "",
            "currency": currency,
        },
        "player": {"x": 0, "y": 0, "z": 0},
        # Placeholder stats only — `apply_wizard_draft` immediately overwrites
        # hp/max_hp/ac/xp_to_next with values computed from the wizard's
        # class/level/abilities/gear (a level-1 rookie and a level-10 veteran
        # must not both show up with the same flat 100 HP / AC 12).
        "party": [
            {
                "name": "Hero",
                "role": "player",
                "hp": 10,
                "max_hp": 10,
                "ac": 10,
                "background": "Unknown wanderer",
                "level": 1,
                "xp": 0,
                "xp_to_next": 300,
            }
        ],
        # Deliberately empty — real starting gear/supplies come from the
        # wizard's Inventory/Gear tabs via `apply_wizard_draft`; a fixed
        # "Rations/Torch/Health Potion + Basic Sword/Cloth Vest" kit would
        # silently override whatever the player actually built.
        "inventory": {
            "shared": [],
            "equipped": {},
        },
        # Deliberately empty — a hardcoded "First Steps" quest from a
        # "Village Elder" made no sense for campaigns whose wizard-authored
        # world never mentioned either. Real quests/NPCs are established by
        # the GM's opening scene and grown by the archivist during play
        # (ADR memory system), not seeded as flavorless placeholders that
        # get written straight into the SQL canon on every new save.
        "quests": {
            "active": [],
            "closed": [],
        },
        "location_state": {
            "name": "Starter Crossroads",
            "description": "A quiet crossroads where your journey begins.",
            "npcs": [],
            "enemies": [],
            "loot": [],
            "sublocations": [],
        },
        "turn": 0,
        "world_time": {"day": 1, "hour": 8},
        "can_undo": False,
        "discovered_blocks": {"0,0,0": True},
        "intel_targets": {},
        "cell_location_cache": {
            "0,0,0": {
                "name": "Starter Crossroads",
                "description": "A quiet crossroads where your journey begins.",
            }
        },
        "travel_capabilities": {"walk": True, "ride": False, "teleport": False, "fly": False},
        "chat_history": [],
    }


_TIME_HINT_COLUMNS = (
    "time of day",
    "hh:mm am/pm",
    "era, year, month, day",
    "moon phase",
    "current location",
    "season",
    "weather",
)


def _parse_time_hint_table(time_hint: str) -> dict[str, str]:
    """
    Parse the wizard Opening tab's 2-line markdown table (see
    `wizard_engine.generateOpeningOptions`'s required "time_hint" shape:
    "| Time of Day | HH:MM AM/PM | Era, Year, Month, Day | Moon Phase |
    Current Location | Season | Weather |") into a {column_name: value} dict.

    Best-effort / never raises — a malformed or missing table just yields {}.
    """
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in str(time_hint or "").splitlines()
        if line.strip().startswith("|")
    ]
    # Drop a markdown separator row like "|---|---|...|" if present.
    rows = [r for r in rows if not all(re.fullmatch(r"-{2,}", c) for c in r if c)]
    if len(rows) < 2:
        return {}
    header, data = rows[0], rows[1]
    out: dict[str, str] = {}
    for idx, col in enumerate(header):
        if idx < len(data) and data[idx]:
            out[col.strip().lower()] = data[idx].strip()
    return out


def starting_location_from_opening(opening_time_hint: str, opening_hook: str) -> tuple[str, str]:
    """
    Derive a starting location name straight from the wizard's Opening tab
    (no extra LLM call — the "Current Location" cell of the time_hint table
    is already exactly this, e.g. "Oakhaven Reach (Lucas's quarters)").
    Falls back to the generic starter location only when the wizard never
    produced usable opening data (e.g. an empty/default draft).

    The description is deliberately kept short/generic rather than reusing
    `opening_hook` verbatim: that text is first-person/third-person scene
    narration about the CHARACTER waking up etc. (it's already shown as the
    GM's opening chat message) — dumping the whole multi-paragraph narrative
    into the "location description" slot read like nonsense out of context.
    The archivist's "update location / description_append" op is the
    intended way this field grows real place-description content over
    subsequent turns as the GM actually narrates the surroundings.
    """
    fields = _parse_time_hint_table(opening_time_hint)
    name = fields.get("current location", "").strip()
    if not name and str(opening_hook or "").strip():
        name = "Starting Location"
    if not name:
        return "Starter Crossroads", "A quiet crossroads where your journey begins."
    return name, f"You find yourself in {name}."


def _parse_hour_from_time_hint(opening_time_hint: str) -> int | None:
    fields = _parse_time_hint_table(opening_time_hint)
    raw = fields.get("hh:mm am/pm", "")
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)?$", raw.strip(), re.IGNORECASE)
    if not m:
        return None
    hour = int(m.group(1)) % 12
    if (m.group(3) or "").upper() == "PM":
        hour += 12
    return hour


def _parse_date_hint_cell(cell: str) -> dict[str, str | int]:
    """Best-effort split of wizard 'Era, Year, Month, Day' column."""
    text = str(cell or "").strip()
    if not text:
        return {}
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 4:
        out: dict[str, str | int] = {
            "era": parts[0],
            "year": parts[1],
            "month": parts[2],
        }
        if parts[3].isdigit():
            out["day"] = int(parts[3])
        else:
            out["day"] = parts[3]
        return out
    if len(parts) == 3:
        return {"era": parts[0], "year": parts[1], "month": parts[2]}
    if len(parts) == 2:
        return {"era": parts[0], "year": parts[1]}
    if re.search(r"year", text, re.IGNORECASE):
        return {"year": text}
    return {"era": text}


def apply_opening_time_hint_to_world_time(state: dict[str, Any], *, overwrite: bool = False) -> bool:
    """
    Map wizard Opening tab time_hint table → `world_time` (TIME fix, Phase 3).

    When overwrite=False, only fills keys that are missing/empty so GM-established
    timestamps from later turns are preserved (used for legacy save migration).
    """
    hint = str((state.get("world_profile") or {}).get("opening_time_hint") or "").strip()
    fields = _parse_time_hint_table(hint)
    if not fields:
        return False

    wt = dict(state.get("world_time") or {})
    changed = False

    def _set(key: str, value: Any) -> None:
        nonlocal changed
        if value is None or (isinstance(value, str) and not str(value).strip()):
            return
        if overwrite or wt.get(key) in (None, ""):
            wt[key] = value
            changed = True

    _set("time_of_day", fields.get("time of day"))
    hhmm_raw = fields.get("hh:mm am/pm", "")
    if hhmm_raw:
        _set("hhmm", hhmm_raw)
        hour = _parse_hour_from_time_hint(hint)
        if hour is not None:
            _set("hour", hour)
        from titan.fugassa import world_time_engine

        parsed = world_time_engine.parse_hhmm(hhmm_raw)
        if parsed:
            _set("hour", parsed[0])
            _set("minute", parsed[1])
    for key, val in _parse_date_hint_cell(fields.get("era, year, month, day", "")).items():
        _set(key, val)
    _set("moon_phase", fields.get("moon phase"))
    _set("season", fields.get("season"))
    _set("weather", fields.get("weather"))

    if changed:
        state["world_time"] = wt
    return changed


def apply_wizard_draft(state: dict[str, Any], draft: dict[str, Any], *, theme: str) -> dict[str, Any]:
    hero_name = str(draft.get("player_name") or "Hero").strip() or "Hero"
    lvl = max(1, int(draft.get("level") or 1))
    playstyle = str(draft.get("playstyle") or "adventure").strip().lower()
    pf = playstyle_framework(playstyle)

    world_profile = dict(state.get("world_profile") or {})
    world_profile.update(
        {
            "theme": theme,
            "campaign_length": str(draft.get("campaign_length") or "medium"),
            "world_information": str(draft.get("world_information") or ""),
            "opening_hook": str(draft.get("opening_hook") or ""),
            "opening_time_hint": str(draft.get("opening_time_hint") or ""),
            "currency": list(draft.get("currency") or default_currency_for_theme(theme))[:3],
            "image_style": str(draft.get("image_style") or "").strip(),
            "playstyle": playstyle,
            "playstyle_framework": pf,
        }
    )
    from titan.fugassa.theme_facet_engine import apply_normalized_theme_to_world_profile

    apply_normalized_theme_to_world_profile(
        world_profile,
        theme_facets=list(draft.get("theme_facets") or []),
        theme_label_en=str(draft.get("theme_label_en") or "").strip(),
    )
    if not world_profile.get("theme_facets"):
        from titan.fugassa.theme_facet_engine import resolve_theme_facets

        facets = resolve_theme_facets(
            theme,
            world_information=str(draft.get("world_information") or ""),
        )
        apply_normalized_theme_to_world_profile(
            world_profile,
            theme_facets=sorted(facets),
            theme_label_en=str(draft.get("theme_label_en") or theme).strip(),
        )
    state["world_profile"] = world_profile
    state["playstyle"] = playstyle
    state["playstyle_framework"] = pf
    state["rules_mode"] = str(draft.get("rules_mode") or "5e-style")
    state["resolution_mode"] = str(draft.get("resolution_mode") or "dice")

    # The wizard's Opening tab is the actual source of truth for where/when the
    # campaign begins — without this, the starting save always kept the
    # generic placeholder location/time from `build_initial_game_state`
    # (hardcoded "Starter Crossroads" / day 1, 8am) no matter what the player
    # wrote in the Opening tab, so the game never looked like it "took"
    # anything from the wizard.
    start_name, start_desc = starting_location_from_opening(
        world_profile["opening_time_hint"], world_profile["opening_hook"]
    )
    location_state = dict(state.get("location_state") or {})
    location_state["name"] = start_name
    location_state["description"] = start_desc
    state["location_state"] = location_state
    cell_key = f"{(state.get('player') or {}).get('x', 0)},{(state.get('player') or {}).get('y', 0)},{(state.get('player') or {}).get('z', 0)}"
    cache = dict(state.get("cell_location_cache") or {})
    cache[cell_key] = {"name": start_name, "description": start_desc}
    state["cell_location_cache"] = cache

    apply_opening_time_hint_to_world_time(state, overwrite=True)

    gm_map = draft.get("gm_guides_map")
    if isinstance(gm_map, dict) and gm_map:
        state["gm_guides_map"] = dict(gm_map)
    state["gm_guides_notes"] = str(draft.get("gm_guides_notes") or "")

    abilities_src = draft.get("abilities") if isinstance(draft.get("abilities"), dict) else {}
    con_score = int(abilities_src.get("con", 10))
    dex_score = int(abilities_src.get("dex", 10))
    class_name = effective_class(draft)

    # Real HP/AC/damage instead of the old flat 100/12/1d8 — those looked
    # like nonsense (a level-1 rookie and a level-10 veteran both showing
    # exactly 100 HP) because they never actually read the wizard's
    # class/level/CON or the Gear tab's structured armor/weapon output.
    gear_struct = draft.get("gear_structured") if isinstance(draft.get("gear_structured"), dict) else {}
    gear_armor = gear_struct.get("armor") if isinstance(gear_struct.get("armor"), dict) else {}
    gear_weapon = gear_struct.get("weapon") if isinstance(gear_struct.get("weapon"), dict) else {}
    # The wizard's JSON shape prompt says "ac", but in the wild the LLM has
    # also emitted "defense" / "armor_class" as a string like "12" — parse
    # leniently across all three rather than silently dropping to the
    # unarmored fallback just because the key/type didn't match exactly.
    armor_ac_raw = gear_armor.get("ac") or gear_armor.get("defense") or gear_armor.get("armor_class")
    armor_ac_match = re.match(r"\s*(\d+)", str(armor_ac_raw)) if armor_ac_raw is not None else None
    ac = int(armor_ac_match.group(1)) if armor_ac_match else 10 + ability_modifier(dex_score)
    # Same leniency for weapon damage: LLM output is often "1d6+2 piercing"
    # (trailing damage-type prose) rather than a bare dice expression — pull
    # just the leading dice notation out instead of requiring an exact match.
    damage_match = re.match(r"\s*(\d+\s*d\s*\d+(?:\s*[+-]\s*\d+)?)", str(gear_weapon.get("damage") or ""), re.IGNORECASE)
    damage_dice = damage_match.group(1).replace(" ", "") if damage_match else "1d8"

    computed_sheet: dict[str, Any] | None = None
    build_input: dict[str, Any] = {}
    try:
        computed_sheet, build_input = build_sheet_from_draft(draft)
    except Exception:
        computed_sheet = None

    max_hp = int(computed_sheet.get("hp") or 0) if computed_sheet else 0
    if max_hp <= 0:
        max_hp = max_hp_for_level(class_name, lvl, con_score)
    if armor_ac_match:
        ac = int(armor_ac_match.group(1))
    elif computed_sheet:
        ac = int(computed_sheet.get("ac_base") or (10 + ability_modifier(dex_score)))
    else:
        ac = 10 + ability_modifier(dex_score)

    party = list(state.get("party") or [])
    if party:
        hero = dict(party[0])
        hero.update(
            {
                "name": hero_name,
                "background": str(draft.get("character_background") or ""),
                "gender": effective_gender(draft),
                "race": effective_race(draft),
                "character_class": class_name,
                "subclass": effective_subclass(draft),
                "age": str(draft.get("player_age") or "").strip(),
                "level": lvl,
                "hp": max_hp,
                "max_hp": max_hp,
                "ac": ac,
                "xp": 0,
                "xp_to_next": xp_to_next_for_level(lvl),
                "damage_dice": damage_dice,
            }
        )
        party[0] = hero
        state["party"] = party

    # Equipped shape is slot-keyed ({"weapon_main": {...}, "body": {...}, ...},
    # matching equipment_slots.SLOTS) rather than the old flat {weapon, armor}
    # strings, so it's compatible with item_engine.equip_item/unequip_item and
    # the Inventory & Equipment screen's paperdoll straight out of chargen.
    weapon_name = str(gear_weapon.get("name") or draft.get("start_weapon") or "Basic Sword").strip()
    armor_name = str(gear_armor.get("name") or draft.get("start_armor") or "Cloth Vest").strip()
    inventory = dict(state.get("inventory") or {})
    equipped = dict(inventory.get("equipped") or {})
    equipped[hero_name] = {
        "weapon_main": {
            "name": weapon_name,
            "description": str(gear_weapon.get("description") or ""),
            "damage": damage_dice,
        },
        "body": {
            "name": armor_name,
            "description": str(gear_armor.get("description") or ""),
            "ac": ac,
        },
    }
    inventory["equipped"] = equipped
    inv_struct = draft.get("inventory_structured")
    if isinstance(inv_struct, dict) and inv_struct.get("items"):
        inventory["structured"] = inv_struct
        # `shared` (not `structured`) is what item_engine/quest_engine/
        # grid_engine/gm_runner actually read and mutate at runtime — without
        # copying into it, the wizard's real Inventory tab picks never left
        # this write-only mirror and the game silently ran on an empty kit.
        shared = list(inventory.get("shared") or [])
        existing = {str(i.get("name", "")).strip().lower() for i in shared if isinstance(i, dict)}
        for item in inv_struct["items"]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name.lower() in existing:
                continue
            qty = max(1, int(item.get("quantity") or 1))
            shared.append({"name": name, "qty": qty, "description": str(item.get("description") or "")})
        inventory["shared"] = shared
    inventory = apply_starting_currency(
        inventory,
        background=str(draft.get("character_background") or ""),
        currency=world_profile.get("currency") or [],
        level=lvl,
    )
    from titan.fugassa.player_property_engine import propose_starting_property

    property_proposal = propose_starting_property(
        background=str(draft.get("character_background") or ""),
        backstory=str(draft.get("character_backstory") or draft.get("backstory") or ""),
        world_information=str(world_profile.get("world_information") or ""),
        hero_name=hero_name,
    )
    if property_proposal:
        draft["starting_property"] = property_proposal
        if property_proposal.get("granted"):
            portfolio = {
                "holdings": [
                    {
                        "code": property_proposal["code"],
                        "name": property_proposal["name"],
                        "property_kind": property_proposal["property_kind"],
                        "title_status": property_proposal.get("title_status", "owned"),
                        "deed_summary": property_proposal.get("deed_summary", ""),
                        "specs": property_proposal.get("specs") or {},
                        "wizard_seeded": True,
                    }
                ],
                "active_residence_code": property_proposal["code"],
            }
            state["property_portfolio"] = portfolio
        elif property_proposal.get("reason") == "sovereign":
            world_profile["property_note"] = property_proposal.get("narrative_note", "")
    inventory["notes"] = str(draft.get("inventory_notes") or "")
    state["inventory"] = inventory

    loc_name = str((state.get("location_state") or {}).get("name") or "Starter Crossroads")
    hero_state = dict(party[0]) if party else {}
    identity = {
        "name": hero_name,
        "background": str(draft.get("character_background") or ""),
        "gender": effective_gender(draft),
        "race": effective_race(draft),
        "character_class": effective_class(draft),
        "subclass": effective_subclass(draft),
        "age": str(draft.get("player_age") or "").strip(),
        "level": lvl,
    }
    labels = (computed_sheet or {}).get("labels") or {}
    if labels.get("subrace"):
        identity["subrace"] = labels["subrace"]

    if computed_sheet:
        state["character_sheet"] = sheet_to_game_json(
            computed_sheet,
            build_input,
            identity=identity,
            weapon_name=weapon_name,
            armor_name=armor_name,
            loc_name=loc_name,
            hp_current=int(hero_state.get("hp", max_hp)),
            inventory_notes=str(draft.get("inventory_notes") or ""),
        )
        # Equipped armor from wizard gear overrides computed base AC in party runtime.
        if party:
            party[0]["ac"] = ac
            state["party"] = party
        if state["character_sheet"].get("derived"):
            state["character_sheet"]["derived"]["ac_base"] = ac
    else:
        sheet_abilities = {
            "strength": int(abilities_src.get("str", 10)),
            "dexterity": int(abilities_src.get("dex", 10)),
            "constitution": int(abilities_src.get("con", 10)),
            "intelligence": int(abilities_src.get("int", 10)),
            "wisdom": int(abilities_src.get("wis", 10)),
            "charisma": int(abilities_src.get("cha", 10)),
        }
        prof = 2 + int(math.floor(max(lvl - 1, 0) / 4))
        race_class = f"{effective_race(draft)} {effective_class(draft)}".strip()
        sub = effective_subclass(draft)
        if sub:
            race_class = f"{race_class} ({sub})"
        character_compact = (
            f"{hero_name} ({race_class}, age {str(draft.get('player_age') or '').strip()}), "
            f"level {lvl} — {str(draft.get('character_background') or '').strip()}"
        )
        state["character_sheet"] = {
            "stable_sheet": {
                "identity": identity,
                "abilities": sheet_abilities,
                "inventory": {
                    "notes": str(draft.get("inventory_notes") or ""),
                    "weapon": weapon_name,
                    "armor": armor_name,
                },
            },
            "volatile_state": {
                "hp_current": int(hero_state.get("hp", max_hp)),
                "location": loc_name,
                "conditions": [],
            },
            "derived": {"proficiency_bonus": prof},
            "llm_summary": {"character_summary_compact": character_compact},
        }

    opening = draft.get("opening_structured")
    if isinstance(opening, dict) and opening.get("opening_text"):
        state["opening_scene"] = opening
    elif str(draft.get("opening_hook") or "").strip():
        state["opening_scene"] = {
            "opening_text": str(draft.get("opening_hook") or "").strip(),
            "time_hint": str(draft.get("opening_time_hint") or "").strip(),
        }

    if draft.get("sheet_snapshot"):
        state["wizard_sheet_snapshot"] = draft.get("sheet_snapshot")

    state["wizard_draft_snapshot"] = {
        k: draft.get(k)
        for k in (
            "portrait_appearance",
            "portrait_sd_prompt_text",
            "player_class_idx",
            "player_race_idx",
            "player_subclass_idx",
            "abilities",
            "spell_list_class_id",
            "skill_proficiencies",
            "expertise",
            "selected_cantrips",
            "selected_spells_by_level",
            "asi_choices",
            "homebrew_choices",
            "class_mechanic_choices",
            "homebrew_details",
            "gear_structured",
        )
        if draft.get(k) is not None
    }

    return state


def write_game_json(save_dir: str, state: dict[str, Any]) -> str:
    path = os.path.join(save_dir, GAME_JSON)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def read_game_json(save_dir: str) -> dict[str, Any] | None:
    path = os.path.join(save_dir, GAME_JSON)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_gm_guides(save_dir: str, guides_map: dict[str, Any]) -> None:
    gm_dir = os.path.join(save_dir, "gm")
    os.makedirs(gm_dir, exist_ok=True)
    manifest: list[str] = []
    for name, text in sorted((guides_map or {}).items()):
        safe = str(name).strip() or "gm_custom.txt"
        if not safe.endswith(".txt"):
            safe = f"{safe}.txt"
        path = os.path.join(gm_dir, safe)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(text or ""))
        manifest.append(safe)
    manifest_path = os.path.join(gm_dir, "gm_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"files": manifest}, f, indent=2)
        f.write("\n")


def attach_portrait_from_staging(
    state: dict[str, Any],
    save_dir: str,
    staging_path: str,
    *,
    player_character_id: int = 1,
) -> str | None:
    """
    Copy wizard portrait into generated/portraits/ and update game.json fields.
    Returns relative path under generated/ (for assets table), or None on failure.
    """
    if not staging_path or not os.path.isfile(staging_path):
        return None
    from titan.fugassa.paths import ensure_save_dirs, generated_dir

    ensure_save_dirs(save_dir)
    rel = f"portraits/pc_{player_character_id}_v1.png"
    dest = os.path.join(generated_dir(save_dir), rel)
    shutil.copy2(staging_path, dest)
    party = list(state.get("party") or [])
    if party:
        hero = dict(party[0])
        hero["portrait_file"] = rel
        party[0] = hero
        state["party"] = party
    state["character_portrait_path"] = dest
    state["portrait_asset_path"] = rel
    return rel


def wizard_portrait_staging_path() -> str:
    return os.path.join(FUGASSA_ROOT, "wizard_portrait_staging.png")
