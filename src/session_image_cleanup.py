"""Cleanup helpers for images attached to chat sessions."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from src.constants import GENERATED_IMAGES_DIR
from src.path_confinement import confine

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
    try:
        return Path(confine(GENERATED_IMAGES_DIR, name, allow_root=False))
    except (ValueError, OSError):
        return None


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


def session_gallery_images(db, session_id: str):
    """Gallery images belonging to this chat, including legacy tool records."""
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
    from core.database import Session
    owner_row = db.query(Session.owner).filter(Session.id == session_id).first()
    if owner_row is not None:
        query = query.filter(GalleryImage.owner == owner_row[0])
    # A reference to an image belonging to another chat is not ownership.
    from sqlalchemy import or_
    return query.filter(or_(GalleryImage.session_id == session_id, GalleryImage.session_id.is_(None)))


def preserve_session_images(session_id: str, db) -> None:
    """Detach gallery images before removing the chat; keep files and albums."""
    _, GalleryImage, _ = _database_models()
    db.query(GalleryImage).filter(GalleryImage.session_id == session_id).update(
        {GalleryImage.session_id: None}, synchronize_session=False
    )


def cleanup_session_images(session_id: str, db=None) -> int:
    """Soft-delete Gallery rows and unlink generated files owned by a chat."""
    _, GalleryImage, SessionLocal = _database_models()
    owns_db = db is None
    db = db or SessionLocal()
    try:
        query = session_gallery_images(db, session_id)
        images = query.all()
        removed = 0
        for img in images:
            img.is_active = False
            if img.filename:
                path = _generated_image_path_for_cleanup(img.filename)
                if path and path.exists():
                    try:
                        path.unlink()
                    except Exception as exc:
                        logger.warning(
                            "Could not remove generated image %s for deleted session %s: %s",
                            img.filename,
                            session_id,
                            exc,
                        )
            removed += 1

        if owns_db and images:
            db.commit()
        return removed
    except Exception as exc:
        if owns_db:
            db.rollback()
        logger.warning("Failed to clean images for deleted session %s: %s", session_id, exc)
        return 0
    finally:
        if owns_db:
            db.close()
