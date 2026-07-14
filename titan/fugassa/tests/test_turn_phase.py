"""Turn phase gating — reading window vs interactive pipeline."""

from __future__ import annotations

import os
import tempfile

from titan.fugassa import game_session
from titan.fugassa.db import job_repository, sqlite_store


def test_resolve_turn_phase_reading_while_sd_jobs_run():
    with tempfile.TemporaryDirectory() as tmp:
        save_id = "TurnPhaseTest"
        save_dir = os.path.join(tmp, save_id)
        os.makedirs(save_dir, exist_ok=True)
        db_path = os.path.join(save_dir, "game.db")
        sqlite_store.init_game_db(db_path, save_id, theme="fantasy")
        job_repository.set_campaign_phase(db_path, "generating_assets")
        job_repository.insert_job(
            db_path,
            save_id=save_id,
            job_type="sd_generate",
            batch_id=job_repository.new_batch_id(save_id),
            payload={"asset_id": 1},
            priority=200,
        )
        assert game_session.resolve_turn_phase(db_path, save_id) == "reading"


def test_resolve_turn_phase_processing_during_interactive_turn():
    with tempfile.TemporaryDirectory() as tmp:
        save_id = "TurnPhaseProcessing"
        save_dir = os.path.join(tmp, save_id)
        os.makedirs(save_dir, exist_ok=True)
        db_path = os.path.join(save_dir, "game.db")
        sqlite_store.init_game_db(db_path, save_id, theme="fantasy")
        job_repository.insert_job(
            db_path,
            save_id=save_id,
            job_type="interactive_turn",
            batch_id=job_repository.new_batch_id(save_id),
            payload={"player_text": "look around"},
            priority=100,
        )
        assert game_session.resolve_turn_phase(db_path, save_id) == "processing"
