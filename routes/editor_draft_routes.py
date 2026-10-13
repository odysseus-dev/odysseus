"""Editor draft routes — persisted in-progress gallery-editor sessions.

The gallery editor (image canvas) lets users layer edits on top of a
photo (or a blank canvas). Persisting those layered sessions to the
server makes them survive cache clears and roams across devices —
unlike the legacy per-image localStorage drafts.

Each draft carries:
  - id           — opaque uuid (the client never sees gallery-image ids
                    as draft ids, so blank-canvas drafts work too)
  - source_image_id (nullable) — back-pointer for "this draft started as
                    an edit of GalleryImage X"
  - payload      — full JSON snapshot (layers as base64 PNG dataURLs,
                    offsets, opacities, etc.) the editor knows how to
                    rehydrate
  - thumbnail    — small data URL for the landing-list grid
"""

import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel

from core.database import EditorDraft, SessionLocal
from src.auth_helpers import get_current_user
from src.upload_limits import EDITOR_DRAFT_MAX_BYTES

logger = logging.getLogger(__name__)


class DraftCreate(BaseModel):
    name: Optional[str] = None
    source_image_id: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    payload: Dict[str, Any]
    thumbnail: Optional[str] = None


class DraftUpdate(BaseModel):
    name: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None
    thumbnail: Optional[str] = None


def _owns(d: EditorDraft, user: Optional[str]) -> bool:
    if user is None:
        return True
    return (d.owner or None) == user


def _summary(d: EditorDraft) -> Dict[str, Any]:
    """List-view representation — omits the bulky payload."""
    return {
        "id": d.id,
        "name": d.name or "Untitled",
        "source_image_id": d.source_image_id,
        "width": d.width,
        "height": d.height,
        "thumbnail": d.thumbnail,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _load_payload(raw: Optional[str]) -> Dict[str, Any]:
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _draft_too_large() -> HTTPException:
    return HTTPException(
        413,
        f"Editor draft exceeds the {EDITOR_DRAFT_MAX_BYTES // (1024 * 1024)} MB safety limit",
    )


def reject_oversized_draft_body(request: Request) -> None:
    """Refuse an oversized draft on declared ``Content-Length``, before the body is read or parsed.

    ``_dump_payload`` still owns the authoritative byte count, but it only runs
    after the request has been parsed and re-serialised. Declaring a body past the
    ceiling is enough to reject it early and cheaply. Requests with absent,
    malformed, or chunked transfer encoding still hit the authoritative byte count
    check further down.
    """
    raw_length = request.headers.get("content-length")
    if not raw_length:
        return
    try:
        declared = int(raw_length)
    except (TypeError, ValueError):
        return
    if declared > EDITOR_DRAFT_MAX_BYTES:
        raise _draft_too_large()


class EditorDraftRoute(APIRoute):
    """Route class that validates declared Content-Length before request body parsing."""

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            if request.method in ("POST", "PUT", "PATCH"):
                reject_oversized_draft_body(request)
            return await original_route_handler(request)

        return custom_route_handler


def _dump_payload(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload or {}, separators=(",", ":"))
    if len(raw.encode("utf-8")) > EDITOR_DRAFT_MAX_BYTES:
        raise _draft_too_large()
    return raw


def setup_editor_draft_routes() -> APIRouter:
    router = APIRouter(tags=["editor-drafts"], route_class=EditorDraftRoute)

    @router.get("/api/editor-drafts")
    async def list_drafts(request: Request) -> Dict[str, List[Dict[str, Any]]]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(EditorDraft).filter(EditorDraft.is_active == True)
            if user is not None:
                q = q.filter(EditorDraft.owner == user)
            rows = q.order_by(EditorDraft.updated_at.desc()).limit(200).all()
            return {"drafts": [_summary(d) for d in rows]}
        finally:
            db.close()

    @router.get("/api/editor-drafts/{draft_id}")
    async def get_draft(request: Request, draft_id: str) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            d = db.query(EditorDraft).filter(
                EditorDraft.id == draft_id, EditorDraft.is_active == True
            ).first()
            if not d or not _owns(d, user):
                raise HTTPException(404, "Draft not found")
            return {
                **_summary(d),
                "payload": _load_payload(d.payload),
            }
        finally:
            db.close()

    @router.post("/api/editor-drafts")
    async def create_draft(
        request: Request,
        body: DraftCreate,
    ) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            d = EditorDraft(
                id=str(uuid.uuid4()),
                owner=user,
                name=(body.name or "Untitled")[:200],
                source_image_id=body.source_image_id,
                width=body.width,
                height=body.height,
                payload=_dump_payload(body.payload),
                thumbnail=body.thumbnail,
            )
            db.add(d)
            db.commit()
            db.refresh(d)
            return _summary(d)
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.warning(f"editor-draft create failed: {e}")
            raise HTTPException(500, "Could not save draft")
        finally:
            db.close()

    @router.put("/api/editor-drafts/{draft_id}")
    async def update_draft(
        request: Request,
        draft_id: str,
        body: DraftUpdate,
    ) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            d = db.query(EditorDraft).filter(
                EditorDraft.id == draft_id, EditorDraft.is_active == True
            ).first()
            if not d or not _owns(d, user):
                raise HTTPException(404, "Draft not found")
            if body.name is not None:
                d.name = body.name[:200]
            if body.width is not None:
                d.width = body.width
            if body.height is not None:
                d.height = body.height
            if body.payload is not None:
                d.payload = _dump_payload(body.payload)
            if body.thumbnail is not None:
                d.thumbnail = body.thumbnail
            db.commit()
            db.refresh(d)
            return _summary(d)
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.warning(f"editor-draft update failed: {e}")
            raise HTTPException(500, "Could not update draft")
        finally:
            db.close()

    @router.delete("/api/editor-drafts/{draft_id}")
    async def delete_draft(request: Request, draft_id: str) -> Dict[str, str]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            d = db.query(EditorDraft).filter(EditorDraft.id == draft_id).first()
            if not d or not _owns(d, user):
                raise HTTPException(404, "Draft not found")
            d.is_active = False
            db.commit()
            return {"status": "deleted", "id": draft_id}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(500, str(e))
        finally:
            db.close()

    return router
