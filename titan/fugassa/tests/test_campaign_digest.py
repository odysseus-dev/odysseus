"""ADR §7 / §11.1 — campaign digest condensation and engine appendix."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile

from titan.fugassa import campaign_chronicle, campaign_digest
from titan.fugassa.db import sqlite_store


def _init_db() -> str:
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "game.db")
    sqlite_store.init_game_db(db_path, "DigestTest", theme="fantasy")
    return db_path


def _seed_turn_history(conn: sqlite3.Connection, count: int) -> None:
    for i in range(1, count + 1):
        conn.execute(
            """
            INSERT INTO turn_history (turn_number, player_text, ai_text, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (i, f"player action {i}", f"gm response {i}"),
        )
    conn.commit()


def test_maybe_condense_noop_below_trigger():
    db_path = _init_db()
    conn = sqlite3.connect(db_path)
    try:
        _seed_turn_history(conn, campaign_digest.CONDENSE_TRIGGER_PAIRS - 1)
    finally:
        conn.close()

    result = asyncio.run(campaign_digest.maybe_condense(db_path, llm_enabled=False))
    assert result.get("condensed") is False

    digest = campaign_digest.get_digest(db_path)
    assert not str(digest.get("digest_text") or "").strip()


def test_maybe_condense_appends_engine_appendix_with_quest_complete():
    db_path = _init_db()
    campaign_chronicle.record_events(
        db_path,
        [
            campaign_chronicle.make_quest_complete_event(
                quest_code="paid",
                quest_title="Paid in Full",
                hero_name="Lucas",
                turn_id=1,
                location_id=None,
                scale="standard",
                chain_code=None,
                chain_position=None,
            )
        ],
    )
    conn = sqlite3.connect(db_path)
    try:
        _seed_turn_history(conn, campaign_digest.CONDENSE_TRIGGER_PAIRS)
    finally:
        conn.close()

    result = asyncio.run(campaign_digest.maybe_condense(db_path, llm_enabled=False))
    assert result.get("condensed") is True

    digest = campaign_digest.get_digest(db_path)
    text = str(digest.get("digest_text") or "")
    assert "ENGINE APPENDIX" in text
    assert "Paid in Full" in text

    conn = sqlite3.connect(db_path)
    try:
        active = conn.execute("SELECT COUNT(*) FROM turn_history WHERE is_active = 1").fetchone()[0]
        assert int(active) == campaign_digest.ROLLING_WINDOW_PAIRS
    finally:
        conn.close()


def test_build_digest_block_includes_prefix():
    db_path = _init_db()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO campaign_digest (id, digest_text) VALUES (1, '')")
        conn.execute(
            "UPDATE campaign_digest SET digest_text = ? WHERE id = 1",
            ("The party secured the harbor contract.",),
        )
        conn.commit()
    finally:
        conn.close()

    block = campaign_digest.build_digest_block(db_path)
    assert "CAMPAIGN DIGEST" in block
    assert "harbor contract" in block
