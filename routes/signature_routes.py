"""Signature routes — CRUD for the user's saved visual signatures.

Signatures are reusable image stamps (drawn once, applied to many things):
PDF form fields, email composition, document insertion. Each signature is
stored as a base64 PNG so it can be embedded inline anywhere without a
separate fetch.
"""

import base64
import logging
import re
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, Signature
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


_DATA_URL_RE = re.compile(r"^data:image/png;base64,(?P<data>.+)$", re.IGNORECASE | re.DOTALL)
_ANY_IMAGE_DATA_URL_RE = re.compile(r"^data:image/[^;]+;base64,", re.IGNORECASE)
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAX_SIGNATURE_BYTES = 2 * 1024 * 1024
_MAX_SIGNATURE_B64 = ((_MAX_SIGNATURE_BYTES + 2) // 3) * 4
_MAX_SIGNATURE_DIMENSION = 4096


def _normalize_signature_png(raw: str) -> str:
    raw = (raw or "").strip()
    m = _DATA_URL_RE.match(raw)
    if m:
        b64 = m.group("data")
    elif _ANY_IMAGE_DATA_URL_RE.match(raw):
        raise HTTPException(400, "Signature data must be a PNG image")
    else:
        b64 = raw
    if len(b64) > _MAX_SIGNATURE_B64:
        raise HTTPException(400, "Signature PNG is too large")
    try:
        payload = base64.b64decode(b64, validate=True)
    except Exception:
        raise HTTPException(400, "Signature data must be base64-encoded PNG bytes")
    if not payload:
        raise HTTPException(400, "Signature PNG is empty")
    if len(payload) > _MAX_SIGNATURE_BYTES:
        raise HTTPException(400, "Signature PNG is too large")
    if not payload.startswith(_PNG_MAGIC):
        raise HTTPException(400, "Signature data must be a PNG image")
    return base64.b64encode(payload).decode("ascii")


def _signature_dimension(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int) or value < 1 or value > _MAX_SIGNATURE_DIMENSION:
        raise HTTPException(400, "Signature dimensions are invalid")
    return value


class SignatureCreate(BaseModel):
    name: Optional[str] = None
    data: str  # base64 PNG, with or without `data:image/png;base64,` prefix
    width: Optional[int] = None
    height: Optional[int] = None
    svg: Optional[str] = None


def _to_dict(s: Signature) -> Dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "data_url": f"data:image/png;base64,{s.data_png}",
        "width": s.width,
        "height": s.height,
        "created_at": (s.created_at.isoformat() + "Z") if s.created_at else None,
    }


def setup_signature_routes() -> APIRouter:
    router = APIRouter(tags=["signatures"])

    @router.get("/api/signatures")
    async def list_signatures(request: Request) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(Signature)
            if user is not None:
                # SECURITY: strict ownership — the previous OR predicate
                # returned every null-owner signature to every user.
                q = q.filter(Signature.owner == user)
            sigs = q.order_by(Signature.created_at.desc()).all()
            return {"signatures": [_to_dict(s) for s in sigs]}
        finally:
            db.close()

    @router.post("/api/signatures")
    async def create_signature(request: Request, req: SignatureCreate) -> Dict[str, Any]:
        user = get_current_user(request)
        b64 = _normalize_signature_png(req.data)
        width = _signature_dimension(req.width)
        height = _signature_dimension(req.height)

        sig = Signature(
            id=str(uuid.uuid4()),
            owner=user,
            name=(req.name or "Signature").strip() or "Signature",
            data_png=b64,
            width=width,
            height=height,
            svg=None,
        )
        db = SessionLocal()
        try:
            db.add(sig)
            db.commit()
            db.refresh(sig)
            return _to_dict(sig)
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to save signature: {e}")
            raise HTTPException(500, f"Failed to save signature: {e}")
        finally:
            db.close()

    @router.delete("/api/signatures/{sig_id}")
    async def delete_signature(sig_id: str, request: Request) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            sig = db.query(Signature).filter(Signature.id == sig_id).first()
            if not sig:
                raise HTTPException(404, "Signature not found")
            if user and sig.owner != user:
                raise HTTPException(403, "Not your signature")
            db.delete(sig)
            db.commit()
            return {"deleted": sig_id}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(500, f"Failed to delete signature: {e}")
        finally:
            db.close()

    return router
