"""migrate.py — auto-migrate OXS extension data schemas.

When an extension is enabled, its ``data_schema`` is applied: every
declared table is created if missing, every declared index is created
if missing, every declared unique constraint is created if missing.

This module is the single source of truth for that flow. Operators can
also call :func:`apply_for_extension` directly from the admin UI to
re-run schema application (e.g. after an author publishes a new version
with extra columns).
"""

from __future__ import annotations

import logging
from typing import Any

from .audit import audit_log
from .storage import OXSStorageError, PluginStorage

logger = logging.getLogger(__name__)


def apply_for_extension(
    extension_id: str,
    data_schema: dict,
    *,
    engine: Any = None,
    database_url: str | None = None,
) -> dict:
    """Apply ``data_schema`` to the extension's storage.

    Returns a summary dict with ``created`` (tables newly created),
    ``existing`` (tables already present), and ``errors`` (per-table
    failures).
    """
    summary: dict[str, Any] = {
        "created": [],
        "existing": [],
        "errors": [],
    }

    if not isinstance(data_schema, dict):
        summary["errors"].append("data_schema must be a dict")
        return summary

    storage = PluginStorage(
        extension_id,
        engine=engine,
        database_url=database_url,
    )

    # We snapshot the existing tables BEFORE applying so we can report
    # "created" vs "existing" accurately.
    try:
        existing_before = set(storage.list_own_tables())
    except Exception as e:
        existing_before = set()
        logger.warning(
            "OXS could not list tables for %s: %s", extension_id, e,
        )

    try:
        created = storage.apply_data_schema(data_schema)
    except OXSStorageError as e:
        # Coerce the storage error into a list of friendly messages
        summary["errors"].append("; ".join(e.errors) if hasattr(e, "errors") else str(e))
        audit_log(
            "extension.migrate.failed",
            extension_id=extension_id,
            actor="system",
            payload=summary,
        )
        return summary
    except Exception as e:
        summary["errors"].append(f"unexpected error: {e}")
        audit_log(
            "extension.migrate.failed",
            extension_id=extension_id,
            actor="system",
            payload=summary,
        )
        return summary

    for full_name in created:
        if full_name in existing_before:
            summary["existing"].append(full_name)
        else:
            summary["created"].append(full_name)

    audit_log(
        "extension.migrate.applied",
        extension_id=extension_id,
        actor="system",
        payload={
            "created": len(summary["created"]),
            "existing": len(summary["existing"]),
            "errors": len(summary["errors"]),
        },
    )
    return summary


def drop_all_for_extension(
    extension_id: str,
    *,
    engine: Any = None,
    database_url: str | None = None,
) -> list[str]:
    """Drop every prefixed table for ``extension_id``. Returns names dropped."""
    storage = PluginStorage(
        extension_id,
        engine=engine,
        database_url=database_url,
    )
    dropped = storage.drop_own_tables()
    if dropped:
        audit_log(
            "extension.migrate.dropped",
            extension_id=extension_id,
            actor="system",
            payload={"tables": dropped},
        )
    return dropped


__all__ = ["apply_for_extension", "drop_all_for_extension"]