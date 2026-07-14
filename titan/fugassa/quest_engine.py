"""Quest objective engine — ADR §H8: engine-only completion.

Objectives are checked against DB facts every turn; the player can never
complete a quest by declaring it in chat, and the GM/archivist can never
mark `complete` directly — only this engine writes objective/quest status.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import campaign_chronicle, currency_engine, npc_knowledge, quest_narrative, renown_engine, world_flags
from titan.fugassa.turn_resolution import TurnResolution

LOG = logging.getLogger("titan.fugassa.quest_engine")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _condition(obj: sqlite3.Row) -> dict[str, Any]:
    try:
        return json.loads(obj["condition_json"]) if obj["condition_json"] else {}
    except (TypeError, ValueError):
        return {}


def _hero_location_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
    return int(row["current_location_id"]) if row and row["current_location_id"] else None


def _game_time_minutes(state: dict[str, Any]) -> int:
    """Absolute in-game clock, in minutes — ADR §H8.3 canonical time for deadlines."""
    wt = state.get("world_time") or {}
    day = int(wt.get("day", 1) or 1)
    hour = int(wt.get("hour", 8) or 8)
    return day * 1440 + hour * 60


def deadline_from_now(state: dict[str, Any], *, duration_hours: int | None = None, day: int | None = None, hour: int | None = None) -> str:
    """Compute an absolute `quests.deadline_ingame_at` value (minutes-since-epoch, as text).

    Either pass `duration_hours` (relative to the current in-game clock) or an
    absolute `day`/`hour` pair.
    """
    if day is not None:
        return str(int(day) * 1440 + int(hour or 0) * 60)
    return str(_game_time_minutes(state) + int(duration_hours or 0) * 60)


def _rewards_json(row: sqlite3.Row, key: str) -> dict[str, Any]:
    try:
        raw = row[key]
    except (IndexError, KeyError):
        return {}
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def _grant_rewards(
    conn: sqlite3.Connection,
    state: dict[str, Any],
    rewards: dict[str, Any],
    *,
    chronicle_events: list[campaign_chronicle.ChronicleEvent] | None = None,
    companions_joined: list[str] | None = None,
    turn_id: int = 0,
    location_id: int | None = None,
    hero_name: str = "Hero",
) -> list[str]:
    """Apply a rewards_json payload — ADR §H8.6: engine-only grant, never GM prose.

    Items/gold are appended to `state["inventory"]["shared"]` (JSON stays
    authoritative for inventory contents; the next `sync_from_state` call
    mirrors it into SQL — writing the SQL `items` table directly here would
    just be overwritten by that same JSON-authoritative pass). XP is safe to
    write straight to SQL since `sync_from_state` never touches that column.
    """
    granted: list[str] = []
    if not rewards:
        return granted

    items = rewards.get("items") or []
    gold = int(rewards.get("gold") or 0)
    if items:
        inv = dict(state.get("inventory") or {})
        shared = list(inv.get("shared") or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            qty = max(1, int(item.get("qty", 1)))
            shared.append({"name": name, "qty": qty})
            granted.append(f"{name} x{qty}")
        inv["shared"] = shared
        state["inventory"] = inv
    if gold:
        currency_name = currency_engine.resolve_tier_name(
            currency_engine.ensure_currency_profile(state),
            None,
            prefer_high=True,
        )
        record = currency_engine.adjust_currency(
            state,
            gold,
            tier_name=currency_name,
            prefer_high_tier=True,
            reason="quest_reward",
        )
        applied = int(record.get("applied") or 0)
        if applied:
            granted.append(f"{applied} {record.get('tier', currency_name)}")

    xp = int(rewards.get("xp") or 0)
    if xp:
        conn.execute(
            "UPDATE player_characters SET experience_points = experience_points + ? WHERE code = 'pc_hero'",
            (xp,),
        )
        granted.append(f"{xp} XP")

    renown = rewards.get("renown")
    if isinstance(renown, dict) and renown.get("renown_code"):
        from titan.fugassa.title_engine import default_bonuses_for_tier

        tier = int(renown.get("impact_tier", 2))
        bonuses = renown.get("bonuses") if isinstance(renown.get("bonuses"), dict) else default_bonuses_for_tier(tier)
        result = renown_engine.grant_renown_conn(
            conn,
            renown_code=str(renown["renown_code"]),
            scope_type=str(renown.get("scope_type", "region")),
            valence=str(renown.get("valence", "positive")),
            impact_tier=tier,
            title_display=renown.get("title_display"),
            scope_id=renown.get("scope_id"),
            granted_at_turn=int(state.get("turn") or 0),
            bonuses_json=bonuses,
        )
        if result:
            granted.append(f"renown: {result['renown_code']}")
            tier = int(result.get("impact_tier") or 0)
            if chronicle_events is not None and tier >= 2:
                chronicle_events.append(
                    campaign_chronicle.make_title_granted_event(
                        renown_code=str(result["renown_code"]),
                        title_display=str(renown.get("title_display") or result["renown_code"]),
                        impact_tier=tier,
                        hero_name=hero_name,
                        turn_id=turn_id,
                        location_id=location_id,
                    )
                )
            radius = renown.get("propagate_radius")
            origin_code = renown.get("propagate_from_location_code")
            if radius and origin_code:
                updated = npc_knowledge.propagate_rumor_conn(
                    conn, origin_location_code=str(origin_code), radius_cells=int(radius)
                )
                if updated:
                    granted.append(f"rumor spread to {len(updated)} NPC(s)")

    companion = rewards.get("companion")
    if isinstance(companion, dict):
        npc_code = str(companion.get("npc_code") or companion.get("code") or "").strip()
        if npc_code:
            row = conn.execute(
                "SELECT id, code, name, portrait_path FROM npcs WHERE code = ? AND status = 'alive' LIMIT 1",
                (npc_code,),
            ).fetchone()
            if row:
                party = list(state.get("party") or [])
                already = any(
                    isinstance(m, dict) and str(m.get("npc_code") or m.get("code") or "") == npc_code for m in party
                )
                if not already:
                    member: dict[str, Any] = {
                        "name": row["name"],
                        "role": str(companion.get("role") or "companion"),
                        "npc_code": row["code"],
                        "npc_id": int(row["id"]),
                        "hp": int(companion.get("hp") or 10),
                        "max_hp": int(companion.get("max_hp") or companion.get("hp") or 10),
                        "ac": int(companion.get("ac") or 12),
                    }
                    if row["portrait_path"]:
                        member["portrait_file"] = row["portrait_path"]
                    party.append(member)
                    state["party"] = party
                    granted.append(f"companion: {row['name']}")
                    if companions_joined is not None:
                        companions_joined.append(str(row["name"]))
                    if chronicle_events is not None:
                        chronicle_events.append(
                            campaign_chronicle.make_companion_join_event(
                                npc_code=row["code"],
                                npc_name=row["name"],
                                hero_name=hero_name,
                                turn_id=turn_id,
                                location_id=location_id,
                            )
                        )
    return granted


def _check_fail_conditions(conn: sqlite3.Connection, q: sqlite3.Row, state: dict[str, Any]) -> str | None:
    """ADR §H8.3 — giver_dead / event_flag / time_expired. `player_choice` is handled
    separately via `renounce_quest()` since it's driven by a dialog choice, not a
    per-turn DB scan."""
    if q["giver_npc_id"]:
        row = conn.execute("SELECT status FROM npcs WHERE id = ?", (q["giver_npc_id"],)).fetchone()
        if row and row["status"] == "dead":
            return "giver_dead"

    deadline = q["deadline_ingame_at"] if "deadline_ingame_at" in q.keys() else None
    if deadline:
        try:
            if _game_time_minutes(state) >= int(deadline):
                return "time_expired"
        except (TypeError, ValueError):
            pass

    fail_flag_objs = conn.execute(
        "SELECT * FROM quest_objectives WHERE quest_id = ? AND objective_type = 'fail_on_event_flag' AND status = 'pending'",
        (q["id"],),
    ).fetchall()
    for obj in fail_flag_objs:
        flag_key = obj["target_code"] or _condition(obj).get("flag")
        if not flag_key:
            continue
        value = world_flags.get_flag_conn(conn, flag_key)
        if value and value not in ("0", "", None):
            conn.execute("UPDATE quest_objectives SET status = 'complete' WHERE id = ?", (obj["id"],))
            return "event_flag"
    return None


def _has_item(conn: sqlite3.Connection, item_code: str | None, item_name: str | None) -> sqlite3.Row | None:
    if item_code:
        row = conn.execute(
            "SELECT * FROM items WHERE code = ? AND owner_type = 'player_character' AND quantity > 0",
            (item_code,),
        ).fetchone()
        if row:
            return row
    if item_name:
        return conn.execute(
            "SELECT * FROM items WHERE name = ? AND owner_type = 'player_character' AND quantity > 0",
            (item_name,),
        ).fetchone()
    return None


def _npc_is_dead(conn: sqlite3.Connection, target_id: int | None, target_code: str | None) -> bool:
    if target_id:
        row = conn.execute("SELECT status FROM npcs WHERE id = ?", (target_id,)).fetchone()
    elif target_code:
        row = conn.execute("SELECT status FROM npcs WHERE code = ?", (target_code,)).fetchone()
    else:
        return False
    return bool(row and row["status"] == "dead")


def _evaluate_objective(
    conn: sqlite3.Connection,
    obj: sqlite3.Row,
    state: dict[str, Any],
    turn_resolution: TurnResolution,
) -> bool:
    otype = obj["objective_type"]
    cond = _condition(obj)
    player = state.get("player") or {}

    if otype == "visit_location":
        loc_id = _hero_location_id(conn)
        if obj["target_entity_id"]:
            return loc_id == obj["target_entity_id"]
        if obj["target_code"]:
            row = conn.execute("SELECT id FROM locations WHERE code = ?", (obj["target_code"],)).fetchone()
            return bool(row and loc_id == row["id"])
        return False

    if otype == "visit_grid_cell":
        tx, ty, tz = cond.get("x"), cond.get("y"), cond.get("z", 0)
        if tx is None or ty is None:
            return False
        return int(player.get("x", 0)) == int(tx) and int(player.get("y", 0)) == int(ty) and int(player.get("z", 0)) == int(tz)

    if otype == "talk_npc":
        npc_name = (turn_resolution.social or {}).get("npc_name")
        if not npc_name:
            return False
        if obj["target_code"]:
            row = conn.execute("SELECT name FROM npcs WHERE code = ?", (obj["target_code"],)).fetchone()
            if row and row["name"] == npc_name:
                return True
            # Alias match — e.g. "Elara" when canonical name is "Elara Voss".
            canonical = (row["name"] or "").lower() if row else ""
            spoken = str(npc_name).lower()
            if canonical and (spoken in canonical or canonical.split()[0] == spoken):
                return True
            return False
        return True

    if otype == "custom":
        flag_key = cond.get("flag") or quest_narrative.objective_flag_key(int(obj["quest_id"]), int(obj["sort_order"]))
        value = world_flags.get_flag_conn(conn, flag_key)
        return bool(value and value not in ("0", "", None))

    if otype == "obtain_item":
        return _has_item(conn, cond.get("item_code") or obj["target_code"], cond.get("item_name")) is not None

    if otype == "deliver_item":
        npc_name = (turn_resolution.social or {}).get("npc_name")
        if not npc_name:
            return False
        if obj["target_code"]:
            row = conn.execute("SELECT name FROM npcs WHERE code = ?", (obj["target_code"],)).fetchone()
            if not row or row["name"] != npc_name:
                return False
        item_row = _has_item(conn, cond.get("item_code"), cond.get("item_name"))
        if not item_row:
            return False
        new_qty = int(item_row["quantity"]) - 1
        conn.execute("UPDATE items SET quantity = ?, updated_at = ? WHERE id = ?", (new_qty, _utc_now(), item_row["id"]))
        return True

    if otype == "kill":
        return _npc_is_dead(conn, obj["target_entity_id"], obj["target_code"])

    if otype == "explore":
        if obj["target_entity_id"]:
            row = conn.execute("SELECT is_discovered FROM locations WHERE id = ?", (obj["target_entity_id"],)).fetchone()
        elif obj["target_code"]:
            row = conn.execute("SELECT is_discovered FROM locations WHERE code = ?", (obj["target_code"],)).fetchone()
        else:
            return False
        return bool(row and row["is_discovered"])

    if otype == "reach_trust":
        trust_min = int(cond.get("trust_min", 3))
        if obj["target_entity_id"]:
            row = conn.execute(
                "SELECT trust FROM npc_relationships WHERE source_npc_id = ? AND target_type = 'player'",
                (obj["target_entity_id"],),
            ).fetchone()
        elif obj["target_code"]:
            row = conn.execute(
                """
                SELECT r.trust FROM npc_relationships r JOIN npcs n ON n.id = r.source_npc_id
                WHERE n.code = ? AND r.target_type = 'player'
                """,
                (obj["target_code"],),
            ).fetchone()
        else:
            return False
        return bool(row and int(row["trust"]) >= trust_min)

    if otype == "wait_event":
        flag_key = obj["target_code"] or cond.get("flag")
        if not flag_key:
            return False
        value = world_flags.get_flag_conn(conn, flag_key)
        return bool(value and value not in ("0", "", None))

    if otype == "use_item_at":
        inv = turn_resolution.inventory or {}
        if not inv.get("used"):
            return False
        want_item_code = cond.get("item_code") or obj["target_code"]
        want_item_name = cond.get("item_name")
        if want_item_code and inv.get("item_code") != want_item_code:
            return False
        if want_item_name and inv.get("item_name") != want_item_name:
            return False
        want_loc_id = cond.get("location_id") or obj["target_entity_id"]
        want_loc_code = cond.get("location_code")
        if want_loc_id:
            return int(inv.get("location_id") or 0) == int(want_loc_id)
        if want_loc_code:
            row = conn.execute("SELECT id FROM locations WHERE code = ?", (want_loc_code,)).fetchone()
            return bool(row and inv.get("location_id") == row["id"])
        return True  # no location constraint — any use of the item counts

    # unknown objective types: no freeform inference
    return False


def evaluate_quests_after_gm(
    db_path: str | None,
    state: dict[str, Any],
    turn_resolution: TurnResolution,
    *,
    player_text: str = "",
    gm_prose: str = "",
    scene_cast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Post-GM quest pass — adjudicate narrative objectives, then evaluate."""
    if db_path and gm_prose.strip():
        quest_narrative.enrich_turn_resolution_for_quests(
            db_path,
            player_text=player_text,
            gm_prose=gm_prose,
            scene_cast=scene_cast,
            turn_resolution=turn_resolution,
        )
    result = evaluate_quests(db_path or "", state, turn_resolution)
    record_quest_chronicle(db_path, result, turn_resolution=turn_resolution)
    return {k: v for k, v in result.items() if k != "chronicle_events"}


def evaluate_quests(db_path: str, state: dict[str, Any], turn_resolution: TurnResolution) -> dict[str, Any]:
    if not db_path or not os.path.isfile(db_path):
        return {}
    conn = _connect(db_path)
    completed_objectives: list[dict[str, Any]] = []
    completed_quests: list[str] = []
    failed_quests: list[dict[str, Any]] = []
    rewards_granted: dict[str, list[str]] = {}
    chronicle_events: list[campaign_chronicle.ChronicleEvent] = []
    companions_joined: list[str] = []
    turn_id = int(state.get("turn") or 0)
    location_id = _hero_location_id(conn)
    hero_name = campaign_chronicle.hero_name_conn(conn)
    try:
        quests = conn.execute("SELECT * FROM quests WHERE status = 'active'").fetchall()
        for q in quests:
            fail_reason = _check_fail_conditions(conn, q, state)
            if fail_reason:
                conn.execute(
                    "UPDATE quests SET status = 'failed', fail_reason = ?, updated_at = ? WHERE id = ?",
                    (fail_reason, _utc_now(), q["id"]),
                )
                world_flags.set_flag_conn(conn, f"quest_failed:{q['code']}")
                failed_quests.append({"quest": q["title"], "reason": fail_reason, "code": q["code"]})
                chronicle_events.append(
                    campaign_chronicle.make_quest_failed_event(
                        quest_code=str(q["code"]),
                        quest_title=str(q["title"]),
                        hero_name=hero_name,
                        reason=fail_reason,
                        turn_id=turn_id,
                        location_id=location_id,
                    )
                )
                continue  # a failed quest's objectives no longer matter this turn

            objs = conn.execute(
                "SELECT * FROM quest_objectives WHERE quest_id = ? AND status = 'pending' AND completion_mode = 'auto'",
                (q["id"],),
            ).fetchall()
            for obj in objs:
                try:
                    if _evaluate_objective(conn, obj, state, turn_resolution):
                        conn.execute("UPDATE quest_objectives SET status = 'complete' WHERE id = ?", (obj["id"],))
                        obj_label = obj["description_text"] or obj["objective_type"]
                        completed_objectives.append({"quest": q["title"], "objective": obj_label, "code": q["code"]})
                        chronicle_events.append(
                            campaign_chronicle.make_quest_progress_event(
                                quest_code=str(q["code"]),
                                quest_title=str(q["title"]),
                                objective=str(obj_label),
                                turn_id=turn_id,
                                location_id=location_id,
                            )
                        )
                except sqlite3.Error as exc:
                    LOG.warning("objective eval failed (quest=%s obj=%s): %s", q["id"], obj["id"], exc)

            total = conn.execute(
                "SELECT COUNT(*) AS c FROM quest_objectives WHERE quest_id = ? AND objective_type != 'fail_on_event_flag'",
                (q["id"],),
            ).fetchone()["c"]
            remaining = conn.execute(
                """
                SELECT COUNT(*) AS c FROM quest_objectives
                WHERE quest_id = ? AND status = 'pending' AND optional = 0 AND objective_type != 'fail_on_event_flag'
                """,
                (q["id"],),
            ).fetchone()["c"]
            if total > 0 and remaining == 0:
                conn.execute("UPDATE quests SET status = 'completed', updated_at = ? WHERE id = ?", (_utc_now(), q["id"]))
                completed_quests.append(q["title"])
                # ADR §H8.1 wait_event hook: quest completion is a first-class engine
                # event other quests can chain off, with no GM/archivist involvement.
                world_flags.set_flag_conn(conn, f"quest_complete:{q['code']}")

                granted = _grant_rewards(
                    conn,
                    state,
                    _rewards_json(q, "rewards_json"),
                    chronicle_events=chronicle_events,
                    companions_joined=companions_joined,
                    turn_id=turn_id,
                    location_id=location_id,
                    hero_name=hero_name,
                )
                merit_done = conn.execute(
                    "SELECT COUNT(*) AS c FROM quest_objectives WHERE quest_id = ? AND optional = 1 AND status = 'complete'",
                    (q["id"],),
                ).fetchone()["c"]
                if merit_done and q["bonus_rewards_json"]:
                    granted += _grant_rewards(
                        conn,
                        state,
                        _rewards_json(q, "bonus_rewards_json"),
                        chronicle_events=chronicle_events,
                        companions_joined=companions_joined,
                        turn_id=turn_id,
                        location_id=location_id,
                        hero_name=hero_name,
                    )
                if granted:
                    rewards_granted[q["code"]] = granted
                scale = q["quest_scale"] if "quest_scale" in q.keys() else None
                chain_code = q["chain_code"] if "chain_code" in q.keys() else None
                chain_pos = q["chain_position"] if "chain_position" in q.keys() else None
                chronicle_events.append(
                    campaign_chronicle.make_quest_complete_event(
                        quest_code=str(q["code"]),
                        quest_title=str(q["title"]),
                        hero_name=hero_name,
                        turn_id=turn_id,
                        location_id=location_id,
                        scale=str(scale) if scale else None,
                        chain_code=str(chain_code) if chain_code else None,
                        chain_position=int(chain_pos) if chain_pos is not None else None,
                        rewards_granted=granted,
                    )
                )
        conn.commit()
    finally:
        conn.close()

    if completed_objectives or completed_quests or failed_quests or companions_joined:
        parts = [f"Quest completed: {t}" for t in completed_quests]
        parts += [f"Quest failed ({f['reason']}): {f['quest']}" for f in failed_quests]
        parts += [f"Objective done ({o['quest']}): {o['objective']}" for o in completed_objectives]
        turn_resolution.quest = {
            "objectives_completed": completed_objectives,
            "quests_completed": completed_quests,
            "quests_failed": failed_quests,
            "rewards_granted": rewards_granted,
            "companions_joined": companions_joined,
            "summary": "; ".join(parts),
        }
    return {
        "objectives_completed": completed_objectives,
        "quests_completed": completed_quests,
        "quests_failed": failed_quests,
        "rewards_granted": rewards_granted,
        "companions_joined": companions_joined,
        "chronicle_events": chronicle_events,
    }


def record_quest_chronicle(
    db_path: str | None,
    result: dict[str, Any],
    *,
    turn_resolution: TurnResolution | None = None,
) -> list[int]:
    """ADR §5.5 — persist engine chronicle events collected during evaluate_quests."""
    events = result.get("chronicle_events") or []
    if not db_path or not events:
        return []
    return campaign_chronicle.record_from_resolution(
        db_path,
        events,
        turn_resolution=turn_resolution,
    )


_AMOUNT_RE = re.compile(r"\b(\d+)\s*(?:gp|gold|coins?)?\b")


def renounce_quest(db_path: str, state: dict[str, Any], giver_npc_id: int) -> dict[str, Any] | None:
    """ADR §H8.3 `player_choice`: renouncing a quest happens through NPC dialog,
    never a UI abandon button. Call this from the social resolver once it has
    already matched the NPC the player is talking to."""
    if not db_path or not os.path.isfile(db_path):
        return None
    conn = _connect(db_path)
    try:
        q = conn.execute(
            "SELECT id, code, title FROM quests WHERE giver_npc_id = ? AND status = 'active' LIMIT 1",
            (giver_npc_id,),
        ).fetchone()
        if not q:
            return None
        conn.execute(
            "UPDATE quests SET status = 'failed', fail_reason = 'player_choice', updated_at = ? WHERE id = ?",
            (_utc_now(), q["id"]),
        )
        world_flags.set_flag_conn(conn, f"quest_failed:{q['code']}")
        conn.commit()
        return {"quest": q["title"], "code": q["code"], "reason": "player_choice"}
    finally:
        conn.close()


def negotiate_reward(db_path: str, state: dict[str, Any], giver_npc_id: int, player_text: str, roll: int) -> dict[str, Any] | None:
    """ADR §H8.6 negotiation table — deterministic DC by relationship + whether the
    ask is within the `bonus_rewards_json` band or greedy beyond it. Only fires
    once per quest (flagged via world_flags so the player can't re-roll)."""
    if not db_path or not os.path.isfile(db_path):
        return None
    conn = _connect(db_path)
    try:
        q = conn.execute(
            """
            SELECT * FROM quests WHERE giver_npc_id = ? AND status = 'completed'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (giver_npc_id,),
        ).fetchone()
        if not q:
            return None
        if world_flags.get_flag_conn(conn, f"quest_reward_negotiated:{q['code']}"):
            return None

        bonus = _rewards_json(q, "bonus_rewards_json")
        rules = _rewards_json(q, "negotiation_rules_json")
        base_gold = int(_rewards_json(q, "rewards_json").get("gold") or 0)
        bonus_pct_max = float(rules.get("bonus_pct_max", 15))
        cap_gold = int(bonus.get("gold") or round(base_gold * bonus_pct_max / 100))

        m = _AMOUNT_RE.search(player_text or "")
        requested = int(m.group(1)) if m else max(1, cap_gold)
        greedy = requested > cap_gold * 1.5 if cap_gold else requested > 0

        rel = conn.execute(
            "SELECT trust FROM npc_relationships WHERE source_npc_id = ? AND target_type = 'player'",
            (giver_npc_id,),
        ).fetchone()
        trust = int(rel["trust"]) if rel else 0

        world_flags.set_flag_conn(conn, f"quest_reward_negotiated:{q['code']}")

        if greedy:
            conn.execute(
                "UPDATE npc_relationships SET trust = MAX(-10, trust - 1), updated_at = ? WHERE source_npc_id = ? AND target_type = 'player'",
                (_utc_now(), giver_npc_id),
            )
            conn.commit()
            return {
                "quest": q["title"],
                "outcome": "auto_fail_greedy",
                "requested": requested,
                "cap": cap_gold,
                "granted_gold": 0,
                "trust_delta": -1,
                "summary": f"Demanding {requested} gold is far beyond the {cap_gold} gold bonus band — the request is refused outright.",
            }

        dc = 13 if trust >= 3 else 16
        crit_success = roll == 20
        crit_fail = roll == 1
        success = crit_success or (not crit_fail and roll >= dc)

        if crit_fail:
            granted = 0
            trust_delta = -2
            conn.execute(
                "INSERT OR IGNORE INTO npc_tags (npc_id, tag, source, created_at) VALUES (?, 'quest_greedy', 'social', ?)",
                (giver_npc_id, _utc_now()),
            )
            outcome = "crit_fail"
        elif success:
            granted = min(requested, cap_gold)
            trust_delta = 0
            outcome = "success"
        else:
            granted = 0
            trust_delta = 0
            outcome = "fail"

        if trust_delta:
            conn.execute(
                "UPDATE npc_relationships SET trust = MAX(-10, MIN(10, trust + ?)), updated_at = ? WHERE source_npc_id = ? AND target_type = 'player'",
                (trust_delta, _utc_now(), giver_npc_id),
            )
        if granted:
            _grant_rewards(conn, state, {"gold": granted})
        conn.commit()
        return {
            "quest": q["title"],
            "outcome": outcome,
            "roll": roll,
            "dc": dc,
            "requested": requested,
            "cap": cap_gold,
            "granted_gold": granted,
            "trust_delta": trust_delta,
            "summary": (
                f"Negotiation d20={roll} vs DC {dc}: {outcome} — "
                f"{'granted ' + str(granted) + ' bonus gold' if granted else 'base reward only'}"
            ),
        }
    finally:
        conn.close()


def set_world_flag(db_path: str, key: str, value: str = "1") -> None:
    """External engine-only hook (wizard, scripted world event) — opens its own connection."""
    world_flags.set_flag(db_path, key, value)


def create_quest(
    db_path: str,
    *,
    code: str,
    title: str,
    description: str = "",
    giver_npc_code: str | None = None,
    location_code: str | None = None,
    objectives: list[dict[str, Any]] | None = None,
    rewards: dict[str, Any] | None = None,
    bonus_rewards: dict[str, Any] | None = None,
    negotiation_rules: dict[str, Any] | None = None,
    deadline_ingame_at: str | None = None,
    duration_hours: int | None = None,
    activated_at_turn: int | None = None,
    quest_scale: str = "standard",
    chain_code: str | None = None,
    chain_position: int | None = None,
    rewards_deferred: bool = False,
) -> int | None:
    """Engine-owned quest creation hook (wizard, world event, or validated archivist create).

    `rewards`/`bonus_rewards`/`negotiation_rules` map to ADR §H8.6's
    `rewards_json`/`bonus_rewards_json`/`negotiation_rules_json`. `deadline_ingame_at`
    is an absolute in-game-minutes string (see `deadline_from_now()`); `duration_hours`
    is a convenience alternative resolved relative to the *current* game clock at
    creation time if the caller doesn't already have one computed.
    """
    if not db_path or not os.path.isfile(db_path):
        return None
    conn = _connect(db_path)
    try:
        existing = conn.execute("SELECT id FROM quests WHERE code = ?", (code,)).fetchone()
        if existing:
            return int(existing["id"])
        giver_id = None
        if giver_npc_code:
            row = conn.execute("SELECT id FROM npcs WHERE code = ?", (giver_npc_code,)).fetchone()
            giver_id = int(row["id"]) if row else None
        loc_id = None
        if location_code:
            row = conn.execute("SELECT id FROM locations WHERE code = ?", (location_code,)).fetchone()
            loc_id = int(row["id"]) if row else None
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO quests (
                code, title, description, status, giver_npc_id, related_location_id,
                rewards_json, bonus_rewards_json, negotiation_rules_json,
                deadline_ingame_at, duration_hours, activated_at_turn,
                quest_scale, chain_code, chain_position, rewards_deferred,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                title,
                description,
                giver_id,
                loc_id,
                json.dumps(rewards) if rewards else None,
                json.dumps(bonus_rewards) if bonus_rewards else None,
                json.dumps(negotiation_rules) if negotiation_rules else None,
                deadline_ingame_at,
                duration_hours,
                activated_at_turn,
                str(quest_scale or "standard"),
                chain_code,
                chain_position,
                1 if rewards_deferred else 0,
                now,
                now,
            ),
        )
        quest_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        for i, obj in enumerate(objectives or []):
            conn.execute(
                """
                INSERT INTO quest_objectives (
                    quest_id, sort_order, objective_type, target_entity_type, target_entity_id,
                    target_code, condition_json, description_text, status, optional, completion_mode, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    quest_id,
                    i,
                    obj.get("objective_type", "custom"),
                    obj.get("target_entity_type"),
                    obj.get("target_entity_id"),
                    obj.get("target_code"),
                    json.dumps(obj.get("condition")) if obj.get("condition") else None,
                    obj.get("description_text", ""),
                    1 if obj.get("optional") else 0,
                    obj.get("completion_mode", "auto"),
                    now,
                ),
            )
        conn.commit()
        return quest_id
    finally:
        conn.close()
