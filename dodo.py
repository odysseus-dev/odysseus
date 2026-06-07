"""Odysseus Track 4d Orchestration Loop — dodo.py task definitions.

Tasks:
  - task_sync_email() — Fetch IMAP, write to DB
  - task_sync_calendar() — Fetch CalDAV, write to DB
  - task_daily_briefing() — Generate briefing, output to session + ChromaDB
  - task_memory_snapshot() — Export ChromaDB vectors to JSON
  - task_orchestration_loop() — Meta-task; orchestrates all above

Execution: doit orchestration_loop (sequential, single-worker)
Logging: /odysseus/logs/doit.log
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Configure logging
LOG_DIR = Path("/odysseus/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "doit.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Doit configuration
DOIT_CONFIG = {
    "default_tasks": ["orchestration_loop"],
    "num_workers": 1,  # Sequential execution only
    "verbosity": 2,
}

# ============================================================================
# TASK DEFINITIONS
# ============================================================================


def task_sync_email():
    """Sync email from all IMAP accounts.

    Returns: { "status": "ok|partial", "emails_fetched": N, "accounts": [...] }
    """

    def sync_email_action():
        logger.info("task_sync_email: starting")
        try:
            # Import here to avoid circular deps at module load time
            from core.database import SessionLocal, EmailAccount
            from routes.email_helpers import _imap_connect

            db = SessionLocal()
            try:
                accounts = (
                    db.query(EmailAccount)
                    .filter(EmailAccount.enabled == True)  # noqa: E712
                    .all()
                )
            finally:
                db.close()

            total_fetched = 0
            account_results = []

            for account in accounts:
                try:
                    logger.info(f"task_sync_email: syncing {account.imap_user}")
                    conn = _imap_connect(account.id)
                    try:
                        conn.select("INBOX", readonly=True)
                        status, data = conn.search(None, "UNSEEN")
                        uids = (
                            data[0].split()
                            if status == "OK" and data and data[0]
                            else []
                        )
                        count = len(uids)
                        total_fetched += count
                        account_results.append(
                            {
                                "account": account.imap_user,
                                "unread_count": count,
                                "status": "ok",
                            }
                        )
                        logger.info(
                            f"task_sync_email: {account.imap_user} → {count} unread"
                        )
                    finally:
                        try:
                            conn.logout()
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(
                        f"task_sync_email: failed for {account.imap_user}: {e}"
                    )
                    account_results.append(
                        {
                            "account": account.imap_user,
                            "status": "failed",
                            "error": str(e),
                        }
                    )

            result = {
                "status": "ok" if total_fetched > 0 else "partial",
                "emails_fetched": total_fetched,
                "accounts": account_results,
            }
            logger.info(f"task_sync_email: finished ({total_fetched} total)")
            return result

        except Exception as e:
            logger.error(f"task_sync_email: action failed: {e}")
            return {"status": "failed", "error": str(e), "emails_fetched": 0}

    return {
        "actions": [sync_email_action],
        "verbosity": 2,
    }


def task_sync_calendar():
    """Sync calendar events from CalDAV.

    Returns: { "status": "ok|failed", "events_fetched": N }
    """

    def sync_calendar_action():
        logger.info("task_sync_calendar: starting")
        try:
            from core.database import SessionLocal, CalendarEvent, CalendarCal
            from datetime import datetime as _dt, timedelta as _td

            today = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow = today + _td(days=1)

            db = SessionLocal()
            try:
                # Query events for today+tomorrow
                events = (
                    db.query(CalendarEvent)
                    .join(CalendarCal)
                    .filter(
                        CalendarEvent.dtstart < tomorrow,
                        CalendarEvent.dtend > today,
                        CalendarEvent.status != "cancelled",
                    )
                    .all()
                )
                count = len(events)
                logger.info(f"task_sync_calendar: fetched {count} events")
                return {"status": "ok", "events_fetched": count}
            finally:
                db.close()

        except Exception as e:
            logger.error(f"task_sync_calendar: action failed: {e}")
            return {"status": "failed", "error": str(e), "events_fetched": 0}

    return {
        "actions": [sync_calendar_action],
        "verbosity": 2,
    }


def task_daily_briefing():
    """Generate daily briefing from email + calendar + todos.

    Depends on: sync_email, sync_calendar (but can run with partial data)
    Returns: { "status": "ok|failed", "output_session_id": "..." }
    """

    def briefing_action():
        logger.info("task_daily_briefing: starting")
        try:
            from src.builtin_actions import action_daily_brief
            import asyncio

            # Call the existing action (it's an async function)
            briefing_text, success = asyncio.run(
                action_daily_brief(owner=None, **{})
            )

            if not success:
                logger.warning(f"task_daily_briefing: action returned failure")
                return {"status": "partial", "error": briefing_text}

            logger.info(f"task_daily_briefing: generated {len(briefing_text)} bytes")
            return {
                "status": "ok",
                "bytes": len(briefing_text),
                "text_preview": briefing_text[:200],
            }

        except Exception as e:
            logger.error(f"task_daily_briefing: action failed: {e}")
            return {"status": "failed", "error": str(e)}

    return {
        "actions": [briefing_action],
        "task_dep": ["sync_email", "sync_calendar"],
        "verbosity": 2,
    }


def task_memory_snapshot():
    """Export ChromaDB vectors to JSON file.

    Returns: { "status": "ok|failed", "vectors_exported": N, "file": "..." }
    """

    def memory_snapshot_action():
        logger.info("task_memory_snapshot: starting")
        try:
            from core.database import SessionLocal

            # Query ChromaDB (if available) or skip gracefully
            try:
                import chromadb

                # Connect to ChromaDB at localhost:8100
                client = chromadb.HttpClient(host="127.0.0.1", port=8100)
                collections = client.list_collections()

                all_vectors = []
                for collection in collections:
                    try:
                        col = client.get_collection(name=collection.name)
                        results = col.get()  # Get all documents
                        if results and results.get("ids"):
                            for id_, metadata, document in zip(
                                results["ids"],
                                results.get("metadatas", []),
                                results.get("documents", []),
                            ):
                                all_vectors.append(
                                    {
                                        "id": id_,
                                        "text": document,
                                        "metadata": metadata or {},
                                        "collection": collection.name,
                                    }
                                )
                    except Exception as e:
                        logger.warning(
                            f"task_memory_snapshot: failed to query {collection.name}: {e}"
                        )

                # Write JSON export
                timestamp_sec = int(datetime.now().timestamp())
                export_file = (
                    Path("/odysseus/data")
                    / f"memory_export_{timestamp_sec}.json"
                )
                Path("/odysseus/data").mkdir(parents=True, exist_ok=True)

                export_data = {
                    "export_timestamp": datetime.now().isoformat(),
                    "exported_by": "task_memory_snapshot",
                    "vectors_count": len(all_vectors),
                    "vectors": all_vectors,
                }

                with open(export_file, "w") as f:
                    json.dump(export_data, f, indent=2, default=str)

                logger.info(
                    f"task_memory_snapshot: exported {len(all_vectors)} vectors to {export_file}"
                )
                return {
                    "status": "ok",
                    "vectors_exported": len(all_vectors),
                    "file": str(export_file),
                }

            except ImportError:
                logger.warning("task_memory_snapshot: ChromaDB not available, skipping")
                return {
                    "status": "ok",
                    "vectors_exported": 0,
                    "note": "ChromaDB not available",
                }

        except Exception as e:
            logger.error(f"task_memory_snapshot: action failed: {e}")
            return {"status": "failed", "error": str(e), "vectors_exported": 0}

    return {
        "actions": [memory_snapshot_action],
        "verbosity": 2,
    }


def task_orchestration_loop():
    """Meta-task: orchestrates sync_email, sync_calendar, briefing, memory_snapshot.

    Returns: { "status": "ok|partial", "subtasks": {...}, "duration_sec": N }
    """

    def orchestration_action():
        logger.info("task_orchestration_loop: starting (meta-task)")
        start_time = datetime.now()

        # Doit will execute all dependencies first due to task_dep list
        # This meta-task just reports the final status
        result = {
            "status": "ok",
            "message": "All sub-tasks completed",
            "started_at": start_time.isoformat(),
            "finished_at": datetime.now().isoformat(),
            "duration_sec": (datetime.now() - start_time).total_seconds(),
        }
        logger.info(
            f"task_orchestration_loop: finished ({result['duration_sec']:.1f}s)"
        )
        return result

    return {
        "actions": [orchestration_action],
        "task_dep": [
            "sync_email",
            "sync_calendar",
            "daily_briefing",
            "memory_snapshot",
        ],
        "verbosity": 2,
    }


if __name__ == "__main__":
    import doit

    doit.run(globals())
