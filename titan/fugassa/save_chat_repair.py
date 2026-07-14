"""Repair Fugassa save chat: dedupe user lines, trim looped GM replies, sync SQL."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any

from titan.fugassa.gm_response_parser import assistant_text_from_response, strip_chat_meta_sections

_REASONING_RE = re.compile(r"(?im)^(?:I'm|I am|I'll|I've|I'd)\s+")


def _dedupe_chat_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in history:
        if (
            out
            and out[-1].get("role") == msg.get("role") == "user"
            and str(out[-1].get("content") or "").strip()
            == str(msg.get("content") or "").strip()
        ):
            continue
        out.append(dict(msg))
    return out


def _clean_assistant(text: str) -> str:
    return assistant_text_from_response(str(text or ""))


def _auctioneer_name_fix(text: str) -> str:
    """Rename auctioneer Kaelen/Kaelen Voss → Harven Vale; keep Elara Voss and other Kaelens."""
    s = str(text or "")
    s = re.sub(r"Merchant Kaelen Voss", "Harven Vale", s)
    s = re.sub(r"(?<!Elara )Kaelen Voss", "Harven Vale", s)
    s = re.sub(r"\bKaelen\b(?! (?:Driscoll|Ashford)\b)", "Harven Vale", s)
    s = re.sub(r"Harven Vale Vale", "Harven Vale", s)
    s = re.sub(r'"No, Harven Vale\.', '"No, Harven.', s)
    s = re.sub(r"'No, Harven Vale\.", "'No, Harven.", s)
    return s


def _fix_text_blob(text: str) -> str:
    return _auctioneer_name_fix(_clean_assistant(str(text or "")))


def _patch_market_location_manifest(conn: sqlite3.Connection) -> dict[str, int]:
    """Swap guard Kaelen Driscoll for auctioneer Harven Vale in market manifest."""
    stats = {"manifest_patched": 0, "guard_renamed": 0}
    row = conn.execute("SELECT notes FROM locations WHERE id = 1").fetchone()
    if not row or not row[0]:
        return stats
    try:
        notes = json.loads(row[0])
    except json.JSONDecodeError:
        return stats
    plan = notes.get("plan") if isinstance(notes.get("plan"), dict) else {}
    present = plan.get("present_npcs") if isinstance(plan.get("present_npcs"), list) else []
    new_present: list[dict[str, Any]] = []
    replaced_guard = False
    for npc in present:
        if not isinstance(npc, dict):
            continue
        name = str(npc.get("name") or "")
        if name == "Kaelen Driscoll":
            replaced_guard = True
            continue
        new_present.append(npc)
    if replaced_guard:
        new_present.insert(
            0,
            {
                "name": "Harven Vale",
                "role": "Lysandra Corp auctioneer at the market kiosk",
                "race": "Human",
                "is_important": True,
                "backstory_summary": (
                    "A sharp-tongued broker who presents concubines and settles corporate debts "
                    "for Lysandra Corp at the Market District kiosk."
                ),
                "visibility": "present",
            },
        )
        plan["present_npcs"] = new_present
        notes["plan"] = plan
    spawned = [str(n) for n in (notes.get("spawned_present") or [])]
    spawned = ["Harven Vale" if n == "Kaelen Driscoll" else n for n in spawned]
    if "Harven Vale" not in spawned:
        spawned.insert(0, "Harven Vale")
    notes["spawned_present"] = spawned
    conn.execute(
        "UPDATE locations SET notes = ?, updated_at = datetime('now') WHERE id = 1",
        (json.dumps(notes, ensure_ascii=False),),
    )
    stats["manifest_patched"] = 1

    cur = conn.execute(
        "UPDATE npcs SET code = ?, name = ?, updated_at = datetime('now') "
        "WHERE code = 'kaelen_driscoll'",
        ("crownstone_guard_roric_hale", "Roric Hale"),
    )
    stats["guard_renamed"] = cur.rowcount
    return stats


def repair_fugassa_auctioneer_identity(save_dir: str) -> dict[str, Any]:
    """Fix auctioneer naming in chat/SQL and restore Harven Vale as visible market NPC."""
    game_json = os.path.join(save_dir, "game.json")
    game_db = os.path.join(save_dir, "game.db")
    with open(game_json, encoding="utf-8") as f:
        state = json.load(f)

    for msg in state.get("chat_history") or []:
        if msg.get("role") in ("assistant", "user"):
            msg["content"] = _fix_text_blob(str(msg.get("content") or ""))

    loc = dict(state.get("location_state") or {})
    loc["narrative_npcs"] = ["Harven Vale", "Elara Voss"]
    state["location_state"] = loc

    cache = dict(state.get("cell_location_cache") or {})
    for key, cached in cache.items():
        if not isinstance(cached, dict):
            continue
        npcs = [("Harven Vale" if n == "Kaelen Driscoll" else n) for n in (cached.get("npcs") or [])]
        if "Harven Vale" not in npcs:
            npcs.insert(0, "Harven Vale")
        cached["npcs"] = [n for n in npcs if n != "Kaelen Driscoll"]
        cache[key] = cached
    state["cell_location_cache"] = cache

    manifest_stats: dict[str, int] = {}
    sql_stats = {"turn_history_updated": 0, "npc_memories_updated": 0, "scene_summaries_updated": 0}
    if os.path.isfile(game_db):
        conn = sqlite3.connect(game_db)
        try:
            manifest_stats = _patch_market_location_manifest(conn)
            conn.execute(
                "UPDATE npcs SET current_location_id = 1, updated_at = datetime('now') "
                "WHERE code = 'merchant_harven_vale'"
            )
            conn.execute(
                "UPDATE npcs SET current_location_id = 2, updated_at = datetime('now') "
                "WHERE code = 'elara_voss'"
            )
            for mem_id, text in conn.execute("SELECT id, memory_text FROM npc_memories"):
                cleaned = _fix_text_blob(str(text or ""))
                if cleaned != text:
                    conn.execute(
                        "UPDATE npc_memories SET memory_text = ? WHERE id = ?",
                        (cleaned, mem_id),
                    )
                    sql_stats["npc_memories_updated"] += 1
            for turn_no, ai_text, player_text in conn.execute(
                "SELECT turn_number, ai_text, player_text FROM turn_history"
            ):
                new_ai = _fix_text_blob(str(ai_text or ""))
                new_player = _auctioneer_name_fix(str(player_text or ""))
                if new_ai != ai_text or new_player != player_text:
                    conn.execute(
                        "UPDATE turn_history SET ai_text = ?, player_text = ? WHERE turn_number = ?",
                        (new_ai, new_player, int(turn_no)),
                    )
                    sql_stats["turn_history_updated"] += 1
            for row_id, summary in conn.execute("SELECT id, summary_text FROM scene_summaries"):
                fixed = _auctioneer_name_fix(str(summary or ""))
                if fixed != summary:
                    conn.execute(
                        "UPDATE scene_summaries SET summary_text = ? WHERE id = ?",
                        (fixed, row_id),
                    )
                    sql_stats["scene_summaries_updated"] += 1
            sql_stats["turn_history_updated"] += _sync_turn_history(conn, state.get("chat_history") or [])
            sql_stats["event_log_updated"] = _sync_event_log(conn, state.get("chat_history") or [])
            conn.commit()
        finally:
            conn.close()

        from titan.fugassa.db import sqlite_store, state_repository
        from titan.fugassa.location_population_engine import refresh_location_npcs_from_sql
        from titan.fugassa.narrative_movement import enrich_location_context

        state_repository.sync_location_state_npcs(game_db, state, 1)
        loc_id = int((state.get("location_state") or {}).get("location_id") or 1)
        state["location_state"] = enrich_location_context(
            game_db, state.get("location_state") or {}, location_id=loc_id
        )
        refresh_location_npcs_from_sql(game_db, state)
        sqlite_store.update_turn_number(game_db, int(state.get("turn") or 13))
        with open(game_json, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        state_repository.export_json_snapshot(game_db, state, save_dir)

    with open(game_json, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    return {
        "save_dir": save_dir,
        "visible_npcs": (state.get("location_state") or {}).get("npcs"),
        **manifest_stats,
        **sql_stats,
    }


def _collapse_duplicate_tail_turn(
    history: list[dict[str, Any]],
    *,
    merged_user_text: str,
    merged_gm_text: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Drop the last user+assistant pair and rewrite the previous beat."""
    if len(history) < 4:
        return history, False
    last_gm = history[-1]
    last_user = history[-2]
    gm_prev = history[-3]
    user_prev = history[-4]
    if (
        last_gm.get("role") != "assistant"
        or last_user.get("role") != "user"
        or gm_prev.get("role") != "assistant"
        or user_prev.get("role") != "user"
    ):
        return history, False
    out = history[:-2]
    out[-2] = {**user_prev, "content": merged_user_text.strip()}
    turn_no = gm_prev.get("turn_number")
    if turn_no is None and last_gm.get("turn_number") is not None:
        turn_no = int(last_gm["turn_number"]) - 1
    out[-1] = {
        **gm_prev,
        "content": _clean_assistant(merged_gm_text),
        "turn_number": turn_no,
    }
    return out, True


FUGASSA_MERGED_USER_TURN_13 = (
    "Confirm whether the seventy sovereigns Lysandra Corp owes House Driscoll are separate from "
    "acquiring Elara—and make clear the debt is owed to us, not by us. Present the letter to "
    "the auctioneer."
)

FUGASSA_MERGED_GM_TURN_13 = """Lucas extends the crumpled letter toward Harven Vale with deliberate care—his fingers steady, his eyes meeting the auctioneer's as if to say: *This is not a request. This is a claim.* "I am taking Elara Voss," he says, voice measured but firm. "As my slave and concubine. And I believe this letter settles the matter House Driscoll has with Lysandra Corp."

He pauses, then adds with quiet emphasis: "To be clear—the seventy sovereigns are owed *to* House Driscoll by Lysandra Corp, not the other way around. The enchanted silks delivered to the high courts were never paid when my father's estate was settled."

Harven takes the document with both hands and bows once more. Recognition deepens into something like relief. "Then it is as I believed, sir," he says quietly. "Elara Voss is indeed the unnamed concubine of considerable value your family's letter speaks of."

He considers before continuing: "The seventy sovereigns remain a separate line in the ledger—owed to you by Lysandra Corp—and they are not mutually exclusive with Elara's acquisition. The letter confirms we recognize your claim. Elara herself may come to you without additional payment beyond what has been discussed."

Lucas watches him closely. Harven senses the scrutiny and adds: "If you had purchased Elara outright for two hundred sovereigns, that sum would have covered both her price and the debt, leaving a surplus. Since we treat this as an exchange of favors rather than a simple purchase, the seventy sovereigns remain owed separately—but they will be honored when convenient for Lysandra Corp."

His eyes meet Lucas's once more: "And now, if you would allow me, I shall call Mistress Elara Voss forward." He turns and calls out in a clear voice that carries through the residence: "Mistress Elara! The time has come. Come and claim your place with House Driscoll."

- Receive Elara's formal presentation as your new concubine
- Discuss final terms with Harven Vale regarding payment schedule for the 70 sovereigns
- Decide on any additional arrangements (clothing, collar, duties)
- Begin preparing to take Elara from Lysandra Corp and back to your own residence

What do you do next?"""

MARKET_DISTRICT_NAME = "City Town Square — Market District"


def repair_player_market_location(
    conn: sqlite3.Connection,
    state: dict[str, Any],
    *,
    market_location_id: int = 1,
    from_turn: int = 11,
) -> dict[str, Any]:
    """Exit sublocation back to Market District grid cell; sync SQL + chat headers."""
    player = dict(state.get("player") or {})
    player.pop("sublocation_id", None)
    player.pop("sublocation_anchor", None)
    state["player"] = player

    conn.execute(
        "UPDATE npcs SET current_location_id = ?, updated_at = datetime('now') "
        "WHERE code = 'merchant_harven_vale'",
        (int(market_location_id),),
    )
    conn.execute(
        "UPDATE player_characters SET current_location_id = ?, updated_at = datetime('now') "
        "WHERE code = 'pc_hero'",
        (int(market_location_id),),
    )

    loc_row = conn.execute(
        "SELECT id, name, description_short, image_path, parent_location_id FROM locations WHERE id = ?",
        (int(market_location_id),),
    ).fetchone()
    if loc_row:
        loc_id, loc_name, loc_desc, loc_image, parent_id = loc_row
        state["location_state"] = {
            "location_id": int(loc_id),
            "name": str(loc_name or ""),
            "description": str(loc_desc or ""),
            "scene_asset": str(loc_image or "") or None,
            "parent_location_id": parent_id,
            "is_sublocation": False,
            "npcs": [],
            "hidden_npcs": [],
            "enemies": [],
            "loot": [],
            "sublocations": [],
        }
        state["_current_location_id"] = int(loc_id)
        state.pop("_current_sublocation_id", None)

    for msg in state.get("chat_history") or []:
        turn_no = msg.get("turn_number")
        if msg.get("role") == "assistant" and turn_no is not None and int(turn_no) >= from_turn:
            msg["location"] = MARKET_DISTRICT_NAME

    return {"market_location_id": market_location_id, "from_turn": from_turn}


def repair_npc_names_and_locations(conn: sqlite3.Connection, state: dict[str, Any]) -> dict[str, int]:
    """Rename duplicate Voss/Kaelen NPCs and trim residence population."""
    stats = {"npcs_renamed": 0, "npcs_relocated": 0}
    renames = {
        "merchant_kaelen_voss": ("merchant_harven_vale", "Harven Vale"),
        "seraphina_voss": ("seraphina_lysande", "Seraphina Lysande"),
    }
    for old_code, (new_code, new_name) in renames.items():
        cur = conn.execute(
            "UPDATE npcs SET code = ?, name = ?, updated_at = datetime('now') WHERE code = ?",
            (new_code, new_name, old_code),
        )
        stats["npcs_renamed"] += cur.rowcount

    # Keep only scene-relevant NPCs inside the residence; others back on the market grid.
    keep_at_residence = {"elara_voss", "merchant_harven_vale"}
    rows = conn.execute(
        "SELECT id, code FROM npcs WHERE current_location_id = 2"
    ).fetchall()
    market_id = conn.execute(
        "SELECT id FROM locations WHERE id = 1 OR code LIKE '%market%' ORDER BY id LIMIT 1"
    ).fetchone()
    market_loc = int(market_id[0]) if market_id else 1
    for npc_id, code in rows:
        if str(code) in keep_at_residence:
            continue
        conn.execute(
            "UPDATE npcs SET current_location_id = ?, updated_at = datetime('now') WHERE id = ?",
            (market_loc, int(npc_id)),
        )
        stats["npcs_relocated"] += 1

    return stats


def _delete_turn_from_sql(conn: sqlite3.Connection, turn_number: int) -> None:
    conn.execute("DELETE FROM turn_history WHERE turn_number = ?", (int(turn_number),))
    conn.execute("DELETE FROM event_log WHERE turn_id = ?", (int(turn_number),))


def repair_fugassa_restore_chat_and_location(
    save_dir: str,
    *,
    backup_name: str = "game.json.bak-pre-chat-repair",
) -> dict[str, Any]:
    """Re-clean GM chat from backup (keep suggestions), fix market location — idempotent."""
    game_json = os.path.join(save_dir, "game.json")
    game_db = os.path.join(save_dir, "game.db")
    backup_path = os.path.join(save_dir, backup_name)
    if not os.path.isfile(backup_path):
        raise FileNotFoundError(backup_path)

    with open(game_json, encoding="utf-8") as f:
        state = json.load(f)
    with open(backup_path, encoding="utf-8") as f:
        backup = json.load(f)

    backup_by_turn: dict[int, str] = {}
    for msg in backup.get("chat_history") or []:
        if msg.get("role") != "assistant":
            continue
        turn_no = msg.get("turn_number")
        if turn_no is not None:
            backup_by_turn[int(turn_no)] = str(msg.get("content") or "")

    history = list(state.get("chat_history") or [])
    for msg in history:
        if msg.get("role") != "assistant":
            if msg.get("role") == "user":
                msg["content"] = _auctioneer_name_fix(str(msg.get("content") or ""))
            continue
        turn_no = msg.get("turn_number")
        if turn_no == 14:
            continue
        if turn_no == 13:
            src = FUGASSA_MERGED_GM_TURN_13
        elif turn_no is not None:
            src = backup_by_turn.get(int(turn_no), "")
        else:
            src = ""
        if not src:
            src = str(msg.get("content") or "")
        msg["content"] = _auctioneer_name_fix(_clean_assistant(src))

    state["chat_history"] = _dedupe_chat_history(
        [m for m in history if not (m.get("role") == "assistant" and m.get("turn_number") == 14)]
    )
    state["turn_phase"] = "reading"

    npc_stats: dict[str, int] = {}
    loc_stats: dict[str, Any] = {}
    sql_stats = {"turn_history_updated": 0}
    if os.path.isfile(game_db):
        conn = sqlite3.connect(game_db)
        try:
            npc_stats = repair_npc_names_and_locations(conn, state)
            loc_stats = repair_player_market_location(conn, state)
            sql_stats["turn_history_updated"] = _sync_turn_history(conn, state["chat_history"])
            sql_stats["event_log_updated"] = _sync_event_log(conn, state["chat_history"])
            sql_stats["npc_memories_updated"] = _clean_npc_memories(conn)
            conn.commit()
        finally:
            conn.close()

        from titan.fugassa.db import sqlite_store, state_repository
        from titan.fugassa.location_population_engine import refresh_location_npcs_from_sql
        from titan.fugassa.narrative_movement import enrich_location_context

        state_repository.sync_location_state_npcs(game_db, state, int(loc_stats.get("market_location_id") or 1))
        loc_id = int((state.get("location_state") or {}).get("location_id") or 1)
        state["location_state"] = enrich_location_context(
            game_db, state.get("location_state") or {}, location_id=loc_id
        )
        refresh_location_npcs_from_sql(game_db, state)
        sqlite_store.update_turn_number(game_db, int(state.get("turn") or 13))
        with open(game_json, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        state_repository.export_json_snapshot(game_db, state, save_dir)

    return {
        "save_dir": save_dir,
        "chat_entries": len(state["chat_history"]),
        **npc_stats,
        **loc_stats,
        **sql_stats,
    }


def repair_fugassa_duplicate_turn(save_dir: str) -> dict[str, Any]:
    """Merge duplicate GM turns 13–14, strip meta sections, fix NPCs — no new chat turn."""
    game_json = os.path.join(save_dir, "game.json")
    game_db = os.path.join(save_dir, "game.db")
    with open(game_json, encoding="utf-8") as f:
        state = json.load(f)

    history = list(state.get("chat_history") or [])
    collapsed, did_merge = _collapse_duplicate_tail_turn(
        history,
        merged_user_text=FUGASSA_MERGED_USER_TURN_13,
        merged_gm_text=FUGASSA_MERGED_GM_TURN_13,
    )
    if not did_merge:
        raise RuntimeError("Could not find duplicate tail turn pair to merge")

    history = repair_chat_history(collapsed)
    for msg in history:
        if msg.get("role") == "assistant":
            msg["content"] = _auctioneer_name_fix(_clean_assistant(str(msg.get("content") or "")))
        elif msg.get("role") == "user":
            msg["content"] = _auctioneer_name_fix(str(msg.get("content") or ""))
    state["chat_history"] = history
    state["turn"] = 13
    state["turn_phase"] = "reading"

    npc_stats: dict[str, int] = {}
    loc_stats: dict[str, Any] = {}
    sql_stats = {"turn_history_updated": 0, "turn_14_deleted": 0}
    if os.path.isfile(game_db):
        conn = sqlite3.connect(game_db)
        try:
            npc_stats = repair_npc_names_and_locations(conn, state)
            loc_stats = repair_player_market_location(conn, state)
            _delete_turn_from_sql(conn, 14)
            sql_stats["turn_14_deleted"] = 1
            conn.execute(
                "UPDATE turn_history SET player_text = ?, ai_text = ? WHERE turn_number = 13",
                (FUGASSA_MERGED_USER_TURN_13, history[-1]["content"]),
            )
            sql_stats["turn_history_updated"] = _sync_turn_history(conn, history)
            sql_stats["event_log_updated"] = _sync_event_log(conn, history)
            sql_stats["npc_memories_updated"] = _clean_npc_memories(conn)
            conn.commit()
        finally:
            conn.close()

    from titan.fugassa.db import state_repository
    from titan.fugassa.location_population_engine import refresh_location_npcs_from_sql

    if os.path.isfile(game_db):
        from titan.fugassa.db import sqlite_store
        from titan.fugassa.narrative_movement import enrich_location_context

        state_repository.sync_location_state_npcs(game_db, state, int(loc_stats.get("market_location_id") or 1))
        loc_id = int((state.get("location_state") or {}).get("location_id") or 1)
        state["location_state"] = enrich_location_context(
            game_db, state.get("location_state") or {}, location_id=loc_id
        )
        refresh_location_npcs_from_sql(game_db, state)
        sqlite_store.update_turn_number(game_db, 13)
        with open(game_json, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        state_repository.export_json_snapshot(game_db, state, save_dir)

    with open(game_json, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    return {
        "save_dir": save_dir,
        "merged_turn": 13,
        "removed_turn": 14,
        "chat_entries": len(history),
        **npc_stats,
        **loc_stats,
        **sql_stats,
    }


def _clean_memory_text(text: str) -> str:
    cleaned = _clean_assistant(text)
    m = _REASONING_RE.search(cleaned)
    if m and m.start() > 80:
        cleaned = cleaned[: m.start()].strip()
    return cleaned.strip()


def repair_chat_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history = _dedupe_chat_history(history)
    for msg in history:
        if msg.get("role") == "assistant":
            msg["content"] = _clean_assistant(str(msg.get("content") or ""))
    return history


def _sync_turn_history(conn: sqlite3.Connection, history: list[dict[str, Any]]) -> int:
    updated = 0
    for msg in history:
        if msg.get("role") != "assistant":
            continue
        turn_no = msg.get("turn_number")
        if turn_no is None:
            continue
        content = str(msg.get("content") or "")
        cur = conn.execute(
            "UPDATE turn_history SET ai_text = ? WHERE turn_number = ? AND ai_text != ?",
            (content, int(turn_no), content),
        )
        updated += cur.rowcount
    return updated


def _sync_event_log(conn: sqlite3.Connection, history: list[dict[str, Any]]) -> int:
    updated = 0
    for msg in history:
        if msg.get("role") != "assistant":
            continue
        turn_no = msg.get("turn_number")
        if turn_no is None:
            continue
        excerpt = str(msg.get("content") or "")[:500]
        rows = conn.execute(
            "SELECT id, details_json FROM event_log WHERE turn_id = ? AND event_type = 'turn'",
            (int(turn_no),),
        ).fetchall()
        for row_id, details_raw in rows:
            if not details_raw:
                continue
            try:
                details = json.loads(details_raw)
            except json.JSONDecodeError:
                continue
            if details.get("gm_excerpt") == excerpt:
                continue
            details["gm_excerpt"] = excerpt
            conn.execute(
                "UPDATE event_log SET details_json = ? WHERE id = ?",
                (json.dumps(details, ensure_ascii=False), row_id),
            )
            updated += 1
    return updated


def _clean_npc_memories(conn: sqlite3.Connection) -> int:
    updated = 0
    for mem_id, text in conn.execute("SELECT id, memory_text FROM npc_memories"):
        cleaned = _clean_memory_text(str(text or ""))
        if cleaned and cleaned != text:
            conn.execute(
                "UPDATE npc_memories SET memory_text = ? WHERE id = ?",
                (cleaned, mem_id),
            )
            updated += 1
    return updated


def repair_save_dir(save_dir: str, *, set_reading_phase: bool = True) -> dict[str, Any]:
    """Repair game.json + game.db under a Fugassa save directory."""
    game_json = os.path.join(save_dir, "game.json")
    game_db = os.path.join(save_dir, "game.db")
    if not os.path.isfile(game_json):
        raise FileNotFoundError(game_json)

    with open(game_json, encoding="utf-8") as f:
        state = json.load(f)

    before = len(state.get("chat_history") or [])
    history = repair_chat_history(list(state.get("chat_history") or []))
    state["chat_history"] = history
    if set_reading_phase:
        state["turn_phase"] = "reading"

    with open(game_json, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    stats: dict[str, Any] = {
        "save_dir": save_dir,
        "chat_entries_before": before,
        "chat_entries_after": len(history),
        "turn_history_updated": 0,
        "event_log_updated": 0,
        "npc_memories_updated": 0,
    }

    if os.path.isfile(game_db):
        conn = sqlite3.connect(game_db)
        try:
            stats["turn_history_updated"] = _sync_turn_history(conn, history)
            stats["event_log_updated"] = _sync_event_log(conn, history)
            stats["npc_memories_updated"] = _clean_npc_memories(conn)
            conn.commit()
        finally:
            conn.close()

    return stats


def main() -> None:
    import sys

    from titan.fugassa.paths import SAVES_DIR

    names = sys.argv[1:] or ["Fugassa"]
    for name in names:
        save_dir = os.path.join(SAVES_DIR, name)
        stats = repair_save_dir(save_dir)
        prev_dir = os.path.join(save_dir, "autosave_prev")
        if os.path.isfile(os.path.join(prev_dir, "game.json")):
            prev_stats = repair_save_dir(prev_dir, set_reading_phase=False)
            stats["autosave_prev"] = prev_stats
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
