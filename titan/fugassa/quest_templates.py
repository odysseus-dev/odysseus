"""Quest scale templates — archivist → playable engine quests with rewards."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from titan.fugassa import quest_engine

VALID_SCALES = frozenset({"minor", "standard", "major", "arc"})

_SCALE_DEFAULTS: dict[str, dict[str, Any]] = {
    "minor": {"gold": 15, "xp": 25},
    "standard": {"gold": 40, "xp": 75},
    "major": {"gold": 0, "xp": 150},
    "arc": {"gold": 0, "xp": 0},
}


def _slug(title: str, prefix: str = "quest") -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", str(title or "").strip().lower()).strip("_")
    return (base[:48] or prefix)


def normalize_scale(raw: Any) -> str:
    scale = str(raw or "standard").strip().lower()
    return scale if scale in VALID_SCALES else "standard"


def rewards_preview(rewards: dict[str, Any] | None, *, deferred: bool = False) -> str:
    if deferred:
        return "Reward to be determined upon completion"
    if not rewards:
        return "No fixed reward"
    parts: list[str] = []
    gold = int(rewards.get("gold") or 0)
    xp = int(rewards.get("xp") or 0)
    if gold:
        parts.append(f"{gold} gold")
    if xp:
        parts.append(f"{xp} XP")
    items = rewards.get("items") or []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            qty = max(1, int(item.get("qty", 1)))
            parts.append(f"{item['name']} x{qty}")
    renown = rewards.get("renown")
    if isinstance(renown, dict) and renown.get("title_display"):
        parts.append(f"title: {renown['title_display']}")
    return ", ".join(parts) if parts else "No fixed reward"


def build_rewards_for_op(op: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    scale = normalize_scale(op.get("scale") or op.get("quest_scale"))
    deferred = bool(op.get("rewards_deferred"))
    chain_code = str(op.get("chain_code") or "").strip() or None
    raw_rewards = op.get("rewards") if isinstance(op.get("rewards"), dict) else None

    if scale in ("major", "arc"):
        deferred = deferred or not raw_rewards
    if scale == "minor" and not raw_rewards and not deferred:
        raw_rewards = dict(_SCALE_DEFAULTS["minor"])
    if scale == "standard" and not raw_rewards and not deferred:
        raw_rewards = dict(_SCALE_DEFAULTS["standard"])

    if deferred or (scale in ("major", "arc") and not raw_rewards):
        return None, True
    if raw_rewards:
        return raw_rewards, False
    return dict(_SCALE_DEFAULTS.get(scale, _SCALE_DEFAULTS["standard"])), False


def objectives_from_op(
    op: dict[str, Any],
    *,
    db_path: str,
    loc_id: int | None,
) -> list[dict[str, Any]]:
    raw = op.get("objectives")
    if isinstance(raw, list) and raw:
        out: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("description_text") or "").strip()
            if not text:
                continue
            obj_type = str(item.get("type") or item.get("objective_type") or "custom").strip()
            entry: dict[str, Any] = {
                "objective_type": obj_type,
                "description_text": text,
            }
            if item.get("target_code"):
                entry["target_code"] = str(item["target_code"])
            if item.get("npc_name"):
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute(
                        "SELECT code FROM npcs WHERE name = ? LIMIT 1",
                        (str(item["npc_name"]),),
                    ).fetchone()
                    if row:
                        entry["target_code"] = row["code"]
                        entry["objective_type"] = "talk_npc"
                finally:
                    conn.close()
            out.append(entry)
        if out:
            return out

    desc = str(op.get("description") or "").strip()
    objectives: list[dict[str, Any]] = [
        {"objective_type": "custom", "description_text": desc or "Complete the task"},
    ]
    if loc_id:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT code FROM locations WHERE id = ?", (loc_id,)).fetchone()
            if row and row["code"]:
                objectives.insert(
                    0,
                    {
                        "objective_type": "visit_location",
                        "target_code": row["code"],
                        "description_text": "Reach the quest location",
                    },
                )
        finally:
            conn.close()
    return objectives


def validate_archivist_quest_op(op: dict[str, Any]) -> str | None:
    scale = normalize_scale(op.get("scale") or op.get("quest_scale"))
    rewards, deferred = build_rewards_for_op(op, {})
    chain_code = str(op.get("chain_code") or "").strip()
    if scale == "minor":
        if deferred:
            return "minor quests must define explicit rewards"
        if not rewards or (not rewards.get("gold") and not rewards.get("items") and not rewards.get("xp")):
            return "minor quests need gold, items, or xp in rewards"
    if scale in ("major", "arc"):
        if not deferred and not chain_code and not rewards:
            return "major/arc quests need rewards_deferred, chain_code, or explicit rewards"
    return None


def create_quest_from_archivist_op(
    db_path: str,
    op: dict[str, Any],
    *,
    state: dict[str, Any],
    loc_id: int | None,
) -> int | None:
    err = validate_archivist_quest_op(op)
    if err:
        return None
    title = str(op.get("title") or "").strip()
    if not title:
        return None
    scale = normalize_scale(op.get("scale") or op.get("quest_scale"))
    rewards, deferred = build_rewards_for_op(op, state)
    chain_code = str(op.get("chain_code") or "").strip() or None
    chain_position = op.get("chain_position")
    giver_name = str(op.get("giver_npc_name") or op.get("giver") or "").strip()
    giver_code = None
    if giver_name:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT code FROM npcs WHERE name = ? LIMIT 1", (giver_name,)).fetchone()
            giver_code = row["code"] if row else None
        finally:
            conn.close()

    loc_code = None
    if loc_id:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT code FROM locations WHERE id = ?", (loc_id,)).fetchone()
            loc_code = row["code"] if row else None
        finally:
            conn.close()

    quest_id = quest_engine.create_quest(
        db_path,
        code=_slug(title),
        title=title,
        description=str(op.get("description") or ""),
        giver_npc_code=giver_code,
        location_code=loc_code,
        objectives=objectives_from_op(op, db_path=db_path, loc_id=loc_id),
        rewards=rewards,
        activated_at_turn=int(state.get("turn") or 0),
        quest_scale=scale,
        chain_code=chain_code,
        chain_position=int(chain_position) if chain_position is not None else None,
        rewards_deferred=deferred,
    )
    return quest_id
