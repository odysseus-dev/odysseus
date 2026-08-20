"""routes/memory/graph_routes.py — the brain view (system-state overview).

DIAGNOSTIC OVERVIEW, NOT recall.

This endpoint renders a living picture of the memory system when the user
opens the Brain view: where the persona layer and identity are forming, the
association graph, and how neurons are firing.

  GET /api/memory-brain/overview
    Returns a snapshot:
      stats         — entry counts, topics, audit chain
      associations  — the association graph (nodes + edges)
      neurons       — the warm neurons and their firing state

This is a pure view. It never mutates memory and never participates in recall.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Request
from src.auth_helpers import get_current_user

logger = logging.getLogger("odysseus_memory_brain")


def setup_brain_routes(store_db_path: str) -> APIRouter:
    """Set up brain view routes.

    Args:
        store_db_path: Path to the memory store database.

    Returns:
        APIRouter with brain endpoints.
    """
    router = APIRouter(prefix="/api/memory-brain", tags=["memory-brain"])

    def _get_db():
        import sqlite3
        db = sqlite3.connect(f"file:{store_db_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    @router.get("/overview")
    async def brain_overview(request: Request):
        """Return a snapshot of the memory platform state."""
        owner = get_current_user(request)
        try:
            db = _get_db()
            try:
                # Entry stats.
                active = db.execute(
                    "SELECT COUNT(*) FROM entries WHERE status='active'"
                ).fetchone()[0]
                by_topic = {}
                for r in db.execute(
                    "SELECT topic, COUNT(*) as n FROM entries "
                    "WHERE status='active' AND topic != '' "
                    "GROUP BY topic ORDER BY n DESC LIMIT 10"
                ).fetchall():
                    by_topic[r["topic"]] = r["n"]

                # Associations.
                associations = []
                for r in db.execute(
                    "SELECT src_id, dst_id, strength FROM associations "
                    "WHERE strength >= 0.1 ORDER BY strength DESC LIMIT 200"
                ).fetchall():
                    associations.append({
                        "source": r["src_id"],
                        "target": r["dst_id"],
                        "strength": r["strength"],
                    })

                # Warm neurons.
                neurons = []
                try:
                    for r in db.execute(
                        "SELECT id, slug, text, kind FROM entries "
                        "WHERE kind='neuron' AND status='active' "
                        "ORDER BY importance DESC LIMIT 50"
                    ).fetchall():
                        neurons.append({
                            "id": r["id"],
                            "slug": r["slug"],
                            "text": r["text"][:200],
                            "kind": r["kind"],
                        })
                except Exception:
                    pass

                return {
                    "active_entries": active,
                    "topics": by_topic,
                    "associations": associations,
                    "neurons": neurons,
                    "owner": owner,
                }
            finally:
                db.close()
        except Exception as e:
            logger.error("Brain overview failed: %s", e)
            return {"error": str(e)}

    @router.get("/pressure")
    async def brain_pressure():
        """Return consolidation pressure (how full the store is)."""
        try:
            from memory_platform.consolidate import store_pressure
            pressure = store_pressure(store_db_path)
            return {"pressure": round(pressure, 3)}
        except Exception as e:
            return {"pressure": 0, "error": str(e)}

    @router.post("/sleep")
    async def brain_sleep():
        """Trigger a sleep consolidation cycle."""
        try:
            from memory_platform.sleep_time import run_sleep_cycle
            result = run_sleep_cycle(hours=24)
            return result
        except Exception as e:
            return {"error": str(e)}

    return router
