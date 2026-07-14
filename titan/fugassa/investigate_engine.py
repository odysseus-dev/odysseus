"""Investigate — real DC roll, permanent per-location exhaustion, hidden_* reveal.

Extracted out of `turn_resolver.py`'s ad-hoc "search" branch (which used to
roll a deterministic hash "pseudo-roll" and never persisted state) so both
the chat-driven "I investigate..." path and the dedicated Investigate popup
(`game_session.investigate`) share one real implementation.

Exhaustion is **permanent** per `(location_key, search_type)` — once a type
has been searched at a location it stays exhausted there until something
else explicitly changes it (no auto time-based reset). This prevents
spamming the same search for free re-rolls while still letting the player
search different types, or the same type at a different location.

`location_state` may carry `hidden_loot` / `hidden_npcs` / `hidden_enemies`
lists that the GM/archivist seeds over time (empty by default — this engine
never invents loot from nothing). A successful search of a matching type
moves those entries into the visible `loot` / `npcs` / `enemies` lists; if
nothing was seeded, a successful search still exhausts the type but may
legitimately find nothing new.
"""

from __future__ import annotations

import os
import random
import sqlite3
from typing import Any

from titan.fugassa import grid_engine
from titan.fugassa.dnd5e_options import ability_modifier

# "items" and "hidden" are deliberately separate search types (visible-loot
# vs. concealed/secret loot such as a hidden compartment) even though both
# reveal into the same `loot` list — they have different DCs and are
# exhausted independently, matching the popup's four checkboxes.
SEARCH_TYPES = ("items", "hidden", "npc", "enemy")

_DC_BY_TYPE: dict[str, int] = {"items": 10, "hidden": 15, "npc": 15, "enemy": 13}
_HIDDEN_KEY_BY_TYPE: dict[str, str] = {
    "items": "hidden_loot",
    "hidden": "hidden_loot",
    "npc": "hidden_npcs",
    "enemy": "hidden_enemies",
}
_VISIBLE_KEY_BY_TYPE: dict[str, str] = {"items": "loot", "hidden": "loot", "npc": "npcs", "enemy": "enemies"}

_SEARCH_TYPE_LABEL: dict[str, str] = {
    "items": "visible items",
    "hidden": "hidden caches",
    "npc": "hidden NPCs",
    "enemy": "hidden enemies",
}

DEFAULT_DURATION_MINUTES = 30
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 240


def location_key(state: dict[str, Any]) -> str:
    """Stable per-place key for `search_history` — combines the grid cell
    with the sublocation id (if the player is currently inside a room), so a
    room search never collides with the grid cell it sits on top of.
    """
    player = state.get("player") or {}
    map_code = str(player.get("map_code") or grid_engine.DEFAULT_MAP_CODE)
    key = grid_engine.coord_key(
        int(player.get("x", 0) or 0), int(player.get("y", 0) or 0), int(player.get("z", 0) or 0), map_code
    )
    sublocation_id = player.get("sublocation_id")
    return f"{key}#sub{int(sublocation_id)}" if sublocation_id else key


def _search_history(state: dict[str, Any]) -> dict[str, Any]:
    history = state.get("search_history")
    if not isinstance(history, dict):
        history = {}
        state["search_history"] = history
    return history


def already_searched(state: dict[str, Any], search_type: str, *, key: str | None = None) -> bool:
    history = _search_history(state)
    loc_entry = history.get(key or location_key(state)) or {}
    return bool(loc_entry.get(search_type))


def options_for_location(state: dict[str, Any]) -> dict[str, Any]:
    """Which search types are still available at the player's current
    location — drives the popup's checkbox disabled/"already searched" state.
    """
    key = location_key(state)
    history = _search_history(state)
    loc_entry = history.get(key) or {}
    types = []
    for t in SEARCH_TYPES:
        exhausted = bool(loc_entry.get(t))
        types.append(
            {
                "type": t,
                "label": _SEARCH_TYPE_LABEL[t],
                "dc": _DC_BY_TYPE[t],
                "available": not exhausted,
                "exhausted": exhausted,
                "last_result": loc_entry.get(t),
            }
        )
    return {"location_key": key, "types": types}


def _player_wis_and_proficiency(db_path: str | None) -> tuple[int, int]:
    """WIS modifier + proficiency bonus from the actual sheet (Investigation
    is an INT skill in 5e, but a bare Perception/Search check without a
    declared skill proficiency uses passive WIS — mirrors
    `combat_engine._player_attack_profile`'s "read the real sheet" pattern).
    """
    if not db_path or not os.path.isfile(db_path):
        return 0, 2
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT wis_score, proficiency_bonus FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return 0, 2
    wis_mod = ability_modifier(int(row["wis_score"] or 10))
    prof = int(row["proficiency_bonus"] or 2)
    return wis_mod, prof


def _reveal_hidden(location_state: dict[str, Any], search_type: str) -> list[Any]:
    hidden_key = _HIDDEN_KEY_BY_TYPE[search_type]
    visible_key = _VISIBLE_KEY_BY_TYPE[search_type]
    hidden = list(location_state.get(hidden_key) or [])
    if not hidden:
        return []
    visible = list(location_state.get(visible_key) or [])
    visible.extend(hidden)
    location_state[visible_key] = visible
    location_state[hidden_key] = []
    return hidden


def resolve_investigate(
    db_path: str | None,
    state: dict[str, Any],
    search_types: list[str],
    duration_minutes: int | None = None,
) -> dict[str, Any]:
    """Roll a real DC check per requested `search_type`, mark permanent
    per-location exhaustion, and reveal matching `hidden_*` content on
    success. Mutates `state` in place; caller is responsible for persisting
    it (SQL sync + `save_game_state`) and applying the returned
    `time_delta_minutes`, exactly like every other turn resolver.
    """
    requested = [t for t in (search_types or []) if t in SEARCH_TYPES]
    if not requested:
        requested = ["items"]
    duration = max(MIN_DURATION_MINUTES, min(MAX_DURATION_MINUTES, int(duration_minutes or DEFAULT_DURATION_MINUTES)))

    key = location_key(state)
    history = _search_history(state)
    loc_entry = dict(history.get(key) or {})
    location_state = dict(state.get("location_state") or {})

    wis_mod, prof_bonus = _player_wis_and_proficiency(db_path)
    turn = int(state.get("turn") or 0)

    results: dict[str, Any] = {}
    any_attempted = False
    any_revealed = False
    summary_lines: list[str] = []

    for search_type in requested:
        if loc_entry.get(search_type):
            results[search_type] = {
                "attempted": False,
                "already_searched": True,
                "summary": f"{_SEARCH_TYPE_LABEL[search_type].capitalize()} already searched here.",
            }
            summary_lines.append(results[search_type]["summary"])
            continue

        any_attempted = True
        roll = random.randint(1, 20)
        total = roll + wis_mod + prof_bonus
        dc = _DC_BY_TYPE[search_type]
        success = total >= dc
        revealed: list[Any] = []
        if success:
            revealed = _reveal_hidden(location_state, search_type)
            if revealed:
                any_revealed = True

        loc_entry[search_type] = {
            "turn": turn,
            "roll": roll,
            "total": total,
            "dc": dc,
            "success": success,
        }

        found_note = (
            f" Found {len(revealed)} new {_SEARCH_TYPE_LABEL[search_type]}."
            if revealed
            else " Nothing new found." if success else ""
        )
        line = (
            f"Search ({_SEARCH_TYPE_LABEL[search_type]}): d20={roll}+{wis_mod}(WIS)+{prof_bonus}(prof)={total} "
            f"vs DC {dc}: {'success' if success else 'failure'}.{found_note}"
        )
        results[search_type] = {
            "attempted": True,
            "already_searched": False,
            "roll": roll,
            "wis_mod": wis_mod,
            "proficiency_bonus": prof_bonus,
            "total": total,
            "dc": dc,
            "success": success,
            "revealed": revealed,
            "summary": line,
        }
        summary_lines.append(line)

    history[key] = loc_entry
    state["search_history"] = history
    state["location_state"] = location_state

    return {
        "location_key": key,
        "results": results,
        "revealed_any": any_revealed,
        "time_delta_minutes": duration if any_attempted else 0,
        "summary": " ".join(summary_lines) or "Nothing left to search here.",
    }
