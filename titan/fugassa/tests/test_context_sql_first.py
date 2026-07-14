import sys, os, sqlite3, tempfile, asyncio
sys.path.insert(0, "/app")

from titan.fugassa.db import sqlite_store
from titan.fugassa import campaign_digest, gm_runner, memory_context, npc_generator, context_builder

FAILURES = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

def make_db():
    d = tempfile.mkdtemp(prefix="fugassa_ctxsql_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Ctx Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute("INSERT INTO player_characters (code, player_id, name) VALUES ('pc_hero', 1, 'Hero')")
    conn.commit()
    conn.close()
    return db_path

def insert_turn(db_path, turn_number, player_text, ai_text):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO turn_history (turn_number, player_text, ai_text, is_active) VALUES (?, ?, ?, 1)",
        (turn_number, player_text, ai_text),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# campaign_digest — rolling window trigger, deterministic fallback, mega merge
# ---------------------------------------------------------------------------

def test_no_condense_below_trigger():
    db_path = make_db()
    for i in range(29):
        insert_turn(db_path, i, f"do thing {i}", f"result {i}")
    result = asyncio.run(campaign_digest.maybe_condense(db_path, llm_enabled=False))
    check("29 active pairs stays below the 30-pair trigger", result.get("condensed") is False, result)
    check("active_pairs reported correctly", result.get("active_pairs") == 29, result)

def test_condense_fires_at_trigger_with_deterministic_fallback():
    db_path = make_db()
    for i in range(30):
        insert_turn(db_path, i, f"do thing {i}", f"the hero did thing {i} successfully")
    result = asyncio.run(campaign_digest.maybe_condense(db_path, llm_enabled=False))
    check("condense fires at 30 active pairs", result.get("condensed") is True, result)
    check("condenses exactly the oldest 15", result.get("batch_size") == 15, result)

    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    active = conn.execute("SELECT COUNT(*) c FROM turn_history WHERE is_active = 1").fetchone()["c"]
    check("15 rows remain active (rolling window intact)", active == 15, active)
    inactive = conn.execute("SELECT COUNT(*) c FROM turn_history WHERE is_active = 0").fetchone()["c"]
    check("15 rows marked inactive (condensed, still in DB)", inactive == 15, inactive)

    digest = campaign_digest.get_digest(db_path)
    check("digest text is non-empty after condensation", bool(digest["digest_text"].strip()), digest)
    check("digest mentions an early turn", "Turn 0" in digest["digest_text"], digest["digest_text"])
    conn.close()

def test_condense_is_idempotent_below_next_trigger():
    db_path = make_db()
    for i in range(30):
        insert_turn(db_path, i, f"action {i}", f"outcome {i}")
    asyncio.run(campaign_digest.maybe_condense(db_path, llm_enabled=False))
    # Only 15 active remain — condensing again should be a no-op until 15 more arrive.
    result2 = asyncio.run(campaign_digest.maybe_condense(db_path, llm_enabled=False))
    check("second condense call is a no-op with only 15 active pairs", result2.get("condensed") is False, result2)

def test_mega_merge_archives_when_digest_exceeds_cap():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    huge_text = "x" * (campaign_digest.DIGEST_MEGA_CAP_CHARS + 1)
    campaign_digest.append_digest_conn(conn, huge_text, last_condensed_turn=5)
    conn.commit()
    digest = campaign_digest.get_digest(db_path)
    check("digest text reset after exceeding the mega cap", digest["digest_text"] == "", digest["digest_text"][:50])
    import json
    anchors = json.loads(digest["mega_anchors_json"])
    check("mega anchor recorded the archived digest", len(anchors) == 1 and anchors[0]["char_count"] > campaign_digest.DIGEST_MEGA_CAP_CHARS, anchors)
    conn.close()

def test_build_digest_block_reflects_mega_anchor_count():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    campaign_digest.append_digest_conn(conn, "The kingdom fell into chaos.", last_condensed_turn=1)
    conn.commit()
    conn.close()
    block = memory_context.build_pinned_facts_block(db_path)  # sanity: unrelated block stays empty
    check("pinned facts block unaffected by digest writes", block == "", block)
    digest_block = campaign_digest.build_digest_block(db_path)
    check("digest block includes the condensed text", "kingdom fell into chaos" in digest_block, digest_block)
    check("digest block empty when nothing condensed yet", campaign_digest.build_digest_block(make_db()) == "", None)


# ---------------------------------------------------------------------------
# gm_runner — rolling window truncation of chat_history
# ---------------------------------------------------------------------------

def test_rolling_window_truncates_chat_history_to_last_30_messages():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"} for i in range(50)]
    state = {"chat_history": history, "location_state": {}, "player": {}, "party": [], "inventory": {}}
    messages = gm_runner.build_messages_for_history(state)
    non_system = [m for m in messages if m["role"] != "system"]
    check("only the last 30 messages are sent to the GM", len(non_system) == 30, len(non_system))
    check("oldest surfaced message is msg 20 (50 - 30)", non_system[0]["content"] == "msg 20", non_system[0])
    check("newest message is msg 49", non_system[-1]["content"] == "msg 49", non_system[-1])

def test_rolling_window_passthrough_when_under_limit():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    state = {"chat_history": history, "location_state": {}, "player": {}, "party": [], "inventory": {}}
    messages = gm_runner.build_messages_for_history(state)
    non_system = [m for m in messages if m["role"] != "system"]
    check("short history passes through unchanged", len(non_system) == 2, non_system)


# ---------------------------------------------------------------------------
# NPC scene brief (§5 row 4 — hexagon/goals/attitude)
# ---------------------------------------------------------------------------

def test_npc_scene_brief_surfaces_notable_traits_and_goals():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO locations (code, name) VALUES ('loc_brief', 'Market')")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    npc_id = npc_generator.spawn_npc(
        conn, name="Greta", tier="T2", location_id=loc_id, race="Dwarf", class_role="Blacksmith",
        goals=["Forge the perfect blade", "Retire wealthy"],
    )["npc_id"]
    conn.execute("UPDATE npc_personality_hex SET kindness = -3, boldness = 3 WHERE npc_id = ?", (npc_id,))
    conn.commit()

    brief = npc_generator.get_npc_scene_brief_conn(conn, npc_id)
    check("brief resolves name/race/role", brief["name"] == "Greta" and brief["race"] == "Dwarf", brief)
    check("notable hexagon traits surfaced", "cruel" in brief["traits"] and "bold" in brief["traits"], brief["traits"])
    check("public goals surfaced", "Forge the perfect blade" in brief["goals"], brief["goals"])
    conn.close()

    state = {"location_state": {"npc_details": [{"npc_id": npc_id, "name": "Greta"}]}}
    block = memory_context.build_npc_scene_briefs_block(db_path, state)
    check("scene brief block renders the NPC line", "Greta" in block and "cruel" in block, block)

def test_npc_scene_brief_block_empty_with_no_npcs_present():
    db_path = make_db()
    state = {"location_state": {}}
    block = memory_context.build_npc_scene_briefs_block(db_path, state)
    check("brief block empty when no NPCs in scene", block == "", block)


# ---------------------------------------------------------------------------
# context_builder wiring — all new blocks actually reach the prompt
# ---------------------------------------------------------------------------

def test_context_builder_includes_all_new_blocks():
    from titan.fugassa.turn_resolution import TurnResolution
    state = {"chat_history": [], "location_state": {}, "player": {}, "party": [], "inventory": {}}
    resolution = TurnResolution(mode="action", intent="narrative_only")
    messages = context_builder.build_gm_messages(
        state,
        turn_resolution=resolution,
        npc_brief_block="NPCS IN SCENE (hexagon/goals...): - Test NPC",
        memory_block="NPC MEMORY: - test memory",
        pinned_facts_block="PINNED CAMPAIGN FACTS: - test fact",
        scene_summary_block="RECENT SCENE HISTORY: - test summary",
        campaign_digest_block="CAMPAIGN DIGEST: - test digest",
        chronicle_hint_block="RECENT CAMPAIGN EVENTS: - quest complete",
    )
    system_text = messages[0]["content"]
    for needle in ("Test NPC", "test memory", "test fact", "test summary", "test digest", "quest complete"):
        check(f"system prompt contains '{needle}'", needle in system_text, system_text[-400:])


if __name__ == "__main__":
    test_no_condense_below_trigger()
    test_condense_fires_at_trigger_with_deterministic_fallback()
    test_condense_is_idempotent_below_next_trigger()
    test_mega_merge_archives_when_digest_exceeds_cap()
    test_build_digest_block_reflects_mega_anchor_count()
    test_rolling_window_truncates_chat_history_to_last_30_messages()
    test_rolling_window_passthrough_when_under_limit()
    test_npc_scene_brief_surfaces_notable_traits_and_goals()
    test_npc_scene_brief_block_empty_with_no_npcs_present()
    test_context_builder_includes_all_new_blocks()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")
