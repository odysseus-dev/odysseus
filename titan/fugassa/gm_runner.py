"""GM prompt assembly for gameplay (ports GMPromptBuilder.gd)."""

from __future__ import annotations

import os
from typing import Any

from titan.fugassa.paths import GM_TEMPLATES_DIR

# ADR §7 rolling window — last 15 pairs (player+GM msg = 1 turn_history row,
# but `chat_history` stores each side as its own entry, so 15 pairs = 30
# entries). Anything older only reaches the GM via the campaign digest block.
ROLLING_WINDOW_MESSAGES = 30

CORE_TEMPLATES = [
    "gm_core.txt",
    "gm_system_context.txt",
    "gm_output_format.txt",
    "gm_time.txt",
    "gm_qwen_boost.txt",
    "gm_world.txt",
]

_FREEFORM_PLAYSTYLES = frozenset({"slice_of_life", "mystery"})


def _load_template(name: str) -> str:
    path = os.path.join(GM_TEMPLATES_DIR, name)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def _game_state_is_freeform(gs: dict[str, Any]) -> bool:
    fw = str(gs.get("playstyle_framework") or "").strip().lower()
    if fw == "freeform":
        return True
    ps = str(gs.get("playstyle") or "").strip().lower()
    return ps in _FREEFORM_PLAYSTYLES


def _party_summary(party: list[Any]) -> str:
    if not party:
        return "[]"
    names = [str((m or {}).get("name", "?")) for m in party if isinstance(m, dict)]
    return f"[{', '.join(names)}]"


def _inv_summary(inv: dict[str, Any]) -> str:
    shared = inv.get("shared") or []
    if not shared:
        return "[]"
    items: list[str] = []
    for it in shared:
        if not isinstance(it, dict):
            continue
        n = str(it.get("name", "?"))
        q = int(it.get("qty", 1))
        items.append(f"{n} x{q}")
    return f"[{', '.join(items)}]"


def _gear_loadout_summary(gs: dict[str, Any]) -> str:
    cs = gs.get("character_sheet") or {}
    stable = cs.get("stable_sheet") or {}
    inv = stable.get("inventory") or {}
    w = str(inv.get("weapon") or "").strip()
    a = str(inv.get("armor") or "").strip()
    if not w and not a:
        return "(see WORLD DATA / equipped)"
    return f"{w or '?'} / {a or '?'}"


def _character_sheet_summary(gs: dict[str, Any]) -> str:
    cs = gs.get("character_sheet") or {}
    stable = cs.get("stable_sheet") or {}
    derived = cs.get("derived") or {}
    llm = cs.get("llm_summary") or {}
    parts: list[str] = []
    if llm.get("spell_summary"):
        parts.append(str(llm["spell_summary"]))
    elif stable.get("spellcasting"):
        sc = stable["spellcasting"]
        cantrips = sc.get("cantrips") or []
        spells = sc.get("spells_known") or []
        if cantrips:
            parts.append(f"Cantrips: {', '.join(cantrips)}")
        if spells:
            parts.append(f"Spells: {', '.join(spells)}")
        if sc.get("save_dc"):
            parts.append(f"Spell DC {sc['save_dc']}")
    if llm.get("feature_summary"):
        parts.append(f"Features/traits/feats: {llm['feature_summary']}")
    if derived.get("passive_perception"):
        parts.append(f"Passive Perception {derived['passive_perception']}")
    return "; ".join(parts) if parts else "(no extended sheet data)"


def _build_three_layer_context(gs: dict[str, Any]) -> str:
    lines: list[str] = []
    loc = gs.get("location_state") or {}
    player = gs.get("player") or {}
    px, py, pz = int(player.get("x", 0)), int(player.get("y", 0)), int(player.get("z", 0))
    lines.append(f"Current cell ({px}, {py}, {pz}):")
    lines.append(f"  name: {loc.get('name', '?')}")
    lines.append(f"  description: {loc.get('description', '')}")
    if loc.get("location_id"):
        lines.append(f"  location_id: {loc.get('location_id')}")
    if player.get("sublocation_id"):
        lines.append(f"  interior_sublocation: yes (grid anchor {px}, {py}, {pz})")
    visible_npcs = [str(n) for n in (loc.get("npcs") or []) if str(n).strip()]
    hidden_npcs = [str(n) for n in (loc.get("hidden_npcs") or []) if str(n).strip()]
    lines.append(f"  npcs (visible): {visible_npcs or ['none']}")
    if hidden_npcs:
        lines.append(
            "  hidden_npcs (concealed — may foreshadow, do not fully reveal until investigation): "
            f"{hidden_npcs}"
        )
    for key in ("enemies", "loot"):
        val = loc.get(key) or []
        if val:
            lines.append(f"  {key}: {val}")
    lines.append("")

    cache = gs.get("cell_location_cache") or {}
    neighbors: list[str] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if dx == dy == dz == 0:
                    continue
                key = f"{px + dx},{py + dy},{pz + dz}"
                if key in cache and isinstance(cache[key], dict):
                    neighbors.append(f"  {key}: {cache[key].get('name', '?')}")
    if neighbors:
        lines.append("Nearby cells (from cache):")
        lines.extend(neighbors)
        lines.append("")

    profile = gs.get("world_profile") or {}
    wt = gs.get("world_time") or {"day": 1, "hour": 8}
    lines.append("World:")
    lines.append(f"  world_name: {profile.get('world_name', gs.get('save_name', '?'))}")
    lines.append(f"  theme: {profile.get('theme', 'Fantasy')}")
    lines.append(f"  playstyle: {gs.get('playstyle', '')}")
    lines.append(f"  playstyle_framework: {gs.get('playstyle_framework', 'rules_based')}")
    if _game_state_is_freeform(gs):
        lines.append("  rules_mode: (inactive — freeform playstyle)")
        lines.append("  resolution_mode: (inactive — freeform playstyle)")
    else:
        lines.append(f"  rules_mode: {gs.get('rules_mode', '5e-style')}")
        lines.append(f"  resolution_mode: {gs.get('resolution_mode', 'dice')}")
    lines.append(f"  time: day {wt.get('day', 1)}, hour {wt.get('hour', 8)}")
    lines.append(f"  party: {_party_summary(gs.get('party') or [])}")
    from titan.fugassa import inventory_display

    wallet = inventory_display.wallet_from_state(gs)
    if wallet:
        wallet_line = ", ".join(f"{row['qty']} {row['name']}" for row in wallet)
        lines.append(f"  wallet: {wallet_line}")
    gear = inventory_display.backpack_gear_from_state(gs)
    if gear:
        lines.append(f"  backpack gear: {_inv_summary({'shared': gear})}")
    else:
        lines.append("  backpack gear: []")
    lines.append(f"  turn: {gs.get('turn', 0)}")
    return "\n".join(lines)


def _build_campaign_lore_block(gs: dict[str, Any]) -> str:
    wp = gs.get("world_profile") or {}
    opening = str(wp.get("opening_hook") or "").strip()
    world = str(wp.get("world_information") or "").strip()
    party = gs.get("party") or []
    back = ""
    if party and isinstance(party[0], dict):
        back = str(party[0].get("background") or "").strip()
    lines = ["CAMPAIGN LORE (from setup — honor continuity)"]
    if opening:
        lines.extend(["PRIMARY — OPENING SITUATION (highest priority for the first scene):", opening])
    else:
        lines.append("PRIMARY — OPENING SITUATION: (not set — infer a strong opening from world + character below)")
    lines.append("")
    lines.append("WORLD INFORMATION:" if world else "WORLD INFORMATION: (not set)")
    if world:
        lines.append(world)
    lines.append("")
    lines.append("CHARACTER BACKSTORY (supporting):" if back else "CHARACTER BACKSTORY: (not set)")
    if back:
        lines.append(back)
    lines.append("")
    lines.append(f"LIGHT CONTEXT — gear/loadout (supporting only): {_gear_loadout_summary(gs)}")
    lines.append(f"LIGHT CONTEXT — character sheet (spells/features): {_character_sheet_summary(gs)}")
    lines.append(f"LIGHT CONTEXT — shared inventory highlights (supporting only): {_inv_summary(gs.get('inventory') or {})}")
    return "\n".join(lines)


def _build_rules_and_resolution_block(gs: dict[str, Any]) -> str:
    if _game_state_is_freeform(gs):
        ps = str(gs.get("playstyle") or "").strip()
        out = "BEHAVIOR GUARANTEE: follow this block.\n"
        out += f"PLAYSTYLE FRAMEWORK: freeform ({ps})\n" if ps else "PLAYSTYLE FRAMEWORK: freeform\n"
        out += "- Resolve through narrative consistency only.\n"
        out += "- Do NOT use DCs, dice, or D&D mechanical resolution.\n"
        return out

    rules_mode = str(gs.get("rules_mode") or "5e-style").strip().lower()
    resolution_mode = str(gs.get("resolution_mode") or "dice").strip().lower()
    narrate_mode = resolution_mode == "narrative"
    if rules_mode == "homebrew":
        rules_block = "RULES MODE: homebrew (base = D&D 5e)\n- Default to D&D 5e mechanics.\n"
    else:
        rules_block = "RULES MODE: 5e-style (strict)\n- Use D&D 5th Edition mechanics.\n"
    resolution_block = f"RESOLUTION MODE: {resolution_mode}\nnarrate_mode: {'true' if narrate_mode else 'false'}\n"
    if narrate_mode:
        resolution_block += "- Do NOT request dice rolls.\n"
    else:
        resolution_block += "- When uncertain, you may ask for a relevant d20-based roll.\n"
    return f"BEHAVIOR GUARANTEE: follow this block.\n{rules_block}\n{resolution_block}"


def opening_bootstrap_user_message() -> str:
    return (
        "[Game — internal] The player just entered the game. There is no prior GM message in this session "
        "and no player action yet. Write the full first reply following OUTPUT FORMAT: timestamp table, Recap "
        "(story begins here), Current scene (3–4 paragraphs anchored on PRIMARY — OPENING SITUATION in "
        "CAMPAIGN LORE), Round summary, Suggestions (3–4 bullets), then the closing line "
        "\"What do you do next?\" Do not include this tag in your answer."
    )


def _opening_bootstrap_instructions() -> str:
    return """OPENING SESSION (first GM message only)
The player has not typed an action yet. Follow OUTPUT FORMAT. Recap must state this is the starting beat. Current scene must be driven mainly by PRIMARY — OPENING SITUATION; world information and backstory are supporting. Gear/inventory are light color only. Do not lead with currency unless the opening situation already focuses on trade."""


def build_system_prompt(
    game_state: dict[str, Any],
    gm_notes: str = "",
    *,
    opening_bootstrap: bool = False,
    extra_system_block: str = "",
) -> str:
    parts: list[str] = []
    for name in CORE_TEMPLATES:
        content = _load_template(name)
        if content:
            parts.append(content)
    if gm_notes.strip():
        parts.append(f"\nGM NOTES (campaign-specific):\n{gm_notes.strip()}")
    if extra_system_block.strip():
        parts.append("\n---\n\n" + extra_system_block.strip())
    parts.append("\n---\n\nWORLD DATA (canonical — do not contradict):\n")
    parts.append(_build_three_layer_context(game_state))
    from titan.fugassa.context_builder import build_world_state_snapshot_block
    from titan.fugassa.save_store import game_db_path

    db_path = game_db_path(str(game_state.get("save_id") or "")) if game_state.get("save_id") else None
    if not db_path or not os.path.isfile(db_path):
        db_path = None
    snapshot_block = build_world_state_snapshot_block(game_state, db_path=db_path)
    if snapshot_block.strip():
        parts.append("\n---\n\n" + snapshot_block.strip())
    from titan.fugassa.context_builder import build_party_context_block

    party_block = build_party_context_block(game_state, db_path)
    if party_block.strip():
        parts.append("\n---\n\n" + party_block.strip())
    parts.append("\n---\n\n" + _build_campaign_lore_block(game_state))
    parts.append("\n---\n\n" + _build_rules_and_resolution_block(game_state))
    if opening_bootstrap:
        parts.append("\n---\n\n" + _opening_bootstrap_instructions())
    return "\n\n".join(parts)


def _chat_entry_content(entry: dict[str, Any]) -> str:
    content = str(entry.get("content") or "")
    if str(entry.get("role") or "") != "assistant":
        return content
    from titan.fugassa.scene_character_context import format_scene_cast_for_llm

    cast_line = format_scene_cast_for_llm(entry.get("scene_cast"))
    if cast_line:
        return f"{cast_line}\n\n{content}"
    return content


def build_messages_for_history(
    game_state: dict[str, Any],
    *,
    gm_notes: str = "",
    opening_bootstrap: bool = False,
    extra_system_block: str = "",
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": build_system_prompt(
                game_state,
                gm_notes,
                opening_bootstrap=opening_bootstrap,
                extra_system_block=extra_system_block,
            ),
        },
    ]
    if opening_bootstrap:
        messages.append({"role": "user", "content": opening_bootstrap_user_message()})
        return messages
    history = list(game_state.get("chat_history") or [])
    # ADR §7 "Rolling window: 15 pairs v promptu" — older turns are only
    # represented via the campaign digest block (see `campaign_digest.py`),
    # never resent verbatim once they've rolled out of this window.
    if len(history) > ROLLING_WINDOW_MESSAGES:
        history = history[-ROLLING_WINDOW_MESSAGES:]
    for entry in history:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "user")
        content = _chat_entry_content(entry)
        if content.strip():
            messages.append({"role": role, "content": content})
    return messages
