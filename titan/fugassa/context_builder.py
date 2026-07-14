"""GM context assembly — turn_resolution first (ADR §G)."""

from __future__ import annotations

import os
import re
from typing import Any

from titan.fugassa import gm_runner, world_state_snapshot
from titan.fugassa.turn_resolution import TurnResolution

_CORRECTION_RE = re.compile(
    r"\b(based on|according to|actually|correction|wrong|not true|quest log|you said|i said)\b",
    re.I,
)


def _last_assistant_excerpt(state: dict[str, Any], *, limit: int = 1200) -> str:
    for entry in reversed(state.get("chat_history") or []):
        if isinstance(entry, dict) and entry.get("role") == "assistant":
            return str(entry.get("content") or "")[:limit]
    return ""


def build_party_context_block(state: dict[str, Any], db_path: str | None = None) -> str:
    """ADR §6.6 — companions present in party (SQL-enriched when db_path available)."""
    party = list(state.get("party") or [])
    if not party:
        return ""
    lines = ["PARTY (canonical — present companions and hero):"]
    conn = None
    if db_path and os.path.isfile(db_path):
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    try:
        for member in party:
            if not isinstance(member, dict):
                continue
            name = str(member.get("name") or "Unknown").strip()
            role = str(member.get("role") or "companion").strip().lower() or "companion"
            if role in ("player", "hero", ""):
                role = "hero"
            extras: list[str] = []
            if role == "hero":
                lvl = member.get("level")
                if lvl:
                    extras.append(f"L{lvl}")
            npc_code = str(member.get("npc_code") or member.get("code") or "").strip()
            if npc_code and role != "hero":
                extras.append(f"npc:{npc_code}")
                if conn:
                    row = conn.execute(
                        "SELECT backstory_summary, race, class_role FROM npcs WHERE code = ? LIMIT 1",
                        (npc_code,),
                    ).fetchone()
                    if row:
                        if row["race"] or row["class_role"]:
                            extras.append(
                                "/".join(x for x in (row["race"], row["class_role"]) if x)
                            )
                        if row["backstory_summary"]:
                            extras.append(str(row["backstory_summary"])[:120])
            line = f"- {name} ({role}"
            if extras:
                line += f"; {', '.join(extras)}"
            line += ")"
            lines.append(line)
    finally:
        if conn:
            conn.close()
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def build_world_state_snapshot_block(
    state: dict[str, Any],
    *,
    db_path: str | None = None,
) -> str:
    """ADR §6.6 — unified campaign state replacing fragmented meta blocks."""
    return world_state_snapshot.build_snapshot_text(db_path, state)


def build_property_context_block(state: dict[str, Any]) -> str:
    portfolio = state.get("property_portfolio") if isinstance(state.get("property_portfolio"), dict) else {}
    holdings = portfolio.get("holdings") if isinstance(portfolio.get("holdings"), list) else []
    if not holdings:
        wp = state.get("world_profile") or {}
        note = str(wp.get("property_note") or "").strip()
        if note:
            return f"PLAYER PROPERTY (canonical):\n- {note}"
        return ""
    lines = ["PLAYER PROPERTY (canonical):"]
    active = str(portfolio.get("active_residence_code") or "").strip()
    for h in holdings:
        if not isinstance(h, dict):
            continue
        code = h.get("code", "")
        marker = " (active residence)" if code and code == active else ""
        specs = h.get("specs") if isinstance(h.get("specs"), dict) else {}
        prestige = specs.get("prestige", "?")
        staff = h.get("staff_names") if isinstance(h.get("staff_names"), list) else []
        staff_note = f"; staff: {', '.join(staff)}" if staff else ""
        room_count = h.get("room_count")
        room_note = f"; {room_count} rooms" if room_count else ""
        lines.append(
            f"- {h.get('name', 'Property')} [{h.get('property_kind', 'holding')}, "
            f"{h.get('title_status', 'owned')}, prestige {prestige}]{marker}{room_note}{staff_note}: "
            f"{h.get('deed_summary', '').strip()}"
        )
    return "\n".join(lines)


def build_titles_context_block(state: dict[str, Any]) -> str:
    block = state.get("player_titles") if isinstance(state.get("player_titles"), dict) else {}
    titles = block.get("titles") if isinstance(block.get("titles"), list) else []
    if not titles:
        return ""
    active = str(block.get("active_display") or "").strip()
    lines = ["PLAYER TITLES (canonical — earned, do not invent new ones casually):"]
    if active:
        bonuses = block.get("bonuses") if isinstance(block.get("bonuses"), dict) else {}
        bonus_note = ""
        if bonuses.get("social_bonus") or bonuses.get("persuasion_bonus"):
            parts = []
            if bonuses.get("social_bonus"):
                parts.append(f"social +{bonuses['social_bonus']}")
            if bonuses.get("persuasion_bonus"):
                parts.append(f"persuasion +{bonuses['persuasion_bonus']}")
            bonus_note = f" [{' ,'.join(parts)}]"
        lines.append(f"- Active: {active}{bonus_note}")
    for t in titles[-5:]:
        if not isinstance(t, dict):
            continue
        lines.append(f"- {t.get('display', t.get('code', 'Title'))} (tier {t.get('impact_tier', 2)})")
    return "\n".join(lines)


def build_settlement_context_block(state: dict[str, Any], *, db_path: str | None = None) -> str:
    loc = state.get("location_state") if isinstance(state.get("location_state"), dict) else {}
    lines: list[str] = []
    settlement = str(loc.get("settlement_name") or "").strip()
    place = str(loc.get("place_label") or loc.get("name") or "").strip()
    if settlement:
        lines.append(f"Current settlement: {settlement}")
    if place:
        lines.append(f"Current place label: {place}")
    if db_path:
        from titan.fugassa.location_name_registry import prompt_block, seed_registry_from_locations

        block = prompt_block(seed_registry_from_locations(db_path, persist=False))
        if block:
            lines.append(block)
    if not lines:
        return ""
    return "NAMED PLACES (canonical geography):\n" + "\n".join(lines)


def build_quest_context_block(state: dict[str, Any]) -> str:
    quests = (state.get("quests") or {}).get("active") if isinstance(state.get("quests"), dict) else []
    if not quests:
        return ""
    lines = ["ACTIVE QUESTS (canonical — respect rewards and chain structure):"]
    for q in quests[:8]:
        if not isinstance(q, dict):
            continue
        scale = str(q.get("scale") or "standard")
        reward = str(q.get("rewards_preview") or "").strip()
        chain = str(q.get("chain_code") or "").strip()
        chain_note = f" [chain: {chain}]" if chain else ""
        deferred = " [reward deferred]" if q.get("rewards_deferred") else ""
        lines.append(f"- {q.get('name', 'Quest')} ({scale}){chain_note}{deferred}")
        if reward:
            lines.append(f"  Reward: {reward}")
        objs = [o for o in (q.get("objectives") or []) if isinstance(o, dict) and not o.get("hidden")]
        for o in objs[:4]:
            mark = "✓" if o.get("status") == "complete" else "○"
            lines.append(f"  {mark} {o.get('text', '')}")
    return "\n".join(lines)


def build_gm_messages(
    state: dict[str, Any],
    *,
    gm_notes: str = "",
    turn_resolution: TurnResolution | None = None,
    opening_bootstrap: bool = False,
    player_text: str = "",
    npc_brief_block: str = "",
    memory_block: str = "",
    pinned_facts_block: str = "",
    scene_summary_block: str = "",
    campaign_digest_block: str = "",
    chronicle_hint_block: str = "",
) -> list[dict[str, str]]:
    if opening_bootstrap or turn_resolution is None:
        return gm_runner.build_messages_for_history(
            state,
            gm_notes=gm_notes,
            opening_bootstrap=opening_bootstrap,
        )

    resolution_block = turn_resolution.to_prompt_yaml()
    extra_parts = [
        "TURN RESOLUTION (binding — narrate faithfully, do not contradict):\n"
        f"```yaml\n{resolution_block}\n```"
    ]
    if turn_resolution.gm_instruction:
        extra_parts.append(
            "REALITY GUARD (mandatory — do not comply with the rejected claim; "
            "narrate the player's attempt honestly instead, using the reframe hint):\n"
            f"{turn_resolution.gm_instruction}"
        )
    if turn_resolution.secret_gm_notes:
        extra_parts.append(
            "SECRET GM NOTES (§B5c — GM-only facade info; NEVER reveal this to the "
            "player in narration unless the turn resolution above shows an 'agenda' "
            "reveal event; use only to color tone/foreshadowing while playing the "
            "public facade straight):\n"
            f"{turn_resolution.secret_gm_notes}"
        )
    # ADR §5 ordering row 4 — per-NPC hexagon/goals/attitude brief comes right
    # before that same NPC's top-K memory block.
    if npc_brief_block.strip():
        extra_parts.append(npc_brief_block.strip())
    if memory_block.strip():
        extra_parts.append(memory_block.strip())
    # ADR §5 ordering rows 7-8 — pinned facts and scene summary come after
    # per-NPC memory (row 4), before rolling chat (row 9, handled separately
    # by `gm_runner.build_messages_for_history`'s own chat-history assembly).
    if pinned_facts_block.strip():
        extra_parts.append(pinned_facts_block.strip())
    if chronicle_hint_block.strip():
        extra_parts.append(chronicle_hint_block.strip())
    if scene_summary_block.strip():
        extra_parts.append(scene_summary_block.strip())
    # ADR §5 row 9 / §7 — condensed older history, right before the rolling
    # chat window itself (which `gm_runner` appends as the actual message list).
    if campaign_digest_block.strip():
        extra_parts.append(campaign_digest_block.strip())
    last_gm = _last_assistant_excerpt(state)
    if last_gm.strip():
        extra_parts.append(
            "ANTI-REPETITION (mandatory):\n"
            "- Never repeat more than one short phrase from your previous GM reply.\n"
            "- Advance the scene with new dialogue, consequences, or sensory detail.\n"
            "- If the player corrected a fact, confirm briefly — do not replay the whole scene.\n"
            f"Previous GM reply (do not recycle):\n{last_gm}"
        )
    if player_text.strip() and _CORRECTION_RE.search(player_text):
        extra_parts.append(
            "PLAYER CORRECTION DETECTED:\n"
            "- The player is fixing or citing established facts.\n"
            "- Acknowledge the correction in one or two sentences, then move forward.\n"
            "- Do NOT re-narrate the entire prior scene."
        )
    return gm_runner.build_messages_for_history(
        state,
        gm_notes=gm_notes,
        opening_bootstrap=False,
        extra_system_block="\n\n---\n\n".join(extra_parts),
    )
