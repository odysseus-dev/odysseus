"""Titan integration for Odysseus — Model Hub, serve removal, hub UI."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

log = logging.getLogger("titan")


def integrate_titan(app: "FastAPI") -> None:
    """Wire Titan Model Hub routes, middleware, and HTML injection into app."""
    from titan.patches.model_routes_dedupe import apply_model_routes_dedupe_patch

    apply_model_routes_dedupe_patch()
    from routes.model_hub_routes import setup_model_hub_routes
    from routes.scheduler_routes import setup_scheduler_routes
    from titan.hub_ui import register_hub_ui
    from titan.remove_serve import register_serve_middleware
    from titan.scheduler_ui import register_scheduler_ui

    app.include_router(setup_model_hub_routes())
    app.include_router(setup_scheduler_routes())
    register_serve_middleware(app)
    register_hub_ui(app)
    register_scheduler_ui(app)
    from titan.fugassa import register_fugassa

    register_fugassa(app)
    log.info("Titan integration registered")


async def titan_startup() -> None:
    """Post-startup cleanup and endpoint sync."""
    from routes.model_hub_routes import sync_hub_endpoints
    from titan.remove_serve import apply_removal

    apply_removal()
    try:
        sync_hub_endpoints()
        log.info("Titan Model Hub endpoints synced")
    except Exception as exc:
        log.warning("Endpoint sync on startup skipped: %s", exc)
    _resume_fugassa_pending_jobs()


def _resume_fugassa_pending_jobs() -> None:
    """After restart, pick up pending SD / scene-prompt jobs without waiting for HUD poll."""
    import os

    from titan.fugassa import campaign_job_runner
    from titan.fugassa.db import job_repository
    from titan.fugassa.paths import SAVES_DIR

    if not os.path.isdir(SAVES_DIR):
        return
    for save_id in sorted(os.listdir(SAVES_DIR)):
        db_path = os.path.join(SAVES_DIR, save_id, "game.db")
        if not os.path.isfile(db_path):
            continue
        try:
            if not job_repository.has_active_jobs(db_path, save_id):
                continue
            campaign_job_runner.reconcile_queued_asset_jobs(save_id, db_path)
            campaign_job_runner.ensure_worker_scheduled(save_id, db_path)
            log.info("Fugassa pipeline resumed for save %s", save_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Fugassa job resume skipped for %s: %s", save_id, exc)
