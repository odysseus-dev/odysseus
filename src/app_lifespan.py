"""
src/app_lifespan.py

ASGI lifespan (startup/shutdown) extracted from app.py (P8-T13).

build_lifespan(app, deps) returns the @asynccontextmanager that app.py assigns
to app.router.lifespan_context, exactly replicating the original wiring. The
startup/shutdown coroutine bodies are preserved verbatim; the singletons they
touch (webhook_manager, mcp_manager, task_scheduler, skills_manager,
model_discovery, auth_manager, upload_cleanup_func) are passed in via `deps`.
The fire-and-forget startup task handle upload_cleanup_task — previously a
module global mutated by _startup_event and read by _shutdown_event — is a
closure variable shared between the two inner coroutines.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from core.constants import AUTH_FILE

logger = logging.getLogger("app")


def build_lifespan(app, deps):
    webhook_manager = deps.webhook_manager
    mcp_manager = deps.mcp_manager
    task_scheduler = deps.task_scheduler
    skills_manager = deps.skills_manager
    model_discovery = deps.model_discovery
    auth_manager = deps.auth_manager
    upload_cleanup_func = deps.upload_cleanup_func
    upload_cleanup_task = None

    @asynccontextmanager
    async def _lifespan(app):
        """Modern lifespan context manager replacing deprecated @app.on_event."""
        # ── STARTUP ──
        await _startup_event()
        yield
        # ── SHUTDOWN ──
        await _shutdown_event()


    async def _startup_event():
        nonlocal upload_cleanup_task
        logger.info("Application starting up...")
        try:
            from core.database import init_db as _init_db
            _init_db()
        except Exception as _e:
            import sys as _sys
            print(
                "FATAL: database init failed — check your DB config, permissions, and"
                " that the data directory is writable. See logs above for details.",
                file=_sys.stderr,
            )
            logger.critical("Database initialisation failed — aborting startup: %s", _e)
            raise
        webhook_manager.set_loop(asyncio.get_running_loop())
        # Wipe any leftover incognito sessions from previous process — they're
        # ephemeral by design and must not survive a restart.
        try:
            from core.database import SessionLocal as _SL, Session as _DbSess, ChatMessage as _DbMsg
            _db = _SL()
            try:
                _ghosts = _db.query(_DbSess).filter(_DbSess.name.in_(("Nobody", "Incognito"))).all()
                for _g in _ghosts:
                    _db.query(_DbMsg).filter(_DbMsg.session_id == _g.id).delete()
                    _db.delete(_g)
                if _ghosts:
                    _db.commit()
                    logger.info(f"Purged {len(_ghosts)} leftover incognito session(s)")
            finally:
                _db.close()
        except Exception as e:
            logger.debug(f"Incognito purge skipped: {e}")
        # Strong refs to fire-and-forget startup tasks. Without this, Python may
        # GC tasks created with `asyncio.create_task(...)` before they finish.
        _startup_tasks: list[asyncio.Task] = getattr(app.state, "_startup_tasks", [])
        app.state._startup_tasks = _startup_tasks
        if upload_cleanup_func:
            upload_cleanup_task = asyncio.create_task(upload_cleanup_func())
        # Always-on monitor that auto-continues the agent when a background bash
        # job (#!bg) finishes — re-invokes the turn with the job output.
        try:
            from src.bg_monitor import start_bg_monitor
            _startup_tasks.append(start_bg_monitor())
        except Exception as _e:
            logger.warning("Failed to start background-job monitor: %s", _e)
        # MCP servers can be slow or blocked by local tooling. Connect them after
        # the web server is accepting traffic instead of delaying the whole UI.
        async def _startup_mcp_connections():
            try:
                from src.builtin_mcp import register_builtin_servers
                await register_builtin_servers(mcp_manager)
            except BaseException as e:
                logger.warning(f"Built-in MCP registration failed (non-critical): {type(e).__name__}: {e}")
            try:
                await asyncio.wait_for(mcp_manager.connect_all_enabled(), timeout=20)
            except asyncio.TimeoutError:
                logger.warning("User MCP startup timed out (non-critical)")
            except BaseException as e:
                logger.warning(f"MCP startup failed (non-critical): {type(e).__name__}: {e}")

        _startup_tasks.append(asyncio.create_task(_startup_mcp_connections()))

        # Pre-warm the RAG tool index off the request path. Loading the local
        # embedding model + opening ChromaDB + indexing the built-in tools is a
        # one-time ~1-3s cost that otherwise lands on the user's FIRST message
        # (showing up as a big `tool_selection` time). Doing it here makes the
        # first turn as fast as subsequent ones (warm embed ≈ a few ms).
        async def _warmup_tool_index():
            try:
                from src.tool_index import get_tool_index
                idx = await asyncio.to_thread(get_tool_index)
                if idx:
                    await asyncio.to_thread(idx.get_tools_for_query, "warmup", 8)
                    logger.info("[startup] Tool index pre-warmed")
            except Exception as e:
                logger.warning(f"Tool index warmup failed (non-critical): {type(e).__name__}: {e}")

        _startup_tasks.append(asyncio.create_task(_warmup_tool_index()))
        # Warmup: ping all known LLM endpoints to prime connections
        async def _warmup_endpoints():
            try:
                import httpx
                from urllib.parse import urlparse as _urlparse

                def _redact_url(raw: str) -> str:
                    """Return scheme+host only, dropping path/query/credentials."""
                    try:
                        p = _urlparse(raw)
                        return f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")
                    except Exception:
                        return "<url>"

                # model_discovery has no get_endpoints(); that call raised
                # AttributeError every run and silently disabled warmup/keepalive.
                # Resolve the /models probe URLs via the real discovery API, off
                # the event loop since discovery does a blocking port scan.
                urls = (
                    await asyncio.to_thread(model_discovery.warmup_ping_urls)
                    if model_discovery else []
                )
                for url in urls:
                    if url:
                        try:
                            async with httpx.AsyncClient(timeout=5.0) as client:
                                await client.get(url)
                            logger.info("Warmup ping OK: %s", _redact_url(url))
                        except Exception as e:
                            logger.debug("Warmup ping failed for endpoint: %s", type(e).__name__)
            except Exception as e:
                logger.debug("Warmup ping skipped: %s", type(e).__name__)

        _startup_tasks.append(asyncio.create_task(_warmup_endpoints()))

        # Keep-alive: ping endpoints every 60 seconds to prevent cold starts
        async def _keepalive_loop():
            while True:
                try:
                    await asyncio.sleep(60)
                    await _warmup_endpoints()
                except Exception as e:
                    logger.warning(f"Keepalive loop error: {e}")
                    await asyncio.sleep(300)  # Back off on error

        _startup_tasks.append(asyncio.create_task(_keepalive_loop()))

        async def _ensure_default_tasks():
            # Create/reconcile default automation tasks + personal assistant for every user.
            owners = set()
            try:
                import json as _json
                auth_path = AUTH_FILE
                with open(auth_path, encoding="utf-8") as f:
                    users = _json.load(f).get("users", {})
                owners.update(users.keys())
            except Exception as e:
                logger.debug(f"Default task auth-owner scan: {e}")

            # Also reconcile owners already present in scheduled_tasks. This cleans
            # up stale/demo/deleted-user built-ins that are no longer in auth.json;
            # otherwise their old scheduled rows can keep firing forever.
            try:
                from core.database import SessionLocal, ScheduledTask
                from src.task_scheduler import HOUSEKEEPING_DEFAULTS
                builtin_names = []
                for defs in HOUSEKEEPING_DEFAULTS.values():
                    builtin_names.append(defs["name"])
                    builtin_names.extend(defs.get("legacy_names") or [])
                db_seed = SessionLocal()
                try:
                    rows = db_seed.query(ScheduledTask.owner).filter(
                        (ScheduledTask.action.in_(list(HOUSEKEEPING_DEFAULTS.keys())))
                        | (ScheduledTask.name.in_(builtin_names))
                    ).distinct().all()
                    owners.update(row[0] for row in rows if row[0])
                finally:
                    db_seed.close()
            except Exception as e:
                logger.debug(f"Default task existing-owner scan: {e}")

            try:
                for uname in sorted(owners):
                    try:
                        await task_scheduler.ensure_defaults(uname)
                    except Exception as e:
                        logger.debug(f"ensure_defaults({uname}): {e}")
            except Exception as e:
                logger.debug(f"Default tasks: {e}")

        # Reconcile built-in tasks before the runner starts. Otherwise legacy
        # scheduled built-ins can fire once before being converted to event tasks.
        await _ensure_default_tasks()

        # Disk-backed skills are not covered by the DB legacy-owner sweep. Repair
        # ownerless or deleted/test-owner SKILL.md files so strict owner filtering
        # does not make an existing library look empty after auth/account changes.
        try:
            import json as _json
            auth_path = AUTH_FILE
            with open(auth_path, encoding="utf-8") as f:
                users = _json.load(f).get("users", {})
            primary_owner = None
            for uname, udata in users.items():
                if udata.get("is_admin") is True:
                    primary_owner = uname
                    break
            if not primary_owner and users:
                primary_owner = next(iter(users))
            if primary_owner:
                changed = skills_manager.backfill_owner(primary_owner, set(users.keys()))
                if changed:
                    logger.info("Assigned %s legacy skill file(s) to %s", changed, primary_owner)
        except Exception as e:
            logger.debug(f"Skill owner backfill skipped: {e}")

        # Start scheduled task runner — skip when running under a cron-driven
        # deployment where an external worker drives task firing. Mirrors
        # `ODYSSEUS_INPROCESS_POLLERS` from the email pollers.
        _tasks_inprocess = os.environ.get("ODYSSEUS_INPROCESS_TASKS", "1").strip().lower()
        if _tasks_inprocess not in ("0", "false", "no", "off", ""):
            await task_scheduler.start()
        else:
            logger.info(
                "In-process task scheduler disabled (ODYSSEUS_INPROCESS_TASKS=0); "
                "drive task firing externally (e.g. cron)."
            )
        # Periodic null-owner sweep — re-runs the legacy-owner assignment hourly
        # so any data created while auth was disabled / localhost-bypassed gets
        # claimed by the admin instead of staying world-visible (M19).
        async def _null_owner_sweep_loop():
            while True:
                try:
                    await asyncio.sleep(3600)
                    from core.database import _migrate_assign_legacy_owner
                    await asyncio.to_thread(_migrate_assign_legacy_owner)
                except Exception as e:
                    logger.debug(f"Null-owner sweep skipped: {e}")
                    await asyncio.sleep(3600)

        _startup_tasks.append(asyncio.create_task(_null_owner_sweep_loop()))

        # Nightly skill audit — at ~02:00 local, test + judge a batch of the
        # least-recently-checked skills, auto-fixing/escalating weak ones (never
        # deletes). Rotates through the library so each night covers different
        # skills. Gated by the `skill_audit_nightly` setting (default on); hour via
        # `skill_audit_hour` (default 2), batch size via `skill_audit_batch` (8).
        async def _skill_audit_nightly_loop():
            from datetime import timedelta
            while True:
                try:
                    from src.settings import get_setting
                    hour = int(get_setting("skill_audit_hour", 2) or 2)
                except Exception:
                    hour = 2
                now = datetime.now()
                nxt = now.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
                if nxt <= now:
                    nxt += timedelta(days=1)
                await asyncio.sleep(max(60, (nxt - now).total_seconds()))
                try:
                    from src.settings import get_setting
                    if not get_setting("skill_audit_nightly", True):
                        continue
                    batch = int(get_setting("skill_audit_batch", 8) or 8)
                    from routes.skills_routes import run_scheduled_skill_audit
                    await run_scheduled_skill_audit(skills_manager, owner=None, max_skills=batch)
                except Exception as e:
                    logger.warning(f"Nightly skill audit failed: {e}")

        _startup_tasks.append(asyncio.create_task(_skill_audit_nightly_loop()))

        # Cookbook serve lifecycle — kills scheduler-launched serves whose
        # window-end has passed. Paired with the cookbook_serve builtin
        # action; both are no-ops unless a scheduled task actually launches
        # something with end_after_min set. Removing this line + the
        # cookbook_serve entry in BUILTIN_ACTIONS + src/cookbook_serve_lifecycle.py
        # removes the feature.
        from src.cookbook_serve_lifecycle import cookbook_serve_lifecycle_loop
        _startup_tasks.append(asyncio.create_task(cookbook_serve_lifecycle_loop()))

        # Periodic reset-token prune — expired in-memory tokens are tiny but
        # accumulate in long-running deployments. Run once at startup (removes
        # anything left from a prior process restart) then hourly. REL-P5-001.
        auth_manager.prune_reset_tokens()  # immediate prune on startup

        async def _reset_token_prune_loop():
            while True:
                try:
                    await asyncio.sleep(3600)
                    auth_manager.prune_reset_tokens()
                except Exception as e:
                    logger.debug(f"Reset-token prune skipped: {e}")

        _startup_tasks.append(asyncio.create_task(_reset_token_prune_loop()))

        logger.info("Application startup complete")

    async def _shutdown_event():
        logger.info("Application shutting down...")
        if upload_cleanup_task:
            upload_cleanup_task.cancel()
            try:
                await upload_cleanup_task
            except asyncio.CancelledError:
                pass
        # Stop task scheduler (no-op if it never started under the gate)
        try:
            await task_scheduler.stop()
        except Exception:
            pass
        # Close webhook manager
        try:
            await webhook_manager.close()
        except Exception as e:
            logger.warning(f"Webhook manager shutdown error: {e}")
        # Disconnect all MCP servers
        try:
            await mcp_manager.disconnect_all()
        except Exception as e:
            logger.warning(f"MCP shutdown error: {e}")
        logger.info("Application shutdown complete")


    return _lifespan
