"""Cleanup helpers for images attached to chat sessions."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from src.constants import GENERATED_IMAGES_DIR

logger = logging.getLogger(__name__)


def _database_models():
    """Import DB models at call time so early import stubs cannot stick here."""
    from core.database import ChatMessage, GalleryImage, SessionLocal

    return ChatMessage, GalleryImage, SessionLocal


def _generated_image_path_for_cleanup(filename: str) -> Path | None:
    if not isinstance(filename, str) or not filename:
        return None
    name = Path(filename).name
    if name != filename or name in {".", ".."}:
        return None
    root = Path(GENERATED_IMAGES_DIR).resolve()
    path = (root / name).resolve()
    try:
        if os.path.commonpath([str(root), str(path)]) != str(root):
            return None
    except Exception:
        return None
    return path


def _image_filename_from_url(url: str) -> str:
    if not isinstance(url, str) or not url:
        return ""
    match = re.search(r"/api/generated-image/([^?#/]+)", url)
    return match.group(1) if match else ""


def session_image_refs(db, session_id: str) -> tuple[set[str], set[str]]:
    """Return gallery image ids and generated-image filenames referenced by a chat."""
    ChatMessage, GalleryImage, _ = _database_models()
    image_ids: set[str] = set()
    filenames: set[str] = set()

    rows = db.query(GalleryImage).filter(GalleryImage.session_id == session_id).all()
    for img in rows:
        if img.id:
            image_ids.add(str(img.id))
        if img.filename:
            filenames.add(str(img.filename))

    messages = db.query(ChatMessage.meta_data).filter(ChatMessage.session_id == session_id).all()
    for row in messages:
        raw = getattr(row, "meta_data", None)
        if not raw:
            continue
        try:
            meta = json.loads(raw)
        except Exception:
            continue
        events = meta.get("tool_events") if isinstance(meta, dict) else None
        if not isinstance(events, list):
            continue
        for ev in events:
            if not isinstance(ev, dict):
                continue
            image_id = ev.get("image_id")
            if image_id:
                image_ids.add(str(image_id))
            filename = _image_filename_from_url(ev.get("image_url") or ev.get("url") or "")
            if filename:
                filenames.add(filename)

    return image_ids, filenames


def prepare_session_image_cleanup(session_id: str, db) -> tuple[int, list[Path]]:
    """Stage gallery soft-deletes and return files to unlink after commit.

    Database work deliberately raises to the caller so a larger session-delete
    transaction can roll back as one unit. Filesystem deletion is deferred
    because it cannot be rolled back with the database transaction.
    """
    _, GalleryImage, _ = _database_models()
    image_ids, filenames = session_image_refs(db, session_id)
    query = db.query(GalleryImage).filter(GalleryImage.session_id == session_id)
    if image_ids or filenames:
        from sqlalchemy import or_

        clauses = [GalleryImage.session_id == session_id]
        if image_ids:
            clauses.append(GalleryImage.id.in_(list(image_ids)))
        if filenames:
            clauses.append(GalleryImage.filename.in_(list(filenames)))
        query = db.query(GalleryImage).filter(or_(*clauses))

    images = query.all()
    paths: list[Path] = []
    for img in images:
        img.is_active = False
        if img.filename:
            path = _generated_image_path_for_cleanup(img.filename)
            if path:
                paths.append(path)
    return len(images), paths


def unlink_session_image_paths(paths: list[Path], session_id: str) -> None:
    """Best-effort removal of generated files after a successful DB commit."""
    for path in dict.fromkeys(paths):
        if not path.exists():
            continue
        try:
            path.unlink()
        except Exception as exc:
            logger.warning(
                "Could not remove generated image %s for deleted session %s: %s",
                path.name,
                session_id,
                exc,
            )


def cleanup_session_images(session_id: str, db=None) -> int:
    """Soft-delete Gallery rows and unlink generated files owned by a chat."""
    _, _, SessionLocal = _database_models()
    owns_db = db is None
    db = db or SessionLocal()
    try:
        removed, paths = prepare_session_image_cleanup(session_id, db)
        if owns_db and removed:
            db.commit()
        # Retain the standalone helper's historical behavior for callers that
        # supply a transaction. Atomic session deletion uses the prepare/unlink
        # pair directly so it can defer this irreversible step until commit.
        unlink_session_image_paths(paths, session_id)
        return removed
    except Exception as exc:
        if owns_db:
            db.rollback()
        logger.warning("Failed to clean images for deleted session %s: %s", session_id, exc)
        return 0
    finally:
        if owns_db:
            db.close()
