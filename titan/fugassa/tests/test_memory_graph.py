import sys, os, sqlite3, tempfile
sys.path.insert(0, "/app")

from titan.fugassa.db import sqlite_store
from titan.fugassa import (
    archivist,
    campaign_facts,
    memory_context,
    memory_graph,
    npc_agenda,
    npc_generator,
    renown_engine,
    scene_summary_engine,
)
from titan.fugassa.turn_resolver import resolve_turn

FAILURES = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

def make_db():
    d = tempfile.mkdtemp(prefix="fugassa_memgraph_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Memory Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute("INSERT INTO player_characters (code, player_id, name) VALUES ('pc_hero', 1, 'Hero')")
    conn.commit()
    conn.close()
    return db_path

def base_state(**overrides):
    state = {
        "player": {"x": 0, "y": 0, "z": 0, "map_code": "overworld"},
        "party": [],
        "location_state": {},
        "inventory": {"shared": []},
        "discovered_blocks": {},
        "turn": 0,
        "in_combat": False,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# memory_graph — link_memory / auto_link_memory / scene_relevance_top_k
# ---------------------------------------------------------------------------

def test_scene_relevance_boosts_linked_memories_over_plain_importance():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO locations (code, name) VALUES ('loc_tavern', 'Tavern')")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    npc_id = npc_generator.spawn_npc(conn, name="Bram", tier="T2", location_id=loc_id)["npc_id"]
    conn.execute("INSERT INTO quests (code, title, status, related_location_id) VALUES ('q1', 'Find the Amulet', 'active', ?)", (loc_id,))
    quest_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Higher importance, no links.
    conn.execute(
        "INSERT INTO npc_memories (npc_id, memory_type, memory_text, importance, created_at) VALUES (?, 'episodic', ?, 6, '2020-01-01T00:00:00')",
        (npc_id, "A vague rumor about distant lands."),
    )
    unlinked_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Lower importance, but linked to the active quest+location relevant to the current scene.
    conn.execute(
        "INSERT INTO npc_memories (npc_id, memory_type, memory_text, importance, created_at) VALUES (?, 'episodic', ?, 3, '2020-01-01T00:00:01')",
        (npc_id, "The player asked about the Amulet right here."),
    )
    linked_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    memory_graph.auto_link_memory_conn(conn, linked_id, npc_id=npc_id, location_id=loc_id, quest_ids=[quest_id])
    conn.commit()

    ranked = memory_graph.scene_relevance_top_k(conn, npc_id, location_id=loc_id, quest_ids=[quest_id], top_k=6)
    check("both memories returned", len(ranked) == 2, ranked)
    check("scene-linked memory outranks higher-importance unlinked one", ranked[0]["id"] == linked_id, ranked)
    check("unlinked memory still present (canon never hidden)", any(r["id"] == unlinked_id for r in ranked), ranked)
    conn.close()

def test_scene_relevance_falls_back_gracefully_with_no_links():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO locations (code, name) VALUES ('loc_x', 'X')")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    npc_id = npc_generator.spawn_npc(conn, name="Nia", tier="T2", location_id=loc_id)["npc_id"]
    conn.execute(
        "INSERT INTO npc_memories (npc_id, memory_type, memory_text, importance, created_at) VALUES (?, 'episodic', 'Met once.', 4, '2020-01-01')",
        (npc_id,),
    )
    conn.commit()
    ranked = memory_graph.scene_relevance_top_k(conn, npc_id, top_k=6)
    check("plain memory still surfaces with no scene context", len(ranked) == 1, ranked)
    conn.close()

def test_link_memory_conn_skips_dupes_and_invalid_types():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO locations (code, name) VALUES ('loc_y', 'Y')")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    npc_id = npc_generator.spawn_npc(conn, name="Ozzy", tier="T2", location_id=loc_id)["npc_id"]
    conn.execute(
        "INSERT INTO npc_memories (npc_id, memory_type, memory_text, importance, created_at) VALUES (?, 'episodic', 'x', 3, '2020-01-01')",
        (npc_id,),
    )
    mem_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    n1 = memory_graph.link_memory_conn(conn, mem_id, [("location", loc_id, "witnessed_at"), ("bogus", 1, "x")])
    n2 = memory_graph.link_memory_conn(conn, mem_id, [("location", loc_id, "witnessed_at")])
    conn.commit()
    check("only the valid link is written", n1 == 1, n1)
    check("re-linking the same entity is a no-op", n2 == 0, n2)
    conn.close()


# ---------------------------------------------------------------------------
# campaign_facts — pin / list / dedupe / auto-pin from renown + agenda reveal
# ---------------------------------------------------------------------------

def test_pin_fact_and_list_pinned():
    db_path = make_db()
    fid = campaign_facts.pin_fact(db_path, "The old bridge collapsed during the siege.", known_by="everyone")
    check("pin_fact returns an id", isinstance(fid, int), fid)
    facts = campaign_facts.list_pinned_facts(db_path)
    check("pinned fact appears in list", "The old bridge collapsed during the siege." in facts, facts)

def test_pin_fact_dedupes_identical_text():
    db_path = make_db()
    first = campaign_facts.pin_fact(db_path, "Duplicate fact.")
    second = campaign_facts.pin_fact(db_path, "Duplicate fact.")
    check("duplicate pin returns the same row id", first == second, (first, second))
    facts = campaign_facts.list_pinned_facts(db_path, limit=50)
    check("duplicate fact stored only once", facts.count("Duplicate fact.") == 1, facts)

def test_tier4_renown_grant_auto_pins_campaign_fact():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    renown_engine.grant_renown_conn(
        conn, renown_code="hero_of_amalur", scope_type="region", scope_id="amalur",
        valence="positive", impact_tier=4, title_display="Hero of Amalur",
    )
    conn.commit()
    facts = campaign_facts.list_pinned_facts(db_path)
    check("tier-4 renown grant pins a campaign fact", any("Hero of Amalur" in f for f in facts), facts)
    conn.close()

def test_tier2_renown_grant_does_not_pin_fact():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    renown_engine.grant_renown_conn(
        conn, renown_code="helped_the_baker", scope_type="region", scope_id="amalur",
        valence="positive", impact_tier=2,
    )
    conn.commit()
    facts = campaign_facts.list_pinned_facts(db_path)
    check("tier-2 renown grant stays out of the curated pinned list", facts == [], facts)
    conn.close()

def test_agenda_reveal_auto_pins_campaign_fact():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO locations (code, name) VALUES ('loc_z', 'Z')")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    npc_id = npc_generator.spawn_npc(
        conn, name="Silas", tier="T3", location_id=loc_id,
        public_disposition="friendly", secret_disposition="hostile", agenda_code="steal_artifact",
    )["npc_id"]
    conn.commit()
    npc_agenda.reveal_agenda_conn(conn, npc_id, turn=3, method="test")
    conn.commit()
    facts = campaign_facts.list_pinned_facts(db_path)
    check("betrayal reveal pins a campaign fact", any("Silas" in f and "revealed" in f for f in facts), facts)
    conn.close()


# ---------------------------------------------------------------------------
# scene_summary_engine — generation on location exit, retrieval on revisit
# ---------------------------------------------------------------------------

def test_scene_summary_generated_on_location_change_via_turn_pipeline():
    db_path = make_db()
    state = base_state()

    res1 = resolve_turn(state, "go south", db_path)
    check("first move succeeds", res1.travel.get("summary", "").startswith("Traveled"), res1.travel)
    loc_a = (state.get("location_state") or {}).get("location_id")
    check("location_id tracked after first move", bool(loc_a), state.get("location_state"))

    # grid_engine bumps `state["turn"]` on every move (independent of the
    # chat-pipeline's own per-turn increment) — read back the turn actually
    # recorded as this location's entry point rather than assuming turn 0.
    entry_turn_a = int((state.get("_location_entry_turn") or {}).get(str(loc_a), 0))
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO event_log (code, event_type, title, summary, turn_id) VALUES ('ev1', 'turn', 'Turn 0', 'The hero haggled with a merchant.', ?)",
        (entry_turn_a,),
    )
    conn.commit()
    conn.close()

    res2 = resolve_turn(state, "go south", db_path)
    check("second move succeeds", res2.travel.get("summary", "").startswith("Traveled"), res2.travel)
    loc_b = (state.get("location_state") or {}).get("location_id")
    check("player is now at a different location", loc_b != loc_a, (loc_a, loc_b))

    summaries = scene_summary_engine.latest_summaries_for_location(db_path, loc_a)
    check("scene summary written for the location just left", len(summaries) == 1, summaries)
    check("summary text carries the event_log content", summaries and "haggled" in summaries[0], summaries)

    check("no summary yet for the location just entered (not left)", scene_summary_engine.latest_summaries_for_location(db_path, loc_b) == [], None)

def test_scene_summary_skipped_when_no_events_happened():
    db_path = make_db()
    state = base_state()
    resolve_turn(state, "go south", db_path)
    loc_a = (state.get("location_state") or {}).get("location_id")
    state["turn"] = 1
    resolve_turn(state, "go south", db_path)
    summaries = scene_summary_engine.latest_summaries_for_location(db_path, loc_a)
    check("no summary written when nothing happened at the location", summaries == [], summaries)

def test_build_scene_summary_block_surfaces_recap_on_revisit():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO locations (code, name) VALUES ('loc_revisit', 'Old Mill')")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    scene_summary_engine.generate_on_location_exit_conn(conn, from_location_id=loc_id, turn_start=0, turn_end=0)
    conn.commit()
    conn.execute(
        "INSERT INTO event_log (code, event_type, title, summary, turn_id) VALUES ('ev_mill', 'turn', 'Turn 0', 'A fire broke out at the mill.', 0)"
    )
    conn.commit()
    # Regenerate properly now that an event exists.
    conn.execute("DELETE FROM scene_summaries")
    scene_summary_engine.generate_on_location_exit_conn(conn, from_location_id=loc_id, turn_start=0, turn_end=0)
    conn.commit()
    conn.close()

    state = base_state()
    state["location_state"] = {"location_id": loc_id}
    block = memory_context.build_scene_summary_block(db_path, state)
    check("scene summary block renders on revisit", "fire broke out" in block, block)

def test_build_pinned_facts_block_empty_when_no_facts():
    db_path = make_db()
    block = memory_context.build_pinned_facts_block(db_path)
    check("pinned facts block empty string when nothing pinned", block == "", block)


if __name__ == "__main__":
    test_scene_relevance_boosts_linked_memories_over_plain_importance()
    test_scene_relevance_falls_back_gracefully_with_no_links()
    test_link_memory_conn_skips_dupes_and_invalid_types()
    test_pin_fact_and_list_pinned()
    test_pin_fact_dedupes_identical_text()
    test_tier4_renown_grant_auto_pins_campaign_fact()
    test_tier2_renown_grant_does_not_pin_fact()
    test_agenda_reveal_auto_pins_campaign_fact()
    test_scene_summary_generated_on_location_change_via_turn_pipeline()
    test_scene_summary_skipped_when_no_events_happened()
    test_build_scene_summary_block_surfaces_recap_on_revisit()
    test_build_pinned_facts_block_empty_when_no_facts()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")
