"""Match named characters in a scene beat and load stable visual identity for SD prompts."""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Any

_NAME_BOUNDARY = re.compile(r"[\w''\-]", re.UNICODE)


def _name_variants(name: str) -> list[str]:
    """Full name plus first-name alias for multi-word NPC names (Elara → Elara Voss)."""
    clean = str(name or "").strip()
    if not clean:
        return []
    parts = clean.split()
    variants = [clean]
    if len(parts) >= 2 and len(parts[0]) >= 3:
        variants.append(parts[0])
    return variants


def _boundary_pattern(term: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![{_NAME_BOUNDARY.pattern}]){re.escape(term)}(?![{_NAME_BOUNDARY.pattern}])",
        re.IGNORECASE,
    )


def _name_mentioned(name: str, haystack: str) -> bool:
    text = str(haystack or "")
    if not text:
        return False
    for variant in sorted(set(_name_variants(name)), key=len, reverse=True):
        if len(variant) < 2:
            continue
        if _boundary_pattern(variant).search(text):
            return True
    return False


def _mention_score(name: str, haystack: str) -> int:
    """Prefer full-name hits; otherwise count first-name alias mentions."""
    text = str(haystack or "")
    if not text:
        return 0
    variants = sorted(set(_name_variants(name)), key=len, reverse=True)
    for idx, variant in enumerate(variants):
        if len(variant) < 2:
            continue
        hits = len(_boundary_pattern(variant).findall(text))
        if hits:
            weight = 10 if idx == 0 else 5
            return hits * weight
    return 0


def _row_flag(row: sqlite3.Row | dict[str, Any] | None, key: str) -> bool:
    if row is None:
        return False
    if isinstance(row, dict):
        return bool(row.get(key))
    try:
        return bool(row[key])  # type: ignore[index]
    except (KeyError, TypeError, IndexError):
        return False


def _appearance_tags_from_row(row: sqlite3.Row | dict[str, Any] | None, *, role: str) -> str:
    def _get(key: str) -> str:
        if row is None:
            return ""
        if isinstance(row, dict):
            return str(row.get(key) or "").strip()
        try:
            return str(row[key] or "").strip()  # type: ignore[index]
        except (KeyError, TypeError):
            return ""

    parts: list[str] = []
    if role:
        parts.append(role)
    race = _get("race")
    if race:
        parts.append(race)
    prompt = _get("portrait_prompt")
    if prompt:
        parts.append(prompt[:220])
    return ", ".join(p for p in parts if p)


def _load_npc_row(conn: sqlite3.Connection, npc_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, name, race, class_role, portrait_prompt, portrait_path, backstory_summary, is_important
        FROM npcs WHERE id = ?
        """,
        (int(npc_id),),
    ).fetchone()


def _npc_rows_mentioned_globally(conn: sqlite3.Connection, haystack: str) -> list[sqlite3.Row]:
    """Match campaign NPCs by name anywhere in the beat — not only current location."""
    if not str(haystack or "").strip():
        return []
    rows = conn.execute(
        """
        SELECT id, name, race, class_role, portrait_prompt, portrait_path, backstory_summary, is_important
        FROM npcs
        WHERE TRIM(COALESCE(name, '')) != ''
        ORDER BY LENGTH(name) DESC, is_important DESC, id ASC
        """
    ).fetchall()
    candidates: list[tuple[sqlite3.Row, int]] = []
    for row in rows:
        name = str(row["name"] or "").strip()
        if name and _name_mentioned(name, haystack):
            candidates.append((row, _mention_score(name, haystack)))

    groups: dict[str, list[tuple[sqlite3.Row, int]]] = {}
    for row, score in candidates:
        parts = str(row["name"] or "").split()
        key = parts[0].lower() if len(parts) >= 2 else str(row["name"] or "").lower()
        groups.setdefault(key, []).append((row, score))

    matched: list[sqlite3.Row] = []
    for group in groups.values():
        if len(group) == 1:
            matched.append(group[0][0])
            continue
        group.sort(
            key=lambda item: (
                -item[1],
                -int(item[0]["is_important"] or 0),
                -len(str(item[0]["portrait_path"] or "")),
                -len(str(item[0]["name"] or "")),
            )
        )
        matched.append(group[0][0])
    return matched


def collect_scene_characters(
    *,
    state: dict[str, Any],
    db_path: str | None,
    narrative: str = "",
    player_action: str = "",
    include_player: bool = True,
) -> list[dict[str, Any]]:
    """
    Return the hero and scene NPCs to keep visually consistent in SD prompts.

    Fugassa chat scenes are third-person cinematic shots: the player hero is
    included by default, plus named or important NPCs present in the beat.

    NPC resolution order:
    1. Player hero (when ``include_player``).
    2. ``location_state.npc_details`` — important ambient cast at the current
       location, plus anyone named in the beat.
    3. Global SQL lookup — any campaign NPC whose name appears in the beat,
       even when they are no longer listed in the current location (flashbacks,
       dialog about someone elsewhere, older scene images).
    """
    haystack = f"{narrative}\n{player_action}".strip()

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    loc = state.get("location_state") if isinstance(state.get("location_state"), dict) else {}
    party = state.get("party") or []
    hero = party[0] if party and isinstance(party[0], dict) else {}

    conn: sqlite3.Connection | None = None
    if db_path:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            conn = None

    def _append(
        name: str,
        *,
        entity_type: str,
        entity_id: int,
        row: sqlite3.Row | dict[str, Any] | None,
        role: str,
        force: bool = False,
    ) -> None:
        key = name.strip().lower()
        if not key or key in seen:
            return
        if not force and not _name_mentioned(name, haystack):
            return
        seen.add(key)
        appearance = _appearance_tags_from_row(row or {}, role=role) if row else role
        portrait_path = ""
        if row is not None:
            if isinstance(row, dict):
                portrait_path = str(row.get("portrait_path") or "").strip()
            elif "portrait_path" in row.keys():
                portrait_path = str(row["portrait_path"] or "").strip()
        out.append(
            {
                "name": name,
                "entity_type": entity_type,
                "entity_id": int(entity_id),
                "appearance_tags": appearance,
                "portrait_path": portrait_path,
                "is_important": _row_flag(row, "is_important"),
            }
        )

    try:
        hero_name = str(hero.get("name") or "").strip()
        if include_player and hero_name:
            if conn:
                row = conn.execute(
                    """
                    SELECT id, name, race, class_name, portrait_prompt, portrait_path
                    FROM player_characters WHERE code = 'pc_hero' LIMIT 1
                    """,
                ).fetchone()
                if row:
                    _append(
                        hero_name,
                        entity_type="player_character",
                        entity_id=int(row["id"]),
                        row=row,
                        role="player hero",
                        force=True,
                    )
            else:
                _append(hero_name, entity_type="player_character", entity_id=0, row=None, role="player hero", force=True)

        for npc in loc.get("npc_details") or []:
            if not isinstance(npc, dict):
                continue
            name = str(npc.get("name") or "").strip()
            npc_id = int(npc.get("npc_id") or 0)
            if not name or not npc_id:
                continue
            row = _load_npc_row(conn, npc_id) if conn else None
            role = str((row["class_role"] if row else "") or "npc").strip() or "npc"
            important = bool(row["is_important"]) if row and "is_important" in row.keys() else False
            mentioned = _name_mentioned(name, haystack)
            if not mentioned and not important:
                continue
            _append(name, entity_type="npc", entity_id=npc_id, row=row, role=role, force=True)

        if conn and haystack:
            for row in _npc_rows_mentioned_globally(conn, haystack):
                name = str(row["name"] or "").strip()
                if not name:
                    continue
                role = str(row["class_role"] or "npc").strip() or "npc"
                _append(
                    name,
                    entity_type="npc",
                    entity_id=int(row["id"]),
                    row=row,
                    role=role,
                    force=True,
                )
    finally:
        if conn:
            conn.close()

    return out


def classify_scene_cast(characters: list[dict[str, Any]]) -> dict[str, list[str]]:
    """
    Split scene cast for chat metadata / LLM context.

    Player hero is always secondary; NPCs in the beat are primary.
    """
    primary: list[str] = []
    secondary: list[str] = []
    for ch in characters:
        name = str(ch.get("name") or "").strip()
        if not name:
            continue
        if ch.get("entity_type") == "player_character":
            secondary.append(name)
        else:
            primary.append(name)
    return {"primary": primary, "secondary": secondary}


def format_scene_cast_for_llm(scene_cast: dict[str, Any] | None) -> str:
    """Machine-readable cast line prepended to GM chat rows in LLM history."""
    if not isinstance(scene_cast, dict):
        return ""
    primary = [str(n).strip() for n in (scene_cast.get("primary") or []) if str(n).strip()]
    secondary = [str(n).strip() for n in (scene_cast.get("secondary") or []) if str(n).strip()]
    if not primary and not secondary:
        return ""
    parts: list[str] = []
    if primary:
        parts.append(f"primary: {', '.join(primary)}")
    if secondary:
        parts.append(f"secondary: {', '.join(secondary)}")
    return f"[Scene cast — {'; '.join(parts)}]"


def scene_cast_metadata(
    *,
    state: dict[str, Any],
    db_path: str | None,
    narrative: str = "",
    player_action: str = "",
) -> dict[str, list[str]]:
    """Resolve cast roles for an active scene beat."""
    characters = collect_scene_characters(
        state=state,
        db_path=db_path,
        narrative=narrative,
        player_action=player_action,
    )
    return classify_scene_cast(characters)


_PORTRAIT_TAG_LEAK = re.compile(
    r"\b(?:single character(?: portrait)?|waist-up portrait|head and shoulders|close-up portrait|"
    r"portrait shot|solo portrait|character portrait|professional character art)\b",
    re.IGNORECASE,
)


def split_visual_focal_and_supporting(
    characters: list[dict[str, Any]],
    *,
    narrative: str = "",
    player_action: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """
    SD focal subject: NPCs in the beat are usually primary; player hero is observer/supporting
    unless the beat centers on the player's action.

    Aligns SD prompts with classify_scene_cast (GM metadata), not player=always-hero.
    """
    player: dict[str, Any] | None = None
    npcs: list[dict[str, Any]] = []
    for ch in characters:
        if ch.get("entity_type") == "player_character":
            player = ch
        else:
            npcs.append(ch)

    haystack = f"{narrative}\n{player_action}".strip()

    if npcs:
        scored_npcs = sorted(
            npcs,
            key=lambda c: _mention_score(str(c.get("name") or ""), haystack),
            reverse=True,
        )
        best_npc = scored_npcs[0]
        best_npc_score = _mention_score(str(best_npc.get("name") or ""), haystack)
        player_score = _mention_score(str(player.get("name") or ""), haystack) if player else 0
        # Player-led action beat (confrontation, movement) — keep player focal unless an NPC dominates.
        if player and player_score > 0 and player_score >= max(best_npc_score, 1):
            hero = player
            supporting = scored_npcs
        else:
            hero = best_npc
            supporting = scored_npcs[1:] + ([player] if player else [])
        return hero, supporting
    if player:
        return player, []
    return None, []


def split_hero_and_supporting(
    characters: list[dict[str, Any]],
    *,
    narrative: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Player hero vs NPC/supporting cast — delegates to visual focal split when narrative given."""
    if narrative.strip():
        return split_visual_focal_and_supporting(characters, narrative=narrative)
    hero: dict[str, Any] | None = None
    supporting: list[dict[str, Any]] = []
    for ch in characters:
        if ch.get("entity_type") == "player_character" and hero is None:
            hero = ch
        else:
            supporting.append(ch)
    return hero, supporting


def _character_line(ch: dict[str, Any]) -> str:
    name = str(ch.get("name") or "").strip()
    if not name:
        return ""
    tags = str(ch.get("appearance_tags") or "").strip()
    tags = _PORTRAIT_TAG_LEAK.sub("", tags)
    tags = re.sub(r",\s*,", ", ", tags).strip(" ,")
    return f"- {name}: {tags or 'keep consistent with prior portraits'}"


def cast_prompt_stats(cast_block: str) -> tuple[bool, int, int]:
    """Parse formatted cast block → (has_hero, supporting_count, total_cast)."""
    text = str(cast_block or "")
    has_hero = False
    supporting = 0
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("HERO"):
            section = "hero"
            has_hero = True
            continue
        if upper.startswith("SUPPORTING"):
            section = "supporting"
            continue
        if stripped.startswith("- ") and section == "supporting":
            supporting += 1
    total = (1 if has_hero else 0) + supporting
    if total == 0 and text.strip():
        total = max(1, len([ln for ln in text.splitlines() if ln.strip().startswith("-")]))
    return has_hero, supporting, max(1, total)


def format_characters_for_scene_prompt(
    characters: list[dict[str, Any]],
    *,
    narrative: str = "",
    player_action: str = "",
) -> str:
    """Structured cast block: focal NPC as HERO; player hero usually supporting/observer."""
    if not characters:
        return ""
    hero, supporting = split_visual_focal_and_supporting(
        characters,
        narrative=narrative,
        player_action=player_action,
    )
    parts: list[str] = []
    if hero:
        parts.append("HERO (main focal subject — foreground, largest figure, match portrait exactly):")
        line = _character_line(hero)
        if line:
            parts.append(line)
    if supporting:
        parts.append(
            "SUPPORTING CAST (separate distinct figures — midground or background, "
            "smaller than hero, never merged with hero):"
        )
        for ch in supporting[:3]:
            line = _character_line(ch)
            if line:
                parts.append(line)
    return "\n".join(parts)


def primary_portrait_reference(characters: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First cast member with a portrait (player hero preferred, then NPCs)."""
    for prefer in ("player_character", "npc"):
        for ch in characters:
            if ch.get("entity_type") != prefer:
                continue
            path = str(ch.get("portrait_path") or "").strip()
            if path:
                return ch
    return None


def scene_cast_for_turn(
    *,
    state: dict[str, Any],
    db_path: str | None,
    turn_number: int,
    narrative: str = "",
    player_action: str = "",
) -> dict[str, str]:
    """Build cast block + portrait hints for a chat-scene asset prompt_seed."""
    if db_path and not player_action:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT player_text, ai_text FROM turn_history WHERE turn_number = ?",
                (int(turn_number),),
            ).fetchone()
            conn.close()
            if row:
                player_action = str(row["player_text"] or "")[:600]
                if not narrative:
                    from titan.fugassa.gm_response_parser import extract_current_scene_narrative

                    narrative = extract_current_scene_narrative(str(row["ai_text"] or ""))[:2000]
        except sqlite3.Error:
            pass

    characters = collect_scene_characters(
        state=state,
        db_path=db_path,
        narrative=narrative,
        player_action=player_action,
    )
    out: dict[str, str] = {}
    if characters:
        out["scene_characters"] = format_characters_for_scene_prompt(
            characters,
            narrative=narrative,
            player_action=player_action,
        )
        ref = primary_portrait_reference(characters)
        if ref and ref.get("portrait_path"):
            out["scene_portrait_ref"] = str(ref["portrait_path"])
            out["scene_portrait_entity"] = f"{ref.get('entity_type')}:{ref.get('entity_id')}"
    if narrative:
        out["scene_narrative"] = narrative
    if player_action:
        out["player_action"] = player_action
    return out


def resolve_generated_portrait_path(generated_root: str, rel_path: str) -> str | None:
    """Return absolute portrait file path when it exists under generated/."""
    rel = str(rel_path or "").strip()
    if not rel or not generated_root:
        return None
    abs_path = rel if os.path.isabs(rel) else os.path.join(generated_root, rel)
    return abs_path if os.path.isfile(abs_path) else None
