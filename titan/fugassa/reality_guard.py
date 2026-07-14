"""Reality Guard — ADR §C1.

Player chat is intent/attempt, never a canonical state patch. This module
classifies raw player text *before* any GM call so that a player cannot
narrate world facts, NPC internal states, retcons, or genre-breaking events
into existence — only the engine (dice, DB, quest/combat resolvers) can do
that.

This is the "pravidla first" half of ADR's hybrid model. The rules below are
deliberately high-precision (favour missing an edge case over blocking a
normal player action) and, where possible, cross-check against the actual
DB state rather than guessing from text alone. A future LLM escalation for
ambiguous cases is a natural extension point (`evaluate()` return contract
already supports adding a "review" verdict) but is intentionally not wired
here — this layer must stay usable without any LLM round-trip.
"""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Any

_NAME = r"[A-Z][a-zA-Z'\-]{1,30}(?:\s+[A-Z][a-zA-Z'\-]{1,30})?"

_NPC_DEAD_RE = re.compile(rf"\b({_NAME})\s+(?:is|was)\s+(?:now\s+)?dead\b")
_NPC_ALIVE_RE = re.compile(rf"\b({_NAME})\s+(?:is|was)\s+(?:now\s+)?alive\b")
_NPC_LOVES_RE = re.compile(rf"\b({_NAME})\s+(?:now\s+)?(?:loves?|trusts?|worships?|adores?)\s+me\b")
_NPC_GIVES_RE = re.compile(rf"\b({_NAME})\s+(?:gives?|hands?|grants?)\s+me\b")
_NPC_AGREES_RE = re.compile(rf"\b({_NAME})\s+(?:agrees?(?:\s+to\s+everything)?|is convinced|surrenders?|joins me|becomes my ally)\b")

_WORLD_FACT_RE = re.compile(
    r"\b(?:everyone|everybody|the whole (?:town|city|kingdom|village|realm|world))\b"
    r"(?:\s+\w+){0,4}?\s+(?:now\s+)?(?:believes?|thinks?|knows?|considers?)\b",
    re.I,
)
_INSTANT_OUTCOME_RE = re.compile(
    r"\b(?:instantly|immediately|automatically)\s+"
    r"(?:kill|defeat|persuade|convince|become|gain|win|solve|complete)\w*\b",
    re.I,
)
_ALREADY_HAVE_RE = re.compile(rf"\bI\s+(?:now\s+)?(?:have|possess|own|hold)\s+(?:the\s+)?({_NAME})\b")

_RETCON_RE = re.compile(
    r"\bactually,?\s+i\s+(?:had|was|secretly)\b|\bin truth,?\s+i\s+(?:had|was|secretly)\b"
    r"|\bturns out\s+i\s+(?:had|was|secretly)\b|\bi secretly\b.{0,40}\ball along\b"
    r"|\bi had already\b|\bthe whole time,?\s+i\b|\bunbeknownst to (?:everyone|you|them)\b",
    re.I,
)

_THEME_FORBIDDEN: dict[str, set[str]] = {
    "fantasy": {"laser gun", "spaceship", "cyborg", "nuclear warhead", "smartphone", "the internet", "machine gun"},
    "sci-fi": {"dragon rider", "wizard tower", "royal knight", "magic sword", "elven"},
    "sci_fi": {"dragon rider", "wizard tower", "royal knight", "magic sword", "elven"},
    "scifi": {"dragon rider", "wizard tower", "royal knight", "magic sword", "elven"},
    "cyberpunk": {"dragon rider", "wizard tower", "magic sword", "elven"},
    "medieval": {"laser gun", "spaceship", "cyborg", "nuclear warhead", "smartphone", "the internet", "machine gun"},
}


def _theme_conflict(text: str, theme: str) -> str | None:
    key = str(theme or "").strip().lower().replace(" ", "_")
    forbidden = _THEME_FORBIDDEN.get(key)
    if not forbidden:
        return None
    low = text.lower()
    for kw in forbidden:
        if kw in low:
            return kw
    return None


def _connect(db_path: str) -> sqlite3.Connection | None:
    if not db_path or not os.path.isfile(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _reality_mode(conn: sqlite3.Connection | None, state: dict[str, Any]) -> str:
    if conn is not None:
        try:
            row = conn.execute("SELECT reality_mode FROM campaign_settings WHERE id = 1").fetchone()
            if row and row["reality_mode"]:
                return str(row["reality_mode"])
        except sqlite3.Error:
            pass
    return str(state.get("reality_mode") or "simulation")


def _lookup_npc(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, name, status FROM npcs WHERE lower(name) = lower(?) LIMIT 1",
        (name.strip(),),
    ).fetchone()


def _npc_trust(conn: sqlite3.Connection, npc_id: int) -> int:
    row = conn.execute(
        "SELECT trust FROM npc_relationships WHERE source_npc_id = ? AND target_type = 'player'",
        (npc_id,),
    ).fetchone()
    return int(row["trust"]) if row and row["trust"] is not None else 0


def _reject(classification: str, reason: str, reframe_hint: str) -> dict[str, Any]:
    return {
        "classification": classification,
        "verdict": "reject",
        "reason": reason,
        "reframe_hint": reframe_hint,
    }


def evaluate(player_text: str, state: dict[str, Any], db_path: str | None) -> dict[str, Any]:
    """Classify a player's chat line for Reality Guard purposes.

    Returns `{}` when the line is a normal action/speech attempt (allowed).
    Returns a reject payload — `{classification, verdict: "reject", reason,
    reframe_hint}` — when the line tries to declare world/NPC state, retcon
    history, or break genre coherence. This never mutates state or blocks the
    mechanical resolvers in `turn_resolver`; it only produces metadata that
    `gm_instruction` / the GM prompt uses to keep the LLM from treating the
    declaration as canon, and that the archivist can use to avoid persisting
    the fabricated claim as a memory/event.
    """
    text = str(player_text or "")
    if not text.strip():
        return {}

    conn = _connect(db_path) if db_path else None
    try:
        mode = _reality_mode(conn, state)
        if mode == "sandbox":
            return {"classification": "sandbox", "verdict": "allow", "reality_mode": "sandbox"}

        for rx, kind in (
            (_NPC_DEAD_RE, "dead"),
            (_NPC_ALIVE_RE, "alive"),
            (_NPC_LOVES_RE, "loves"),
            (_NPC_GIVES_RE, "gives"),
            (_NPC_AGREES_RE, "agrees"),
        ):
            m = rx.search(text)
            if not m:
                continue
            name = m.group(1).strip()
            npc = _lookup_npc(conn, name) if conn else None
            if kind == "dead" and (not npc or npc["status"] != "dead"):
                return _reject(
                    "declare_npc_state",
                    f"Player declared '{name}' dead, but the DB does not confirm this.",
                    "The NPC's fate is decided by the engine (combat/quest), not by the player's words. "
                    "Narrate the attempt or the NPC's actual current state instead.",
                )
            if kind == "alive" and npc and npc["status"] == "dead":
                return _reject(
                    "declare_npc_state",
                    f"Player declared '{name}' alive, but the DB says dead.",
                    "The dead do not return because the player says so.",
                )
            if kind in ("loves", "gives", "agrees"):
                if not npc:
                    return _reject(
                        "declare_npc_state",
                        f"Player asserted '{name}' already {kind} them, but no such NPC exists in the DB yet.",
                        "This relationship/gift hasn't happened in the simulation — narrate it as an attempt, "
                        "or let the NPC be introduced properly first.",
                    )
                trust = _npc_trust(conn, npc["id"])
                if kind == "loves" and trust < 5:
                    return _reject(
                        "declare_npc_state",
                        f"Player declared '{name}' loves/trusts them outright, but DB trust is only {trust}.",
                        "Relationship inertia (ADR §D): trust like this must be earned through play, not declared.",
                    )
                if kind in ("gives", "agrees") and trust < 0:
                    return _reject(
                        "declare_npc_state",
                        f"Player declared '{name}' hands over an item / agrees, but the relationship is negative ({trust}).",
                        "A hostile or distrustful NPC doesn't cooperate just because the player says so.",
                    )

        if _WORLD_FACT_RE.search(text):
            return _reject(
                "declare_world_fact",
                "Player asserted a settled public opinion/fact about the world as already true.",
                "Reputation spreads through renown/witness mechanics (ADR §E2), never by direct player declaration.",
            )
        if _INSTANT_OUTCOME_RE.search(text):
            return _reject(
                "declare_world_fact",
                "Player declared an instantaneous mechanical outcome (kill/persuade/win/complete) without a roll.",
                "Narrate the attempt; the engine (combat/social/quest resolvers) decides the outcome.",
            )
        m = _ALREADY_HAVE_RE.search(text)
        if m and conn:
            item_name = m.group(1).strip()
            row = conn.execute(
                "SELECT id FROM items WHERE lower(name) = lower(?) AND owner_type = 'player_character' AND quantity > 0",
                (item_name,),
            ).fetchone()
            if not row:
                return _reject(
                    "declare_world_fact",
                    f"Player declared possession of '{item_name}', which is not in the DB inventory.",
                    "Items only enter inventory via loot/reward/engine grant, never by player declaration.",
                )

        if _RETCON_RE.search(text):
            return _reject(
                "retcon",
                "Player asserted a hidden past action/secret that retroactively rewrites established history.",
                "History is what happened in play. Reframe this as a new declaration going forward, "
                "not a rewrite of the past.",
            )

        theme = str((state.get("world_profile") or {}).get("theme") or "")
        conflict = _theme_conflict(text, theme)
        if conflict:
            return _reject(
                "impossible",
                f"Player action references '{conflict}', which conflicts with the campaign theme '{theme}'.",
                "Reframe within the established genre, or treat it as a hallucination/dream/misunderstanding "
                "if narratively interesting.",
            )

        return {}
    finally:
        if conn is not None:
            conn.close()
