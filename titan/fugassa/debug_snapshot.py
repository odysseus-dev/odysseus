"""Pause Debug tab data — ADR §15: depth=2 subgraph from current location, read-only."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from titan.fugassa import campaign_chronicle, campaign_digest, npc_generator, quest_templates, world_state_snapshot
from titan.fugassa.property_repository import (
    list_fixtures_for_holding_conn,
    list_holdings_conn,
    list_rooms_for_holding_conn,
    list_staff_for_holding_conn,
)
from titan.fugassa.title_engine import list_titles_conn


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def build_debug_snapshot(db_path: str, save_id: str, *, include_secrets: bool = False) -> dict[str, Any]:
    if not db_path or not os.path.isfile(db_path):
        return {"error": "no_db"}

    conn = _connect(db_path)
    try:
        pc = conn.execute("SELECT * FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
        loc_id = int(pc["current_location_id"]) if pc and pc["current_location_id"] else None
        location: dict[str, Any] | None = None
        neighbor_locations: list[dict[str, Any]] = []
        npcs: list[dict[str, Any]] = []

        if loc_id:
            loc_row = conn.execute("SELECT * FROM locations WHERE id = ?", (loc_id,)).fetchone()
            if loc_row:
                location = dict(loc_row)
            npc_rows = conn.execute(
                "SELECT id FROM npcs WHERE current_location_id = ? AND status = 'alive'", (loc_id,)
            ).fetchall()
            for r in npc_rows:
                detail = npc_generator.get_npc_detail(db_path, r["id"], include_secrets=include_secrets)
                if detail:
                    npcs.append(detail)
            neighbor_rows = conn.execute(
                "SELECT id, name, is_discovered FROM locations WHERE parent_location_id = ? OR id = (SELECT parent_location_id FROM locations WHERE id = ?)",
                (loc_id, loc_id),
            ).fetchall()
            neighbor_locations = [dict(r) for r in neighbor_rows]

        active_quests = []
        quest_cols = {r[1] for r in conn.execute("PRAGMA table_info(quests)").fetchall()}
        select_cols = ["id", "title", "status", "description", "rewards_json"]
        for optional in ("quest_scale", "chain_code", "chain_position", "rewards_deferred"):
            if optional in quest_cols:
                select_cols.append(optional)
        quest_sql = f"SELECT {', '.join(select_cols)} FROM quests WHERE status = 'active'"
        for q in conn.execute(quest_sql).fetchall():
            rewards: dict[str, Any] = {}
            try:
                rewards = json.loads(q["rewards_json"]) if q["rewards_json"] else {}
            except (TypeError, ValueError):
                rewards = {}
            deferred = bool(q["rewards_deferred"]) if "rewards_deferred" in q.keys() else False
            active_quests.append(
                {
                    "title": q["title"],
                    "status": q["status"],
                    "description": q["description"],
                    "scale": (q["quest_scale"] if "quest_scale" in q.keys() else None) or "standard",
                    "chain_code": q["chain_code"] if "chain_code" in q.keys() else None,
                    "chain_position": q["chain_position"] if "chain_position" in q.keys() else None,
                    "rewards_deferred": deferred,
                    "rewards": rewards,
                    "rewards_preview": quest_templates.rewards_preview(rewards, deferred=deferred),
                    "objectives": [
                        dict(o)
                        for o in conn.execute(
                            "SELECT objective_type, description_text, status, optional, completion_mode FROM quest_objectives WHERE quest_id = ? ORDER BY sort_order",
                            (q["id"],),
                        ).fetchall()
                    ],
                }
            )

        property_holdings: list[dict[str, Any]] = []
        for h in list_holdings_conn(conn):
            row = conn.execute("SELECT * FROM property_holdings WHERE code = ?", (h["code"],)).fetchone()
            rooms = list_rooms_for_holding_conn(conn, int(h["root_location_id"]))
            fixtures: list[dict[str, Any]] = []
            staff: list[dict[str, Any]] = []
            if row:
                fixtures = list_fixtures_for_holding_conn(conn, int(row["id"]))
                staff = list_staff_for_holding_conn(conn, int(row["id"]))
            property_holdings.append(
                {
                    **h,
                    "sql_row": dict(row) if row else None,
                    "rooms": [{"id": r["id"], "name": r["name"]} for r in rooms],
                    "fixtures": fixtures,
                    "staff": staff,
                }
            )

        try:
            player_titles = list_titles_conn(conn)
        except sqlite3.OperationalError:
            player_titles = []
        active_title_code = ""
        if pc:
            try:
                active_title_code = str(pc["active_title_code"] or "").strip()
            except (KeyError, IndexError, TypeError):
                active_title_code = ""

        world_flags = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM world_flags").fetchall()}

        last_turn = conn.execute(
            "SELECT turn_number, player_text, ai_text, resolution_json, ingame_time FROM turn_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_turn_resolution = None
        if last_turn and last_turn["resolution_json"]:
            try:
                last_turn_resolution = json.loads(last_turn["resolution_json"])
            except (TypeError, ValueError):
                last_turn_resolution = None

        campaign = conn.execute("SELECT * FROM campaign_settings WHERE id = 1").fetchone()

        chronicle_last_10 = campaign_chronicle.query_recent(db_path, limit=10)
        embedding = campaign_chronicle.build_embedding_debug(db_path)
        semantic_recall_last = campaign_chronicle.load_semantic_recall_last(db_path)
        pipeline_turn = campaign_chronicle.load_pipeline_turn(db_path)
        digest_row = conn.execute(
            "SELECT COUNT(*) AS c FROM turn_history WHERE is_active = 1"
        ).fetchone()
        digest_meta = campaign_digest.get_digest(db_path)
        import json as _json

        condensed_eras = 0
        try:
            anchors = _json.loads(str(digest_meta.get("mega_anchors_json") or "[]"))
            condensed_eras = len(anchors) if isinstance(anchors, list) else 0
        except (TypeError, ValueError):
            condensed_eras = 0
        digest_stats = {
            "active_turns": int(digest_row["c"] or 0) if digest_row else 0,
            "active_turn_rows": int(digest_row["c"] or 0) if digest_row else 0,
            "condensed_eras": condensed_eras,
            "last_condense_turn": int(digest_meta.get("last_condensed_turn") or 0),
        }

        from titan.fugassa.db.state_repository import enrich_state_from_sql
        from titan.fugassa.game_bootstrap import read_game_json
        from titan.fugassa.save_store import save_dir as fugassa_save_dir

        state = read_game_json(fugassa_save_dir(save_id)) if save_id else {}
        if not isinstance(state, dict):
            state = {}
        state["save_id"] = save_id
        state = enrich_state_from_sql(db_path, state)
        snapshot_text = world_state_snapshot.build_snapshot_text(db_path, state, truncate=2400)
        campaign_state = world_state_snapshot.build_snapshot_dict(db_path, state)
        chronicle_recent = world_state_snapshot.format_chronicle_for_api(chronicle_last_10)

        return {
            "save_id": save_id,
            "campaign": dict(campaign) if campaign else None,
            "player_character": dict(pc) if pc else None,
            "location": location,
            "neighbor_locations": neighbor_locations,
            "npcs": npcs,
            "active_quests": active_quests,
            "property_holdings": property_holdings,
            "player_titles": {
                "titles": player_titles,
                "active_code": active_title_code or (player_titles[-1]["code"] if player_titles else None),
            },
            "world_flags": world_flags,
            "last_turn": {
                "turn_number": last_turn["turn_number"],
                "player_text": last_turn["player_text"],
                "ingame_time": last_turn["ingame_time"],
                "resolution": last_turn_resolution,
            }
            if last_turn
            else None,
            "chronicle_last_10": chronicle_last_10,
            "chronicle_recent": chronicle_recent,
            "campaign_state": campaign_state,
            "embedding": embedding,
            "semantic_recall_last": semantic_recall_last,
            "pipeline_turn": pipeline_turn,
            "snapshot_text": snapshot_text,
            "digest_stats": digest_stats,
        }
    finally:
        conn.close()
