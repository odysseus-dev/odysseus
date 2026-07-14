"""One-time campaign repair for the Fugassa save — modest, lore-aligned."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import campaign_chronicle, campaign_facts
from titan.fugassa.game_bootstrap import read_game_json, write_game_json
from titan.fugassa.property_repository import (
    assign_staff_conn,
    create_fixture_conn,
    create_holding_conn,
    create_property_room_conn,
    dedupe_spurious_holdings_conn,
    sync_property_portfolio,
)
from titan.fugassa.save_store import game_db_path, save_dir


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _repair_driscoll_estate_details(
    conn: sqlite3.Connection,
    state: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    """Ensure Guest Chamber, Study fixture, and Elara staff assignment on existing saves."""
    prop = conn.execute(
        "SELECT id, root_location_id FROM property_holdings WHERE code = ?",
        ("house_driscoll_city",),
    ).fetchone()
    if not prop:
        return
    root_id = int(prop["root_location_id"])
    property_id = int(prop["id"])

    guest = conn.execute(
        "SELECT id FROM locations WHERE parent_location_id = ? AND name = ? COLLATE NOCASE",
        (root_id, "Guest Chamber"),
    ).fetchone()
    if not guest:
        create_property_room_conn(
            conn,
            property_code="house_driscoll_city",
            room_name="Guest Chamber",
            description="A modest second bedroom for retainers or honored guests.",
        )
        sync_property_portfolio(conn, state)
        summary["changes"].append("added Guest Chamber room")

    fixture = conn.execute(
        """
        SELECT id FROM property_fixtures
        WHERE property_id = ? AND name = ? COLLATE NOCASE
        LIMIT 1
        """,
        (property_id, "Family Ledgers Cabinet"),
    ).fetchone()
    if not fixture:
        created = create_fixture_conn(
            conn,
            property_code="house_driscoll_city",
            room_name="Study",
            name="Family Ledgers Cabinet",
            fixture_kind="storage",
            description="Locked oak cabinet holding House Driscoll ledgers.",
            specs={"material": "oak", "storage_slots": 12},
        )
        if created:
            summary["changes"].append("seeded Family Ledgers fixture in Study")

    elara = conn.execute(
        "SELECT id FROM npcs WHERE code = ? AND status = 'alive' LIMIT 1",
        ("elara_voss",),
    ).fetchone()
    if elara:
        assigned = conn.execute(
            "SELECT assigned_property_id FROM npcs WHERE id = ?",
            (int(elara["id"]),),
        ).fetchone()
        if not assigned or not assigned["assigned_property_id"]:
            if assign_staff_conn(
                conn,
                property_code="house_driscoll_city",
                npc_code="elara_voss",
                role="concubine",
            ):
                sync_property_portfolio(conn, state)
                summary["changes"].append("assigned Elara Voss to House Driscoll")


def seed_fugassa_campaign(save_id: str = "Fugassa", *, dry_run: bool = False) -> dict[str, Any]:
    """Repair Fugassa: Crownstone naming, property, quest metadata."""
    db_path = game_db_path(save_id)
    slot_dir = save_dir(save_id)
    if not os.path.isfile(db_path) or not os.path.isdir(slot_dir):
        return {"ok": False, "reason": "save_not_found"}

    summary: dict[str, Any] = {"save_id": save_id, "dry_run": dry_run, "changes": []}
    state = read_game_json(slot_dir) or {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        # --- Crownstone settlement naming ---
        grid = conn.execute(
            "SELECT id, name FROM locations WHERE parent_location_id IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        if grid:
            new_name = "Market District"
            if dry_run:
                summary["changes"].append(f"location {grid['id']} → name={new_name}, region=Crownstone")
            else:
                conn.execute(
                    "UPDATE locations SET name = ?, region_name = ?, updated_at = ? WHERE id = ?",
                    (new_name, "Crownstone", _utc_now(), int(grid["id"])),
                )
                from titan.fugassa.location_name_registry import REGISTRY_META_KEY, load_registry, reserve_name

                registry = load_registry(db_path)
                reserve_name(
                    registry,
                    name="Crownstone",
                    kind="city",
                    entity_type="location",
                    entity_id=int(grid["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO save_meta (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (REGISTRY_META_KEY, json.dumps({"entries": registry.entries}, ensure_ascii=False), _utc_now()),
                )
                summary["changes"].append("named settlement Crownstone")

        # --- House Driscoll property (modest) ---
        existing = conn.execute("SELECT id FROM property_holdings WHERE code = ?", ("house_driscoll_city",)).fetchone()
        pc = conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
        if pc and not existing:
            proposal = {
                "granted": True,
                "code": "house_driscoll_city",
                "name": "House Driscoll — City Residence",
                "root_location_name": "Driscoll Townhouse",
                "property_kind": "townhouse",
                "title_status": "owned",
                "acquired_via": "inheritance",
                "deed_summary": (
                    "Inherited family seat in the Crownstone quarter — modest townhouse with study "
                    "and family ledgers, not a palace."
                ),
                "specs": {"prestige": 2, "comfort": 2, "bedrooms": 2, "modest": True},
            }
            if dry_run:
                summary["changes"].append("create property house_driscoll_city + 3 rooms")
            else:
                turn = int(state.get("turn") or 0)
                holding = create_holding_conn(
                    conn,
                    player_character_id=int(pc["id"]),
                    proposal=proposal,
                    acquired_at_turn=turn,
                )
                if holding:
                    for room_name, desc in (
                        ("Entry Hall", "Narrow hall with Driscoll crest faded on the wall."),
                        ("Study", "Oak shelves and a locked cabinet holding family ledgers."),
                        ("Master Suite", "A modest bedroom suite — comfortable, not extravagant."),
                        ("Guest Chamber", "A modest second bedroom for retainers or honored guests."),
                    ):
                        create_property_room_conn(
                            conn,
                            property_code="house_driscoll_city",
                            room_name=room_name,
                            description=desc,
                        )
                    sync_property_portfolio(conn, state)
                    state["property_portfolio"] = state.get("property_portfolio") or {}
                    state["property_portfolio"]["active_residence_code"] = "house_driscoll_city"
                    summary["changes"].append("seeded House Driscoll property")
                    create_fixture_conn(
                        conn,
                        property_code="house_driscoll_city",
                        room_name="Study",
                        name="Family Ledgers Cabinet",
                        fixture_kind="storage",
                        description="Locked oak cabinet holding House Driscoll ledgers.",
                        specs={"material": "oak", "storage_slots": 12},
                    )
                    summary["changes"].append("seeded Family Ledgers fixture in Study")

        if not dry_run:
            _repair_driscoll_estate_details(conn, state, summary)
            removed = dedupe_spurious_holdings_conn(conn, state)
            if removed:
                summary["changes"].append(f"removed spurious property holdings: {', '.join(removed)}")
            campaign_facts.pin_fact_conn(
                conn,
                "Lucas Driscoll inherited a modest city residence and family ledgers in Crownstone.",
                known_by="everyone",
            )
            summary["changes"].append("pinned property campaign fact")

        # --- Quest metadata + objectives ---
        quests_spec = [
            {
                "code": "the_seventy_sovereons_debt",
                "scale": "standard",
                "chain_code": "house_driscoll_legacy",
                "chain_position": 1,
                "rewards_deferred": 0,
                "rewards": {"gold": 70, "xp": 50},
                "objectives": [
                    (
                        "Present House Driscoll's debt claim at the Lysandra Corp kiosk",
                        "custom",
                        {"completion_signals": ["debt", "claim", "kiosk", "lysandra", "present"]},
                    ),
                    (
                        "Secure acknowledgment of the 70 sovereign debt",
                        "custom",
                        {"completion_signals": ["acknowledg", "sovereign", "debt", "seventy"]},
                    ),
                ],
            },
            {
                "code": "the_unnamed_concubine",
                "scale": "major",
                "chain_code": "house_driscoll_legacy",
                "chain_position": 2,
                "rewards_deferred": 0,
                "rewards": {
                    "xp": 150,
                    "companion": {"npc_code": "elara_voss", "role": "companion"},
                    "renown": {
                        "renown_code": "patron_of_house_driscoll",
                        "title_display": "Patron of House Driscoll",
                        "impact_tier": 3,
                        "scope_type": "region",
                        "scope_id": "Crownstone",
                    },
                },
                "objectives": [
                    ("Meet Elara Voss at the concubine residence", "talk_npc", {"target_code": "elara_voss"}),
                    (
                        "Apply the seventy-sovereign debt toward her acquisition",
                        "custom",
                        {
                            "completion_signals": [
                                "seventy",
                                "sovereign",
                                "debt",
                                "acquisition",
                                "elara",
                            ]
                        },
                    ),
                    (
                        "Formalize Elara's contract for House Driscoll",
                        "custom",
                        {
                            "completion_signals": [
                                "contract",
                                "formal",
                                "elara",
                                "driscoll",
                                "concubine",
                            ]
                        },
                    ),
                ],
            },
        ]
        for spec in quests_spec:
            row = conn.execute("SELECT id FROM quests WHERE code = ?", (spec["code"],)).fetchone()
            if not row:
                continue
            qid = int(row["id"])
            if dry_run:
                summary["changes"].append(f"update quest {spec['code']}")
                continue
            conn.execute(
                """
                UPDATE quests SET quest_scale = ?, chain_code = ?, chain_position = ?,
                       rewards_deferred = ?, rewards_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    spec["scale"],
                    spec["chain_code"],
                    spec["chain_position"],
                    spec["rewards_deferred"],
                    json.dumps(spec["rewards"]),
                    _utc_now(),
                    qid,
                ),
            )
            conn.execute("DELETE FROM quest_objectives WHERE quest_id = ?", (qid,))
            for idx, obj_spec in enumerate(spec["objectives"]):
                if len(obj_spec) == 2:
                    text, obj_type = obj_spec
                    cond = {}
                else:
                    text, obj_type, cond = obj_spec
                target_code = cond.get("target_code")
                if obj_type == "talk_npc" and not target_code and "Elara" in text:
                    npc = conn.execute("SELECT code FROM npcs WHERE name LIKE '%Elara%' LIMIT 1").fetchone()
                    target_code = npc["code"] if npc else None
                condition_json = json.dumps(cond) if cond else None
                conn.execute(
                    """
                    INSERT INTO quest_objectives (
                        quest_id, objective_type, description_text, status, optional,
                        sort_order, target_code, condition_json, completion_mode, created_at
                    ) VALUES (?, ?, ?, 'pending', 0, ?, ?, ?, 'auto', ?)
                    """,
                    (qid, obj_type, text, idx, target_code, condition_json, _utc_now()),
                )
            summary["changes"].append(f"updated quest {spec['code']}")

        if not dry_run:
            conn.commit()
            from titan.fugassa.db import state_repository

            state = state_repository.enrich_state_from_sql(db_path, state)
            write_game_json(slot_dir, state)
            summary["ok"] = True
        else:
            summary["ok"] = True
    finally:
        conn.close()
    return summary


def repair_fugassa_playthrough(save_id: str = "Fugassa") -> dict[str, Any]:
    """Retroactively apply narrative quest completion from chat history + fix estate rooms."""
    from titan.fugassa import quest_engine
    from titan.fugassa.db import state_repository
    from titan.fugassa.gm_response_parser import extract_current_scene_narrative
    from titan.fugassa.scene_character_context import scene_cast_metadata
    from titan.fugassa.turn_resolution import TurnResolution

    db_path = game_db_path(save_id)
    slot_dir = save_dir(save_id)
    if not os.path.isfile(db_path) or not os.path.isdir(slot_dir):
        return {"ok": False, "reason": "save_not_found"}

    summary: dict[str, Any] = {"save_id": save_id, "changes": []}
    state = read_game_json(slot_dir) or {}
    seed_fugassa_campaign(save_id, dry_run=False)
    summary["changes"].append("refreshed quest metadata and completion signals")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _repair_driscoll_estate_details(conn, state, summary)
        removed = dedupe_spurious_holdings_conn(conn, state)
        if removed:
            summary["changes"].append(f"removed spurious property holdings: {', '.join(removed)}")

        for code in ("elara_moonwhisper", "concubine_elara"):
            conn.execute(
                """
                UPDATE npcs SET status = 'dead',
                       notes = TRIM(COALESCE(notes, '') || ' [merged into elara_voss]')
                WHERE code = ?
                """,
                (code,),
            )
        summary["changes"].append("deprecated duplicate Elara NPC records")
        conn.commit()
    finally:
        conn.close()

    state = state_repository.enrich_state_from_sql(db_path, state)
    history = list(state.get("chat_history") or [])
    last_user = ""
    for entry in history:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        if role == "user":
            last_user = str(entry.get("content") or "")
            continue
        if role != "assistant":
            continue
        gm_prose = str(entry.get("content") or "")
        if len(gm_prose.strip()) < 40:
            continue
        scene_narrative = extract_current_scene_narrative(gm_prose)
        scene_cast = entry.get("scene_cast") or scene_cast_metadata(
            state=state,
            db_path=db_path,
            narrative=scene_narrative,
            player_action=last_user,
        )
        resolution = TurnResolution(mode="narrative_only", intent="narrative_only")
        quest_engine.evaluate_quests_after_gm(
            db_path,
            state,
            resolution,
            player_text=last_user,
            gm_prose=gm_prose,
            scene_cast=scene_cast if isinstance(scene_cast, dict) else None,
        )
        if resolution.quest:
            summary["changes"].append(f"turn {entry.get('turn_number')}: {resolution.quest.get('summary', '')[:120]}")

    state = state_repository.enrich_state_from_sql(db_path, state)
    write_game_json(slot_dir, state)
    summary["ok"] = True
    summary["party"] = [m.get("name") for m in state.get("party") or [] if isinstance(m, dict)]
    summary["active_quests"] = len((state.get("quests") or {}).get("active") or [])
    return summary


def repair_chronicle_save(
    save_id: str = "Fugassa",
    *,
    dry_run: bool = False,
    reindex_only: bool = False,
    recondense: bool = False,
) -> dict[str, Any]:
    """ADR §13 — backfill typed chronicle + vec reindex for pre-C1 saves."""
    slot_dir = save_dir(save_id)
    db_path = game_db_path(save_id)
    state = read_game_json(slot_dir) or {}
    return campaign_chronicle.repair_chronicle(
        db_path,
        state,
        dry_run=dry_run,
        reindex_only=reindex_only,
        recondense=recondense,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Seed/repair Fugassa campaign data")
    parser.add_argument("--save-id", default="Fugassa")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repair-playthrough", action="store_true", help="Replay chat for quest completion + estate fix")
    parser.add_argument("--repair-chronicle", action="store_true", help="Backfill typed chronicle events + vec reindex")
    parser.add_argument("--reindex-only", action="store_true", help="With --repair-chronicle: only rebuild vec indexes")
    parser.add_argument("--recondense", action="store_true", help="With --repair-chronicle: note digest recondense (manual)")
    args = parser.parse_args()
    if args.repair_chronicle:
        print(
            json.dumps(
                repair_chronicle_save(
                    args.save_id,
                    dry_run=args.dry_run,
                    reindex_only=args.reindex_only,
                    recondense=args.recondense,
                ),
                indent=2,
                default=str,
            )
        )
    elif args.repair_playthrough:
        print(json.dumps(repair_fugassa_playthrough(args.save_id), indent=2))
    else:
        print(json.dumps(seed_fugassa_campaign(args.save_id, dry_run=args.dry_run), indent=2))
