import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from titan.fugassa import quest_engine, quest_narrative
from titan.fugassa.db import sqlite_store
from titan.fugassa.turn_resolution import TurnResolution

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def make_db():
    d = tempfile.mkdtemp(prefix="fugassa_qn_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Narrative Quest", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute("INSERT INTO player_characters (code, player_id, name) VALUES ('pc_hero', 1, 'Lucas')")
    conn.execute("INSERT INTO locations (code, name, is_discovered) VALUES ('loc_a', 'Kiosk', 1)")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE player_characters SET current_location_id = ? WHERE code='pc_hero'", (loc_id,))
    conn.execute(
        "INSERT INTO npcs (code, name, current_location_id, status) VALUES ('elara_voss', 'Elara Voss', ?, 'alive')",
        (loc_id,),
    )
    conn.commit()
    conn.close()
    return db_path


def base_state():
    return {
        "player": {"x": 0, "y": 0, "z": 0},
        "party": [{"name": "Lucas", "role": "player", "hp": 10, "max_hp": 10}],
        "location_state": {"name": "Kiosk"},
        "inventory": {"shared": []},
        "world_time": {"day": 1, "hour": 8},
        "turn": 5,
        "quests": {"active": [], "closed": []},
    }


def test_custom_objective_completes_from_gm_prose():
    db_path = make_db()
    state = base_state()
    qid = quest_engine.create_quest(
        db_path,
        code="concubine",
        title="The Unnamed Concubine",
        objectives=[
            {
                "objective_type": "custom",
                "description_text": "Formalize Elara's contract for House Driscoll",
                "condition": {
                    "completion_signals": ["contract", "formal", "elara", "driscoll"],
                },
            }
        ],
        rewards={"xp": 50, "companion": {"npc_code": "elara_voss"}},
    )
    assert qid
    player_text = "Formalize Elara's contract for House Driscoll."
    gm_prose = (
        "Harven presents the contract bound in polished leather, sealed with Lysandra Corp's crest. "
        "Lucas formally accepts Elara Voss into House Driscoll by signed agreement."
    )
    scene_cast = {"primary": ["Elara Voss", "Harven Vale"], "secondary": ["Lucas"]}
    resolution = TurnResolution(mode="narrative_only", intent="narrative_only")
    quest_engine.evaluate_quests_after_gm(
        db_path,
        state,
        resolution,
        player_text=player_text,
        gm_prose=gm_prose,
        scene_cast=scene_cast,
    )
    check("quest completed in resolution", "Quest completed" in str(resolution.quest.get("summary", "")))
    check("companion added to party", any(m.get("npc_code") == "elara_voss" for m in state.get("party") or []))
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status FROM quests WHERE code = 'concubine'").fetchone()
    quest_event = conn.execute(
        "SELECT event_type, summary FROM event_log WHERE event_type = 'quest_complete' LIMIT 1"
    ).fetchone()
    companion_event = conn.execute(
        "SELECT event_type FROM event_log WHERE event_type = 'companion_join' LIMIT 1"
    ).fetchone()
    conn.close()
    check("quest status completed in SQL", row and row[0] == "completed")
    check("chronicle quest_complete emitted", quest_event is not None and "Concubine" in quest_event[1])
    check("chronicle companion_join emitted", companion_event is not None)


def test_player_text_alone_does_not_complete_custom():
    db_path = make_db()
    state = base_state()
    quest_engine.create_quest(
        db_path,
        code="debt",
        title="Debt",
        objectives=[
            {
                "objective_type": "custom",
                "description_text": "Secure acknowledgment of the 70 sovereign debt",
                "condition": {"completion_signals": ["acknowledg", "sovereign", "debt", "seventy"]},
            }
        ],
    )
    player_text = "I secure acknowledgment of the seventy sovereign debt right now."
    gm_prose = "Lucas waits. Nothing official happens yet."
    resolution = TurnResolution(mode="narrative_only", intent="narrative_only")
    quest_engine.evaluate_quests_after_gm(
        db_path,
        state,
        resolution,
        player_text=player_text,
        gm_prose=gm_prose,
        scene_cast={"primary": [], "secondary": ["Lucas"]},
    )
    conn = sqlite3.connect(db_path)
    pending = conn.execute(
        "SELECT COUNT(*) FROM quest_objectives WHERE status = 'pending'"
    ).fetchone()[0]
    conn.close()
    check("objective stays pending without GM confirmation", pending == 1)


def test_talk_npc_via_scene_cast():
    db_path = make_db()
    state = base_state()
    quest_engine.create_quest(
        db_path,
        code="meet",
        title="Meet Elara",
        objectives=[
            {
                "objective_type": "talk_npc",
                "description_text": "Meet Elara Voss",
                "target_code": "elara_voss",
            }
        ],
    )
    gm_prose = "Elara Voss looks up as Lucas enters the concubine residence and offers a quiet greeting."
    resolution = TurnResolution(mode="narrative_only", intent="narrative_only")
    quest_engine.evaluate_quests_after_gm(
        db_path,
        state,
        resolution,
        player_text="I approach Elara.",
        gm_prose=gm_prose,
        scene_cast={"primary": ["Elara Voss"], "secondary": ["Lucas"]},
    )
    conn = sqlite3.connect(db_path)
    done = conn.execute(
        "SELECT COUNT(*) FROM quest_objectives WHERE status = 'complete'"
    ).fetchone()[0]
    conn.close()
    check("talk_npc completes from scene cast + GM prose", done == 1)


if __name__ == "__main__":
    test_custom_objective_completes_from_gm_prose()
    test_player_text_alone_does_not_complete_custom()
    test_talk_npc_via_scene_cast()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED")
        sys.exit(1)
    print("\nAll narrative quest tests passed.")
