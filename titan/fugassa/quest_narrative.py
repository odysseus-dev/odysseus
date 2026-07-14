"""Narrative quest adjudication — engine-owned completion from GM prose.

Custom objectives cannot be satisfied by player declarations alone. After each GM
turn, this module reads the canonical GM narrative (plus scene cast context) and
sets world flags that `quest_engine._evaluate_objective` checks on the next pass.

Archivist remains forbidden from writing quest status directly (ADR §H8).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from typing import Any

from titan.fugassa import world_flags
from titan.fugassa.scene_character_context import _name_mentioned
from titan.fugassa.turn_resolution import TurnResolution

LOG = logging.getLogger("titan.fugassa.quest_narrative")

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "for",
        "from",
        "her",
        "his",
        "house",
        "in",
        "of",
        "on",
        "or",
        "the",
        "their",
        "them",
        "this",
        "to",
        "toward",
        "with",
        "your",
    }
)

_COMPLETION_RE = re.compile(
    r"\b(?:"
    r"acknowledg(?:ed|ement|es|ing)|"
    r"agreed|agreement|"
    r"applied|application|"
    r"accepted|acceptance|"
    r"confirmed|confirmation|"
    r"contract(?:ed|s|ing)?|"
    r"formaliz(?:ed|es|ing|ation)|"
    r"formally|"
    r"presented|submitted|"
    r"secured|settled|sealed|signed|"
    r"complete(?:d|s|ing)?|"
    r"finaliz(?:ed|es|ing|ation)|"
    r"recorded|registered|"
    r"paid|payment"
    r")\b",
    re.I,
)

_OBJ_FLAG_PREFIX = "quest_obj"


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def objective_flag_key(quest_id: int, sort_order: int) -> str:
    return f"{_OBJ_FLAG_PREFIX}:{quest_id}:{sort_order}"


def _condition(obj: sqlite3.Row) -> dict[str, Any]:
    try:
        return json.loads(obj["condition_json"]) if obj["condition_json"] else {}
    except (TypeError, ValueError):
        return {}


def _keywords_from_description(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9']+", str(text or "").lower())
    out: list[str] = []
    for token in tokens:
        if len(token) < 4 or token in _STOPWORDS:
            continue
        if token not in out:
            out.append(token)
    return out


def _keyword_match_ratio(keywords: list[str], haystack: str) -> float:
    if not keywords:
        return 0.0
    text = str(haystack or "").lower()
    hits = sum(1 for kw in keywords if kw in text)
    return hits / len(keywords)


def _objective_signals(obj: sqlite3.Row) -> list[str]:
    cond = _condition(obj)
    explicit = cond.get("completion_signals") or cond.get("signals")
    if isinstance(explicit, list):
        return [str(s).strip().lower() for s in explicit if str(s).strip()]
    if isinstance(explicit, str) and explicit.strip():
        return [explicit.strip().lower()]
    return _keywords_from_description(obj["description_text"] or "")


def _narrative_confirms_objective(
    obj: sqlite3.Row,
    *,
    player_text: str,
    gm_prose: str,
) -> bool:
    """True when GM prose (not player claims alone) supports objective completion."""
    gm = str(gm_prose or "").strip()
    if len(gm) < 40:
        return False
    if not _COMPLETION_RE.search(gm):
        return False

    signals = _objective_signals(obj)
    if not signals:
        return False

    combined = f"{player_text}\n{gm_prose}"
    ratio = _keyword_match_ratio(signals, combined)
    min_ratio = float(_condition(obj).get("min_signal_ratio") or 0.45)
    if ratio < min_ratio:
        return False

    # Require at least one signal token in GM prose itself (not only player intent).
    gm_ratio = _keyword_match_ratio(signals, gm_prose)
    return gm_ratio >= max(0.25, min_ratio * 0.55)


def _resolve_scene_npc(scene_cast: dict[str, Any] | None, player_text: str, gm_prose: str) -> str | None:
    if not isinstance(scene_cast, dict):
        return None
    candidates = [str(n).strip() for n in (scene_cast.get("primary") or []) if str(n).strip()]
    best: tuple[int, str] | None = None
    haystack = f"{player_text}\n{gm_prose}"
    for name in candidates:
        if not _name_mentioned(name, haystack):
            continue
        score = len(name)
        if best is None or score > best[0]:
            best = (score, name)
    return best[1] if best else None


def enrich_turn_resolution_for_quests(
    db_path: str | None,
    *,
    player_text: str,
    gm_prose: str,
    scene_cast: dict[str, Any] | None,
    turn_resolution: TurnResolution,
) -> list[str]:
    """Set narrative quest flags and enrich social context before evaluate_quests."""
    if not db_path or not os.path.isfile(db_path):
        return []

    flagged: list[str] = []
    npc_name = _resolve_scene_npc(scene_cast, player_text, gm_prose)
    if npc_name:
        social = dict(turn_resolution.social or {})
        social.setdefault("npc_name", npc_name)
        turn_resolution.social = social

    conn = _connect(db_path)
    try:
        quests = conn.execute("SELECT id, code, title FROM quests WHERE status = 'active'").fetchall()
        for q in quests:
            objs = conn.execute(
                """
                SELECT * FROM quest_objectives
                WHERE quest_id = ? AND status = 'pending'
                  AND objective_type = 'custom' AND completion_mode = 'auto'
                ORDER BY sort_order
                """,
                (q["id"],),
            ).fetchall()
            for obj in objs:
                flag = objective_flag_key(int(q["id"]), int(obj["sort_order"]))
                existing = world_flags.get_flag_conn(conn, flag)
                if existing and existing not in ("0", "", None):
                    continue
                if _narrative_confirms_objective(obj, player_text=player_text, gm_prose=gm_prose):
                    world_flags.set_flag_conn(conn, flag, "1")
                    label = obj["description_text"] or obj["objective_type"]
                    flagged.append(f"{q['title']}: {label}")
        conn.commit()
    except sqlite3.Error as exc:
        LOG.warning("narrative quest adjudication failed: %s", exc)
    finally:
        conn.close()
    return flagged
