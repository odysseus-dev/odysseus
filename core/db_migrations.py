"""
core/db_migrations.py — schema migrations + init_db.

Extracted from core/database.py (P8-T9). Pure relocation: NO migration
semantics changed (versioning is deferred to P9). Imports the engine / Base /
DATABASE_URL / helpers it needs from core.db_models (NOT core.database, to avoid
an import cycle). core/database.py re-exports init_db and the _migrate_* names
so existing importers keep resolving.
"""

import logging
import os
import sqlite3
from sqlalchemy import text

from core.db_models import Base, engine, logger, DATABASE_URL, utcnow_naive
from src.constants import AUTH_FILE, MEMORY_FILE, USER_PREFS_FILE, SETTINGS_FILE

def _migrate_add_last_message_at_column():
    """Add last_message_at to sessions + backfill from the latest message
    timestamp per session (fallback to last_accessed / created_at when a
    session has no messages). Idempotent: column-add is guarded, and the
    backfill only touches rows where last_message_at is still NULL so it
    won't clobber live values on later restarts."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(sessions)")
        columns = [row[1] for row in cursor.fetchall()]
        if "last_message_at" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN last_message_at DATETIME")
        # Backfill any NULL rows: newest message timestamp, else last_accessed,
        # else created_at. Only fills NULLs so it's safe on every startup.
        conn.execute(
            """
            UPDATE sessions
               SET last_message_at = COALESCE(
                   (SELECT MAX(timestamp) FROM chat_messages
                     WHERE chat_messages.session_id = sessions.id),
                   last_accessed,
                   created_at
               )
             WHERE last_message_at IS NULL
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_sessions_last_message_at "
            "ON sessions(archived, last_message_at)"
        )
        conn.commit()
        logging.getLogger(__name__).info("Migrated: added + backfilled 'last_message_at' on sessions")
    except Exception as e:
        logging.getLogger(__name__).warning(f"last_message_at migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_document_archived_column():
    """Add `archived` to documents (soft-archive flag). Guarded + idempotent."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(documents)")
        columns = [row[1] for row in cursor.fetchall()]
        if "archived" not in columns:
            conn.execute("ALTER TABLE documents ADD COLUMN archived BOOLEAN DEFAULT 0")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'archived' to documents")
    except Exception as e:
        logging.getLogger(__name__).warning(f"documents.archived migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _migrate_add_owner_column():
    """Add owner column to sessions table if it doesn't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(sessions)")
        columns = [row[1] for row in cursor.fetchall()]
        if "owner" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN owner TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_sessions_owner ON sessions(owner)")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'owner' column to sessions")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Migration check failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_model_endpoints():
    """Recreate model_endpoints table if schema changed (url->base_url)."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(model_endpoints)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "base_url" not in columns:
            conn.execute("DROP TABLE IF EXISTS model_endpoints")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: dropped old model_endpoints table (schema change)")
    except Exception as e:
        logging.getLogger(__name__).warning(f"model_endpoints migration check failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_hidden_models_column():
    """Add hidden_models column to model_endpoints if it doesn't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(model_endpoints)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "hidden_models" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN hidden_models TEXT")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'hidden_models' column to model_endpoints")
    except Exception as e:
        logging.getLogger(__name__).warning(f"hidden_models migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_model_endpoint_owner_column():
    """Add owner column to model_endpoints if it doesn't exist.

    Without this column, the per-user model picker query
    `(owner == user) | (owner IS NULL)` fails with `OperationalError:
    no such column: model_endpoints.owner`, leaving non-admin users
    with an empty picker even when `allowed_models` is unrestricted.
    Backfills NULL for existing rows (treated as shared by the filter).
    """
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(model_endpoints)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "owner" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN owner VARCHAR")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_model_endpoints_owner ON model_endpoints(owner)")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'owner' column + index to model_endpoints")
    except Exception as e:
        logging.getLogger(__name__).warning(f"model_endpoints.owner migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _migrate_add_provider_auth_id_column():
    """Add provider_auth_id column to model_endpoints if it doesn't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(model_endpoints)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "provider_auth_id" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN provider_auth_id VARCHAR")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_model_endpoints_provider_auth_id ON model_endpoints(provider_auth_id)")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'provider_auth_id' column + index to model_endpoints")
    except Exception as e:
        logging.getLogger(__name__).warning(f"model_endpoints.provider_auth_id migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _migrate_add_model_type_column():
    """Add model_type column to model_endpoints if it doesn't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(model_endpoints)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "model_type" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN model_type TEXT DEFAULT 'llm'")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'model_type' column to model_endpoints")
    except Exception as e:
        logging.getLogger(__name__).warning(f"model_type migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_model_endpoint_refresh_columns():
    """Add endpoint classification / refresh policy columns if missing."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(model_endpoints)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "endpoint_kind" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN endpoint_kind TEXT DEFAULT 'auto'")
        if columns and "model_refresh_mode" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN model_refresh_mode TEXT DEFAULT 'auto'")
        if columns and "model_refresh_interval" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN model_refresh_interval INTEGER")
        if columns and "model_refresh_timeout" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN model_refresh_timeout INTEGER")
        conn.commit()
    except Exception as e:
        logging.getLogger(__name__).warning(f"model_endpoints refresh-policy migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_task_run_model_column():
    """Add model column to task_runs if it doesn't exist (records which model ran)."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(task_runs)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "model" not in columns:
            conn.execute("ALTER TABLE task_runs ADD COLUMN model TEXT")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'model' column to task_runs")
    except Exception as e:
        logging.getLogger(__name__).warning(f"task_runs model migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_supports_tools_column():
    """Add supports_tools column to model_endpoints if it doesn't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(model_endpoints)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "supports_tools" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN supports_tools BOOLEAN")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'supports_tools' column to model_endpoints")
    except Exception as e:
        logging.getLogger(__name__).warning(f"supports_tools migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _migrate_add_cached_models_column():
    """Add cached_models column to model_endpoints if it doesn't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(model_endpoints)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "cached_models" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN cached_models TEXT")
            conn.commit()
    except Exception as e:
        logging.getLogger(__name__).warning(f"cached_models migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_pinned_models_column():
    """Add pinned_models column to model_endpoints if it doesn't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(model_endpoints)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "pinned_models" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN pinned_models TEXT")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'pinned_models' column to model_endpoints")
    except Exception as e:
        logging.getLogger(__name__).warning(f"pinned_models migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_notes_sort_order():
    """Add sort_order, image_url, repeat columns to notes if they don't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(notes)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "sort_order" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN sort_order INTEGER DEFAULT 0")
        if columns and "image_url" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN image_url TEXT")
        if columns and "repeat" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN repeat TEXT DEFAULT 'none'")
        if columns and "ai_classification" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN ai_classification TEXT")
        if columns and "ai_content_hash" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN ai_content_hash TEXT")
        if columns and "agent_session_id" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN agent_session_id TEXT")
        conn.commit()
    except Exception as e:
        logging.getLogger(__name__).warning(f"notes migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_mode_column():
    """Add mode column to sessions table if it doesn't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(sessions)")
        columns = [row[1] for row in cursor.fetchall()]
        if "mode" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN mode TEXT")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'mode' column to sessions")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Migration check for mode failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_folder_column():
    """Add folder column to sessions table if it doesn't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(sessions)")
        columns = [row[1] for row in cursor.fetchall()]
        if "folder" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN folder TEXT")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'folder' column to sessions")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Migration check for folder failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_token_columns():
    """Add cumulative token tracking columns to sessions table."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(sessions)")
        columns = [row[1] for row in cursor.fetchall()]
        if "total_input_tokens" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN total_input_tokens INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE sessions ADD COLUMN total_output_tokens INTEGER DEFAULT 0")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added token tracking columns to sessions")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Migration check for token columns failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_owner_to_table(table_name: str, index_name: str):
    """Generic helper: add owner TEXT column + index to a table if missing."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor.fetchall()]
        if "owner" not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN owner TEXT")
            conn.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name}(owner)")
            conn.commit()
            logging.getLogger(__name__).info(f"Migrated: added 'owner' column to {table_name}")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Migration owner column for {table_name} failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_add_multiuser_owner_columns():
    """Add owner column to memories, gallery_images, user_tools, comparisons."""
    _migrate_add_owner_to_table("memories", "ix_memories_owner")
    _migrate_add_owner_to_table("gallery_images", "ix_gallery_images_owner")
    _migrate_add_owner_to_table("user_tools", "ix_user_tools_owner")
    _migrate_add_owner_to_table("comparisons", "ix_comparisons_owner")
    _migrate_add_owner_to_table("api_tokens", "ix_api_tokens_owner")
    # documents derived ownership from their session join until this column
    # existed; the legacy-owner sweep (below) backfills it on the next boot.
    _migrate_add_owner_to_table("documents", "ix_documents_owner")


def _migrate_add_api_token_scopes_column():
    """Add API token scopes for existing installs.

    Existing tokens get the current only-supported scope (`chat`) so they keep
    working after the schema migration, but route checks no longer treat tokens
    as an unscoped bearer credential.
    """
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(api_tokens)").fetchall()]
        if columns and "scopes" not in columns:
            conn.execute("ALTER TABLE api_tokens ADD COLUMN scopes TEXT NOT NULL DEFAULT 'chat'")
            conn.execute("UPDATE api_tokens SET scopes = 'chat' WHERE scopes IS NULL OR scopes = ''")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added scopes column to api_tokens")
    except Exception as e:
        logging.getLogger(__name__).warning(f"api_tokens.scopes migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _migrate_assign_legacy_owner():
    """Assign all null-owner data to the first (admin) user.

    Runs at boot AND periodically (sweep_null_owners) so that data created
    while auth is disabled / middleware is bypassed via localhost doesn't
    sit in the DB as world-visible. Previously only swept 5 tables; the
    actual set of owner-bearing tables is much larger.
    """
    import sqlite3
    import json as _json

    # Find admin user from auth.json. The auth schema uses `is_admin: True`,
    # not `role: "admin"` — old code looked for the wrong field and silently
    # fell through to "first user" every time.
    auth_path = os.path.join(os.path.dirname(DATABASE_URL.replace("sqlite:///", "")), "auth.json")
    if not os.path.isabs(auth_path):
        auth_path = AUTH_FILE
    admin_user = None
    try:
        with open(auth_path, "r", encoding="utf-8") as f:
            auth_data = _json.load(f)
        users = auth_data.get("users", {})
        if users:
            for uname, udata in users.items():
                if udata.get("is_admin") is True:
                    admin_user = uname
                    break
            if not admin_user:
                admin_user = next(iter(users))
    except Exception:
        pass

    if not admin_user:
        return

    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return

    logger = logging.getLogger(__name__)
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        # Every table with an `owner` column. New tables added later will be
        # picked up automatically because we only UPDATE when the column
        # exists; the explicit list documents intent.
        tables = [
            "sessions", "memories", "gallery_images", "user_tools",
            "comparisons", "documents", "signatures", "notes",
            "calendars", "calendar_events", "integrations",
            "scheduled_tasks", "task_runs", "crew_members",
            "gallery_albums", "gallery_people", "user_tool_data",
            "api_tokens", "webhooks",
        ]
        for table in tables:
            try:
                cursor = conn.execute(f"PRAGMA table_info({table})")
                columns = [row[1] for row in cursor.fetchall()]
                if "owner" in columns:
                    res = conn.execute(f"UPDATE {table} SET owner = ? WHERE owner IS NULL", (admin_user,))
                    if res.rowcount > 0:
                        logger.info(f"Assigned {res.rowcount} legacy rows in {table} to '{admin_user}'")
            except Exception as e:
                logger.warning(f"Legacy owner assignment for {table} failed: {e}")
        conn.commit()
    except Exception as e:
        logger.warning(f"Legacy owner migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Also migrate memory.json
    mem_path = MEMORY_FILE
    try:
        if os.path.exists(mem_path):
            with open(mem_path, "r", encoding="utf-8") as f:
                memories = _json.load(f)
            changed = False
            for m in memories:
                if not m.get("owner"):
                    m["owner"] = admin_user
                    changed = True
            if changed:
                with open(mem_path, "w", encoding="utf-8") as f:
                    _json.dump(memories, f, ensure_ascii=False, indent=2)
                logger.info(f"Assigned {sum(1 for _ in memories)} legacy memories in memory.json to '{admin_user}'")
    except Exception as e:
        logger.warning(f"memory.json legacy migration failed: {e}")

    # Also migrate user_prefs.json to per-user format
    prefs_path = USER_PREFS_FILE
    try:
        if os.path.exists(prefs_path):
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = _json.load(f)
            if "_users" not in prefs and prefs:
                # Flat format → nest under admin user
                new_prefs = {"_users": {admin_user: prefs}}
                with open(prefs_path, "w", encoding="utf-8") as f:
                    _json.dump(new_prefs, f, indent=2)
                logger.info(f"Migrated user_prefs.json to per-user format under '{admin_user}'")
    except Exception as e:
        logger.warning(f"user_prefs.json migration failed: {e}")


def _migrate_backfill_document_owner_from_session():
    """Backfill documents.owner from the owner of the linked chat session.

    Must run AFTER the owner column is added and BEFORE the blanket
    legacy-owner sweep, so session-linked docs get their *true* owner
    while only genuinely orphaned (sessionless) docs fall through to the
    admin assignment. Idempotent — only touches NULL-owner rows."""
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(documents)"))]
            if "owner" not in cols:
                return
            res = conn.execute(text(
                "UPDATE documents SET owner = ("
                "  SELECT s.owner FROM sessions s WHERE s.id = documents.session_id"
                ") WHERE owner IS NULL AND session_id IS NOT NULL "
                "AND EXISTS (SELECT 1 FROM sessions s WHERE s.id = documents.session_id "
                "            AND s.owner IS NOT NULL)"
            ))
            conn.commit()
            if res.rowcount:
                logging.getLogger(__name__).info(
                    f"Backfilled owner on {res.rowcount} session-linked documents")
    except Exception as e:
        logging.getLogger(__name__).warning(f"document owner backfill: {e}")


def _migrate_add_tidy_verdict():
    """Add tidy_verdict column to documents table if missing."""
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(documents)"))]
            if "tidy_verdict" not in cols:
                conn.execute(text("ALTER TABLE documents ADD COLUMN tidy_verdict VARCHAR"))
                conn.commit()
                logging.getLogger(__name__).info("Added tidy_verdict column to documents")
    except Exception as e:
        logging.getLogger(__name__).warning(f"tidy_verdict migration: {e}")


def _migrate_add_doc_source_email_cols():
    """Add source-email provenance columns to documents (for the Sign-and-Reply flow)."""
    cols_to_add = {
        "source_email_uid":        "VARCHAR",
        "source_email_folder":     "VARCHAR",
        "source_email_account_id": "VARCHAR",
        "source_email_message_id": "VARCHAR",
    }
    try:
        with engine.connect() as conn:
            existing = {r[1] for r in conn.execute(text("PRAGMA table_info(documents)"))}
            for col, spec in cols_to_add.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE documents ADD COLUMN {col} {spec}"))
                    logging.getLogger(__name__).info(f"Added {col} column to documents")
            # Index for lookup-by-message-id (the "find existing draft" path)
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_documents_source_email_message_id "
                "ON documents (source_email_message_id)"
            ))
            conn.commit()
    except Exception as e:
        logging.getLogger(__name__).warning(f"doc source-email migration: {e}")

def _migrate_add_task_automation_columns():
    """Add automation columns to scheduled_tasks table if missing."""
    new_cols = {
        "task_type": "VARCHAR DEFAULT 'llm'",
        "action": "VARCHAR",
        "trigger_type": "VARCHAR DEFAULT 'schedule'",
        "trigger_event": "VARCHAR",
        "trigger_count": "INTEGER",
        "trigger_counter": "INTEGER DEFAULT 0",
    }
    try:
        with engine.connect() as conn:
            cols_info = list(conn.execute(text("PRAGMA table_info(scheduled_tasks)")))
            col_names = [r[1] for r in cols_info]
            for col_name, col_def in new_cols.items():
                if col_name not in col_names:
                    conn.execute(text(f"ALTER TABLE scheduled_tasks ADD COLUMN {col_name} {col_def}"))

            # Check if prompt/schedule/scheduled_time are still NOT NULL — need table rebuild
            notnull_map = {r[1]: r[3] for r in cols_info}
            needs_rebuild = (
                notnull_map.get("prompt", 0) == 1 or
                notnull_map.get("schedule", 0) == 1 or
                notnull_map.get("scheduled_time", 0) == 1
            )
            if needs_rebuild:
                logging.getLogger(__name__).info("Rebuilding scheduled_tasks to make prompt/schedule/scheduled_time nullable")
                # DI-P8-01: wrap the RENAME -> CREATE -> INSERT -> DROP rebuild in a
                # single explicit (nested/savepoint) transaction so it is all-or-nothing.
                # A mid-rebuild failure used to leave _old_scheduled_tasks renamed away
                # with no/partial scheduled_tasks; now any failure rolls back the whole
                # rebuild, leaving the original scheduled_tasks intact (the RENAME is
                # reverted). begin_nested() is used because the ADD COLUMN loop above has
                # already autobegun the connection's transaction; the savepoint brackets
                # the rebuild while the outer conn.commit() still commits the ADD COLUMN
                # work.
                with conn.begin_nested():
                    conn.execute(text("ALTER TABLE scheduled_tasks RENAME TO _old_scheduled_tasks"))
                    conn.execute(text("""
                        CREATE TABLE scheduled_tasks (
                            id VARCHAR PRIMARY KEY,
                            owner VARCHAR,
                            name VARCHAR NOT NULL,
                            prompt TEXT,
                            schedule VARCHAR,
                            scheduled_time VARCHAR,
                            scheduled_day INTEGER,
                            scheduled_date DATETIME,
                            next_run DATETIME,
                            last_run DATETIME,
                            status VARCHAR,
                            output_target VARCHAR,
                            session_id VARCHAR,
                            model VARCHAR,
                            endpoint_url VARCHAR,
                            run_count INTEGER,
                            created_at DATETIME NOT NULL,
                            updated_at DATETIME NOT NULL,
                            task_type VARCHAR DEFAULT 'llm',
                            action VARCHAR,
                            trigger_type VARCHAR DEFAULT 'schedule',
                            trigger_event VARCHAR,
                            trigger_count INTEGER,
                            trigger_counter INTEGER DEFAULT 0
                        )
                    """))
                    conn.execute(text("""
                        INSERT INTO scheduled_tasks
                        SELECT id, owner, name, prompt, schedule, scheduled_time,
                               scheduled_day, scheduled_date, next_run, last_run,
                               status, output_target, session_id, model, endpoint_url,
                               run_count, created_at, updated_at,
                               task_type, action, trigger_type, trigger_event,
                               trigger_count, trigger_counter
                        FROM _old_scheduled_tasks
                    """))
                    conn.execute(text("DROP TABLE _old_scheduled_tasks"))

            conn.commit()
            logging.getLogger(__name__).info("Task automation columns migration complete")
    except Exception as e:
        logging.getLogger(__name__).warning(f"task automation migration: {e}")

def _migrate_add_oauth_config():
    """Add oauth_config column to mcp_servers table if missing."""
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(mcp_servers)"))]
            if "oauth_config" not in cols:
                conn.execute(text("ALTER TABLE mcp_servers ADD COLUMN oauth_config TEXT"))
                conn.commit()
                logging.getLogger(__name__).info("Added oauth_config column to mcp_servers")
    except Exception as e:
        logging.getLogger(__name__).warning(f"oauth_config migration: {e}")

def _migrate_add_disabled_tools():
    """Add disabled_tools column to mcp_servers table if missing."""
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(mcp_servers)"))]
            if "disabled_tools" not in cols:
                conn.execute(text("ALTER TABLE mcp_servers ADD COLUMN disabled_tools TEXT"))
                conn.commit()
                logging.getLogger(__name__).info("Added disabled_tools column to mcp_servers")
    except Exception as e:
        logging.getLogger(__name__).warning(f"disabled_tools migration: {e}")

def _migrate_add_mcp_oauth_tokens_column():
    """Add oauth_tokens column to mcp_servers table if missing.

    The model declares this column as EncryptedText, but the SQL type is plain
    TEXT on purpose: EncryptedText is a SQLAlchemy TypeDecorator that encrypts at
    the Python layer and stores the ciphertext as TEXT, so the DB column type is
    TEXT. This matches the existing encrypted columns (see _migrate_encrypt_*)."""
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(mcp_servers)"))]
            if "oauth_tokens" not in cols:
                conn.execute(text("ALTER TABLE mcp_servers ADD COLUMN oauth_tokens TEXT"))
                conn.commit()
                logging.getLogger(__name__).info("Added oauth_tokens column to mcp_servers")
    except Exception as e:
        logging.getLogger(__name__).warning(f"oauth_tokens migration: {e}")

def _migrate_add_task_v2_columns():
    """Add cron_expression, then_task_id, webhook_token to scheduled_tasks."""
    new_cols = {
        "cron_expression": "VARCHAR",
        "then_task_id": "VARCHAR",
        "webhook_token": "VARCHAR",
    }
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(scheduled_tasks)"))]
            for col_name, col_def in new_cols.items():
                if col_name not in cols:
                    conn.execute(text(f"ALTER TABLE scheduled_tasks ADD COLUMN {col_name} {col_def}"))
            if "webhook_token" not in cols:
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_scheduled_tasks_webhook ON scheduled_tasks(webhook_token)"))
            conn.commit()
            logging.getLogger(__name__).info("Task v2 columns migration complete")
    except Exception as e:
        logging.getLogger(__name__).warning(f"task v2 migration: {e}")

def _migrate_drop_ping_notes_tasks():
    """One-time cleanup: ping_notes and ping_events used to be seeded as
    user-facing tasks. They're now pure background scanners inside the
    scheduler (no LLM, don't belong in the Tasks UI). Remove existing rows
    + their runs for both. (tidy_sessions/documents/research stay as tasks.)"""
    targets = ("ping_notes", "ping_events")
    try:
        with engine.connect() as conn:
            for action in targets:
                conn.execute(text(
                    "DELETE FROM task_runs WHERE task_id IN "
                    "(SELECT id FROM scheduled_tasks WHERE action=:a)"
                ), {"a": action})
                r = conn.execute(text("DELETE FROM scheduled_tasks WHERE action=:a"), {"a": action})
                if r.rowcount:
                    logging.getLogger(__name__).info(f"Dropped {r.rowcount} {action} task row(s)")
            conn.commit()
    except Exception as e:
        logging.getLogger(__name__).debug(f"drop_ping_notes_tasks: {e}")


def _migrate_add_notifications_enabled():
    """Per-task notification on/off toggle (default ON)."""
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(scheduled_tasks)"))]
            if "notifications_enabled" not in cols:
                conn.execute(text("ALTER TABLE scheduled_tasks ADD COLUMN notifications_enabled BOOLEAN DEFAULT 1"))
                conn.commit()
                logging.getLogger(__name__).info("Added notifications_enabled column to scheduled_tasks")
    except Exception as e:
        logging.getLogger(__name__).warning(f"notifications_enabled migration: {e}")


def _migrate_add_crew_member_id():
    """Add crew_member_id column to sessions and scheduled_tasks tables if missing."""
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(sessions)"))]
            if "crew_member_id" not in cols:
                conn.execute(text("ALTER TABLE sessions ADD COLUMN crew_member_id TEXT"))
                conn.commit()
                logging.getLogger(__name__).info("Added crew_member_id column to sessions")
            cols2 = [r[1] for r in conn.execute(text("PRAGMA table_info(scheduled_tasks)"))]
            if "crew_member_id" not in cols2:
                conn.execute(text("ALTER TABLE scheduled_tasks ADD COLUMN crew_member_id TEXT"))
                conn.commit()
                logging.getLogger(__name__).info("Added crew_member_id column to scheduled_tasks")
    except Exception as e:
        logging.getLogger(__name__).warning(f"crew_member_id migration: {e}")

def _migrate_add_assistant_columns():
    """Add is_default_assistant + timezone columns to crew_members for the personal-assistant feature."""
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(crew_members)"))]
            if "is_default_assistant" not in cols:
                conn.execute(text("ALTER TABLE crew_members ADD COLUMN is_default_assistant BOOLEAN DEFAULT 0"))
                conn.commit()
                logging.getLogger(__name__).info("Added is_default_assistant column to crew_members")
            if "timezone" not in cols:
                conn.execute(text("ALTER TABLE crew_members ADD COLUMN timezone TEXT"))
                conn.commit()
                logging.getLogger(__name__).info("Added timezone column to crew_members")
    except Exception as e:
        logging.getLogger(__name__).warning(f"assistant columns migration: {e}")









def _migrate_seed_email_account():
    """If email_accounts is empty and settings.json has legacy flat imap_host/smtp_host
    keys, create a single default account from them so nothing breaks for users who
    upgraded. Safe to run repeatedly — it short-circuits once any row exists."""
    try:
        with engine.connect() as conn:
            tables = [r[0] for r in conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='email_accounts'"
            ))]
            if "email_accounts" not in tables:
                return
            existing = conn.execute(text("SELECT COUNT(*) FROM email_accounts")).scalar() or 0
            if existing > 0:
                return

        import json as _json
        import uuid as _uuid
        from pathlib import Path
        settings_file = Path(SETTINGS_FILE)
        if not settings_file.exists():
            return
        try:
            s = _json.loads(settings_file.read_text(encoding="utf-8"))
        except Exception:
            return

        imap_host = (s.get("imap_host") or "").strip()
        smtp_host = (s.get("smtp_host") or "").strip()
        if not imap_host and not smtp_host:
            return  # nothing to migrate

        now = utcnow_naive()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO email_accounts
                  (id, owner, name, is_default, enabled,
                   imap_host, imap_port, imap_user, imap_password, imap_starttls,
                   smtp_host, smtp_port, smtp_user, smtp_password,
                   from_address, created_at, updated_at)
                VALUES
                  (:id, :owner, :name, :is_default, :enabled,
                   :imap_host, :imap_port, :imap_user, :imap_password, :imap_starttls,
                   :smtp_host, :smtp_port, :smtp_user, :smtp_password,
                   :from_address, :created_at, :updated_at)
            """), {
                "id": _uuid.uuid4().hex,
                "owner": None,
                "name": "Default",
                "is_default": True,
                "enabled": True,
                "imap_host": imap_host,
                "imap_port": int(s.get("imap_port") or 993),
                "imap_user": s.get("imap_user") or "",
                "imap_password": s.get("imap_password") or "",
                "imap_starttls": bool(s.get("imap_starttls", True)),
                "smtp_host": smtp_host,
                "smtp_port": int(s.get("smtp_port") or 465),
                "smtp_user": s.get("smtp_user") or "",
                "smtp_password": s.get("smtp_password") or "",
                "from_address": s.get("email_from") or "",
                "created_at": now,
                "updated_at": now,
            })
            logging.getLogger(__name__).info("Seeded email_accounts 'Default' from settings.json")
    except Exception as e:
        logging.getLogger(__name__).warning(f"seed email account migration: {e}")


# WARNING: Foreign-key enforcement is enabled globally for all SQLite connections.
# Any future migrations or schema changes that temporarily violate foreign-key
# constraints will fail. To perform such operations, foreign_keys must be
# temporarily disabled around the migration workflow.
def init_db():
    """
    Initialize the database by creating all tables.
    Should be called when starting the application.
    """
    _migrate_model_endpoints()
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        logger.critical(f"init_db: Base.metadata.create_all failed: {e}")
        raise
    # Post-create migrations now run through the versioned, fail-loud runner
    # (MIG-P9-02). The runner's MIGRATIONS registry mirrors the exact ordering that
    # used to be enumerated here; each _migrate_* stays individually idempotent, the
    # runner records applied versions in schema_migrations and re-raises on failure.
    # Deferred import avoids an import-order cycle (db_migrations is imported very
    # early via core.database; db_migration_runner imports from db_migrations).
    from core.db_migration_runner import run_migrations
    run_migrations()


def _migrate_backfill_task_folders():
    """Backfill folder='Tasks' on pre-existing task/research sessions.

    Sessions created by the task scheduler (LLM tasks, action tasks, research
    runs) now set folder='Tasks' at creation time.  This migration tags any
    older sessions that predate that assignment.  Idempotent — only touches
    rows where folder is NULL or empty and the title matches known prefixes.
    """
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(sessions)"))]
            if "folder" not in cols:
                return
            res = conn.execute(text(
                "UPDATE sessions SET folder = 'Tasks' "
                "WHERE (folder IS NULL OR folder = '') "
                "AND (name LIKE '[Task] %' OR name LIKE '[Research] %')"
            ))
            conn.commit()
            if res.rowcount:
                logging.getLogger(__name__).info(
                    f"Backfilled folder='Tasks' on {res.rowcount} task/research sessions")
    except Exception as e:
        logging.getLogger(__name__).warning(f"task folder backfill: {e}")


def _migrate_chat_messages_fts():
    """Create and backfill the session transcript FTS index for SQLite."""
    if not DATABASE_URL.startswith("sqlite"):
        return

    db_path = DATABASE_URL.replace("sqlite:///", "")
    if db_path == ":memory:":
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp._odysseus_fts5_probe USING fts5(content)")
            conn.execute("DROP TABLE IF EXISTS temp._odysseus_fts5_probe")
        except Exception as e:
            logging.getLogger(__name__).warning(f"chat_messages FTS migration skipped; FTS5 unavailable: {e}")
            return

        conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
                content,
                message_id UNINDEXED,
                session_id UNINDEXED,
                role UNINDEXED
            );

            CREATE TRIGGER IF NOT EXISTS chat_messages_fts_ai
            AFTER INSERT ON chat_messages BEGIN
                INSERT INTO chat_messages_fts(content, message_id, session_id, role)
                VALUES (COALESCE(new.content, ''), new.id, new.session_id, new.role);
            END;

            CREATE TRIGGER IF NOT EXISTS chat_messages_fts_ad
            AFTER DELETE ON chat_messages BEGIN
                DELETE FROM chat_messages_fts WHERE message_id = old.id;
            END;

            CREATE TRIGGER IF NOT EXISTS chat_messages_fts_au
            AFTER UPDATE ON chat_messages BEGIN
                DELETE FROM chat_messages_fts WHERE message_id = old.id;
                INSERT INTO chat_messages_fts(content, message_id, session_id, role)
                VALUES (COALESCE(new.content, ''), new.id, new.session_id, new.role);
            END;
            """
        )
        conn.execute(
            """
            INSERT INTO chat_messages_fts(content, message_id, session_id, role)
            SELECT COALESCE(cm.content, ''), cm.id, cm.session_id, cm.role
            FROM chat_messages cm
            WHERE NOT EXISTS (
                SELECT 1 FROM chat_messages_fts fts
                WHERE fts.message_id = cm.id
            )
            """
        )
        conn.commit()
    except Exception as e:
        logging.getLogger(__name__).warning(f"chat_messages FTS migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _migrate_add_email_smtp_security():
    """Add explicit SMTP security mode for Proton Bridge/custom local SMTP."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(email_accounts)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "smtp_security" not in columns:
            conn.execute("ALTER TABLE email_accounts ADD COLUMN smtp_security TEXT DEFAULT 'ssl'")
            conn.execute(
                "UPDATE email_accounts SET smtp_security = CASE "
                "WHEN COALESCE(smtp_port, 465) = 587 THEN 'starttls' "
                "WHEN COALESCE(smtp_port, 465) = 465 THEN 'ssl' "
                "ELSE 'ssl' END "
                "WHERE smtp_security IS NULL OR smtp_security = ''"
            )
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added smtp_security column to email_accounts")
    except Exception as e:
        logging.getLogger(__name__).warning(f"smtp_security migration skipped: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _migrate_encrypt_endpoint_keys():
    """Encrypt any plaintext provider API keys in model_endpoints. Idempotent;
    raw SQL so the EncryptedText decorator isn't applied twice."""
    try:
        from src.secret_storage import encrypt, is_encrypted
    except Exception as e:
        logger.warning(f"secret_storage import failed; skipping endpoint-key migration: {e}")
        return
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, api_key FROM model_endpoints")).fetchall()
            migrated = 0
            for rid, key in rows:
                if key and not is_encrypted(key):
                    conn.execute(text("UPDATE model_endpoints SET api_key = :k WHERE id = :id"),
                                 {"k": encrypt(key), "id": rid})
                    migrated += 1
            if migrated:
                conn.commit()
                logger.info(f"Encrypted plaintext API key on {migrated} endpoint row(s)")
    except Exception as e:
        logger.warning(f"Endpoint-key encryption migration skipped: {e}")


def _migrate_encrypt_signatures():
    """Encrypt any plaintext signature images still in the signatures table.
    Idempotent — rows already prefixed with `enc:` are skipped. Uses raw SQL
    so the EncryptedText type decorator isn't applied twice."""
    try:
        from src.secret_storage import encrypt, is_encrypted
    except Exception as e:
        logger.warning(f"secret_storage import failed; skipping signature migration: {e}")
        return
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, data_png, svg FROM signatures"
            )).fetchall()
            migrated = 0
            for rid, data_png, svg in rows:
                updates = {}
                if data_png and not is_encrypted(data_png):
                    updates["data_png"] = encrypt(data_png)
                if svg and not is_encrypted(svg):
                    updates["svg"] = encrypt(svg)
                if updates:
                    sets = ", ".join(f"{k} = :{k}" for k in updates)
                    conn.execute(text(f"UPDATE signatures SET {sets} WHERE id = :id"), {**updates, "id": rid})
                    migrated += 1
            if migrated:
                conn.commit()
                logger.info(f"Encrypted plaintext signature(s) on {migrated} row(s)")
    except Exception as e:
        logger.warning(f"Signature encryption migration skipped: {e}")


def _migrate_encrypt_email_passwords():
    """Encrypt any plaintext IMAP/SMTP passwords still in the email_accounts
    table. Idempotent — rows already prefixed with `enc:` are skipped.
    Safe to run on every startup."""
    try:
        from src.secret_storage import encrypt, is_encrypted
    except Exception as e:
        logger.warning(f"secret_storage import failed; skipping password migration: {e}")
        return
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, imap_password, smtp_password FROM email_accounts"
            )).fetchall()
            migrated = 0
            for row in rows:
                rid, imap_pw, smtp_pw = row
                updates = {}
                if imap_pw and not is_encrypted(imap_pw):
                    updates["imap_password"] = encrypt(imap_pw)
                if smtp_pw and not is_encrypted(smtp_pw):
                    updates["smtp_password"] = encrypt(smtp_pw)
                if updates:
                    sets = ", ".join(f"{k} = :{k}" for k in updates)
                    params = {**updates, "id": rid}
                    conn.execute(text(f"UPDATE email_accounts SET {sets} WHERE id = :id"), params)
                    migrated += 1
            if migrated:
                conn.commit()
                logger.info(f"Encrypted plaintext passwords on {migrated} email account row(s)")
    except Exception as e:
        logger.warning(f"Password migration failed (will retry next start): {e}")


def _migrate_add_calendar_is_utc():
    """Add is_utc column to calendar_events so imported events can preserve
    their original UTC timestamps (Z-suffix on the wire) without touching
    legacy naive-local rows."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(calendar_events)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "is_utc" not in columns:
            conn.execute("ALTER TABLE calendar_events ADD COLUMN is_utc BOOLEAN DEFAULT 0 NOT NULL")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'is_utc' column to calendar_events")
    except Exception as e:
        logging.getLogger(__name__).warning(f"is_utc migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _migrate_add_calendar_origin():
    """Add `origin` to calendar_events so the CalDAV sync can tell server-pulled
    rows (prunable when they vanish upstream) from locally-created ones (agent /
    email triage / failed write-back), which must never be pruned. Idempotent."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(calendar_events)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "origin" not in columns:
            conn.execute("ALTER TABLE calendar_events ADD COLUMN origin TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_calendar_events_origin ON calendar_events(origin)")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'origin' column to calendar_events")
    except Exception as e:
        logging.getLogger(__name__).warning(f"calendar_events.origin migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _migrate_add_calendar_account_id():
    """Add `account_id` to calendars so each CalDAV-backed calendar knows which
    credential set (from caldav_accounts in user prefs) owns it. Idempotent."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(calendars)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "account_id" not in columns:
            conn.execute("ALTER TABLE calendars ADD COLUMN account_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_calendars_account_id ON calendars(account_id)")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'account_id' column to calendars")
    except Exception as e:
        logging.getLogger(__name__).warning(f"calendars.account_id migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _migrate_add_calendar_metadata():
    """Add importance/event_type/last_pinged columns to calendar_events table."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(calendar_events)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "importance" not in columns:
            conn.execute("ALTER TABLE calendar_events ADD COLUMN importance TEXT DEFAULT 'normal'")
        if columns and "event_type" not in columns:
            conn.execute("ALTER TABLE calendar_events ADD COLUMN event_type TEXT")
        if columns and "last_pinged" not in columns:
            conn.execute("ALTER TABLE calendar_events ADD COLUMN last_pinged DATETIME")
        conn.commit()
    except Exception as e:
        logging.getLogger(__name__).warning(f"calendar_events migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
