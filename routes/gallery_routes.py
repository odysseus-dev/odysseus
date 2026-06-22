"""Gallery routes — browsable library for photos and AI-generated images."""

import asyncio
import json
import os
import hashlib
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from core.database import SessionLocal, GalleryImage, GalleryAlbum, ModelEndpoint
from core.database import Session as DbSession
from src.auth_helpers import get_current_user, owner_filter, require_privilege, require_user
from src.upload_limits import (
    read_upload_limited,
    GALLERY_UPLOAD_MAX_BYTES,
    GALLERY_TRANSFORM_UPLOAD_MAX_BYTES,
)
from src.constants import GENERATED_IMAGES_DIR

from routes.gallery_helpers import (
    GalleryPatch, _extract_exif, _image_to_dict, _owner_filter, _human_size,
)

logger = logging.getLogger(__name__)

_INPAINT_PROGRESS: Dict[str, Dict[str, Any]] = {}
_INPAINT_PROGRESS_MAX_AGE_SECONDS = 8 * 60


def _normalize_inpaint_progress_id(value: Any) -> str:
    progress_id = str(value or "").strip()
    if not progress_id:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,80}", progress_id):
        return ""
    return progress_id


def _prune_inpaint_progress(now: float | None = None) -> None:
    now = now or time.time()
    stale = [
        progress_id for progress_id, record in _INPAINT_PROGRESS.items()
        if now - float(record.get("updated_at") or 0) > _INPAINT_PROGRESS_MAX_AGE_SECONDS
    ]
    for progress_id in stale:
        _INPAINT_PROGRESS.pop(progress_id, None)


def _push_inpaint_progress(
    progress_id: str,
    owner: str | None,
    phase: str,
    message: str = "",
    *,
    percent: int | None = None,
    done: bool = False,
    error: bool = False,
) -> None:
    progress_id = _normalize_inpaint_progress_id(progress_id)
    if not progress_id:
        return
    now = time.time()
    _prune_inpaint_progress(now)
    record = _INPAINT_PROGRESS.setdefault(
        progress_id,
        {"owner": owner or "", "events": [], "updated_at": now, "done": False},
    )
    if record.get("owner") and owner and record.get("owner") != owner:
        return
    if owner and not record.get("owner"):
        record["owner"] = owner
    event: Dict[str, Any] = {
        "phase": str(phase or "working"),
        "message": str(message or ""),
        "at": now,
    }
    if percent is not None:
        event["percent"] = max(0, min(100, int(percent)))
    if done:
        event["done"] = True
        record["done"] = True
    if error:
        event["error"] = True
    events = record.setdefault("events", [])
    events.append(event)
    del events[:-80]
    record["updated_at"] = now


def _current_user_is_admin(request: Request, user: str | None) -> bool:
    if not user:
        return False
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    is_admin = getattr(auth_mgr, "is_admin", None)
    if not callable(is_admin):
        return False
    try:
        return bool(is_admin(user))
    except Exception:
        return False


def _gallery_owner_matches(owner: str | None, user: str | None) -> bool:
    """True when this request may access a gallery row.

    In normal authenticated mode, rows are private to their owner. In
    auth-disabled / single-user mode, uploaded gallery rows are stamped with a
    null owner, so the local user must still be able to manage them.
    """
    if user:
        return owner == user
    return owner in (None, "")


def _sanitize_gallery_filename(filename: str) -> str:
    """Return a local filename safe to join under generated_images."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(str(filename or "")).name)[:128]
    if not safe_name or safe_name in {".", ".."}:
        safe_name = uuid.uuid4().hex[:12]
    return safe_name


GALLERY_IMAGE_DIR = Path(GENERATED_IMAGES_DIR)


def _gallery_image_path(filename: str) -> Path:
    """Resolve a stored gallery filename without leaving generated_images."""
    if not isinstance(filename, str):
        raise HTTPException(400, "Unsafe gallery filename")
    safe_name = _sanitize_gallery_filename(filename)
    original = str(filename or "")
    root = GALLERY_IMAGE_DIR.resolve()
    path = (GALLERY_IMAGE_DIR / safe_name).resolve()
    try:
        if os.path.commonpath([str(root), str(path)]) != str(root):
            raise ValueError
    except Exception:
        raise HTTPException(400, "Unsafe gallery filename")
    if safe_name != original:
        raise HTTPException(400, "Unsafe gallery filename")
    return path


def _normalize_image_endpoint_base(url: str) -> str:
    base = (url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def _visible_image_endpoint_query(db, owner: str | None):
    from src.auth_helpers import owner_filter
    q = db.query(ModelEndpoint).filter(
        ModelEndpoint.model_type == "image",
        ModelEndpoint.is_enabled == True,  # noqa: E712
    )
    return owner_filter(q, ModelEndpoint, owner)


def _first_visible_image_endpoint(db, owner: str | None):
    endpoints = _visible_image_endpoint_query(db, owner).all()
    if owner:
        for ep in endpoints:
            if getattr(ep, "owner", None) == owner:
                return ep
    return endpoints[0] if endpoints else None


def _visible_image_endpoint_for_base(db, base: str, owner: str | None):
    target = _normalize_image_endpoint_base(base)
    if not target:
        return None
    fallback = None
    for ep in _visible_image_endpoint_query(db, owner).all():
        if _normalize_image_endpoint_base(getattr(ep, "base_url", "")) == target:
            if owner and getattr(ep, "owner", None) == owner:
                return ep
            if fallback is None:
                fallback = ep
    return fallback


def _visible_image_endpoint_for_id(db, endpoint_id: str, owner: str | None):
    endpoint_id = str(endpoint_id or "").strip()
    if not endpoint_id:
        return None
    return _visible_image_endpoint_query(db, owner).filter(ModelEndpoint.id == endpoint_id).first()


def setup_gallery_routes() -> APIRouter:
    router = APIRouter(tags=["gallery"])

    # ---- POST /api/gallery/upload ----
    @router.post("/api/gallery/upload")
    async def gallery_upload(request: Request):
        """Upload an image file to the gallery with EXIF extraction and dedup."""
        import uuid
        from pathlib import Path

        form = await request.form()
        file = form.get("file")
        if not file or not hasattr(file, 'filename'):
            raise HTTPException(400, "No file provided")

        user = get_current_user(request)
        album_id = form.get("album_id") or None
        content = await read_upload_limited(file, GALLERY_UPLOAD_MAX_BYTES, "Gallery upload")

        # Duplicate detection via SHA-256
        file_hash = hashlib.sha256(content).hexdigest()
        db = SessionLocal()
        try:
            if album_id and user is not None:
                _get_or_404_album(db, album_id, user)

            # SECURITY: scope the dup-detect to THIS user — otherwise a
            # caller can probe whether someone else uploaded the same
            # file (the response leaks the existing row's id+filename).
            _dup_q = db.query(GalleryImage).filter(
                GalleryImage.file_hash == file_hash,
                GalleryImage.is_active == True,
            )
            if user:
                _dup_q = _dup_q.filter(GalleryImage.owner == user)
            existing = _dup_q.first()
            if existing:
                return {"ok": False, "duplicate": True, "filename": existing.filename,
                        "id": existing.id, "message": "Duplicate photo skipped"}

            img_dir = Path(GENERATED_IMAGES_DIR)
            img_dir.mkdir(parents=True, exist_ok=True)

            ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "png"
            VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "m4v"}
            IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
            if ext not in VIDEO_EXTS and ext not in IMAGE_EXTS:
                raise HTTPException(400, f"Unsupported file type: .{ext}")
            is_video = ext in VIDEO_EXTS
            filename = f"{uuid.uuid4().hex[:12]}.{ext}"
            img_path = img_dir / filename
            img_path.write_bytes(content)

            # Extract EXIF for images only — PIL can't parse video containers
            # and the failure path logs a noisy WARNING. We'll add ffprobe-based
            # video metadata extraction in a follow-up.
            exif = {} if is_video else _extract_exif(content)
            original_name = file.filename.rsplit(".", 1)[0] if "." in file.filename else file.filename

            img_id = str(uuid.uuid4())
            db.add(GalleryImage(
                id=img_id,
                filename=filename,
                prompt=original_name,
                model="imported",
                owner=user,
                file_hash=file_hash,
                file_size=len(content),
                width=exif.get("width"),
                height=exif.get("height"),
                taken_at=exif.get("taken_at"),
                camera_make=exif.get("camera_make"),
                camera_model=exif.get("camera_model"),
                gps_lat=exif.get("gps_lat"),
                gps_lng=exif.get("gps_lng"),
                album_id=album_id,
            ))
            db.commit()
            resp = {"ok": True, "filename": filename, "id": img_id}
            if exif.get("exif_error"):
                resp["exif_warning"] = exif["exif_error"]
            return resp
        finally:
            db.close()

    # ---- POST /api/gallery/{id}/replace ----
    @router.post("/api/gallery/{image_id}/replace")
    async def gallery_replace(request: Request, image_id: str):
        """Replace an existing gallery image file with a new one."""
        from pathlib import Path

        user = require_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not _gallery_owner_matches(img.owner, user):
                raise HTTPException(403, "Not your image")

            form = await request.form()
            file = form.get("image")
            if not file or not hasattr(file, 'read'):
                raise HTTPException(400, "No image provided")

            content = await read_upload_limited(file, GALLERY_UPLOAD_MAX_BYTES, "Gallery replacement")
            img_dir = Path(GENERATED_IMAGES_DIR)
            img_dir.mkdir(parents=True, exist_ok=True)
            img_path = img_dir / _sanitize_gallery_filename(img.filename)
            img_path.write_bytes(content)

            # Refresh dimensions in case the editor resized the canvas.
            # updated_at auto-bumps via TimestampMixin's onupdate hook.
            try:
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(content)) as new_im:
                    img.width = new_im.width
                    img.height = new_im.height
            except Exception:
                pass
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                raise HTTPException(500, f"DB commit failed: {e}")
            return {"ok": True, "width": img.width, "height": img.height}
        finally:
            db.close()

    # ---- POST /api/gallery/{image_id}/rename ----
    @router.post("/api/gallery/{image_id}/rename")
    async def gallery_rename(request: Request, image_id: str):
        """Rename a gallery photo. Stores the new name in the `prompt`
        column (which serves as the user-facing label for uploaded
        photos that have no AI prompt)."""
        user = require_user(request)
        data = await request.json()
        new_name = (data.get("name") or "").strip()
        if not new_name:
            raise HTTPException(400, "Name cannot be empty")
        if len(new_name) > 500:
            raise HTTPException(400, "Name too long")
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not _gallery_owner_matches(img.owner, user):
                raise HTTPException(403, "Not your image")
            img.prompt = new_name
            db.commit()
            return {"ok": True, "name": new_name}
        finally:
            db.close()

    # ---- POST /api/gallery/{image_id}/rotate ----
    @router.post("/api/gallery/{image_id}/rotate")
    async def gallery_rotate(request: Request, image_id: str):
        """Rotate an image by ±90° or 180°. Updates the file on disk and the
        width/height in the DB. Body: {angle: 90 | -90 | 180}."""
        from pathlib import Path
        from PIL import Image
        from io import BytesIO

        data = await request.json()
        try:
            angle = int(data.get("angle", 90))
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid angle")
        if angle not in (90, -90, 180, 270):
            raise HTTPException(400, "Angle must be 90, -90, 180, or 270")

        user = require_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not _gallery_owner_matches(img.owner, user):
                raise HTTPException(403, "Not your image")

            img_path = _gallery_image_path(img.filename)
            if not img_path.exists():
                raise HTTPException(404, "Image file not found")

            # PIL rotates counter-clockwise; the API takes "clockwise"
            # convention so we negate to match user expectation.
            with Image.open(img_path) as pil:
                rotated = pil.rotate(-angle, expand=True)
                # Recompute hash so dedupe stays accurate.
                buf = BytesIO()
                ext = img.filename.rsplit(".", 1)[-1].lower()
                save_kwargs = {}
                if ext in ("jpg", "jpeg"):
                    save_kwargs["quality"] = 95
                    fmt = "JPEG"
                elif ext == "webp":
                    fmt = "WEBP"
                    save_kwargs["quality"] = 95
                else:
                    fmt = "PNG"
                rotated.save(buf, format=fmt, **save_kwargs)
                content = buf.getvalue()
                img_path.write_bytes(content)
                img.file_hash = hashlib.sha256(content).hexdigest()
                img.file_size = len(content)
                img.width, img.height = rotated.size
            db.commit()
            return {"ok": True, "width": img.width, "height": img.height}
        finally:
            db.close()

    # ---- POST /api/gallery/ai-upscale ----
    @router.post("/api/gallery/ai-upscale")
    async def gallery_ai_upscale(request: Request):
        """AI upscale using img2img with the diffusion server."""
        import base64, httpx

        user = require_privilege(request, "can_generate_images")
        form = await request.form()
        file = form.get("image")
        if not file: raise HTTPException(400, "No image")
        scale = int(form.get("scale", "2"))

        image_bytes = await read_upload_limited(file, GALLERY_TRANSFORM_UPLOAD_MAX_BYTES, "Image upload")
        b64 = base64.b64encode(image_bytes).decode()

        # Find image endpoint
        db = SessionLocal()
        try:
            ep = _first_visible_image_endpoint(db, user)
        finally:
            db.close()

        if not ep:
            raise HTTPException(400, "No image generation endpoint configured. Add one in Settings → Add Models.")

        base_url = ep.base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"

        # Use img2img endpoint if available, otherwise upscale via canvas on client
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{base_url}/images/upscale", json={
                    "image": b64, "scale": scale,
                })
                if resp.status_code == 200:
                    data = resp.json()
                    return {"image": data.get("data", [{}])[0].get("b64_json", "")}
                # Fallback: no upscale endpoint — return error
                return {"error": f"Upscale endpoint not available ({resp.status_code})"}
        except Exception as e:
            return {"error": str(e)}

    # ---- POST /api/gallery/style-transfer ----
    @router.post("/api/gallery/style-transfer")
    async def gallery_style_transfer(request: Request):
        """Style transfer using img2img with the diffusion server."""
        import base64, httpx

        user = require_privilege(request, "can_generate_images")
        form = await request.form()
        file = form.get("image")
        prompt = form.get("prompt", "")
        strength = float(form.get("strength", "0.55"))
        if not file: raise HTTPException(400, "No image")

        image_bytes = await read_upload_limited(file, GALLERY_TRANSFORM_UPLOAD_MAX_BYTES, "Image upload")
        b64 = base64.b64encode(image_bytes).decode()

        db = SessionLocal()
        try:
            ep = _first_visible_image_endpoint(db, user)
        finally:
            db.close()

        if not ep:
            raise HTTPException(400, "No image generation endpoint configured.")

        base_url = ep.base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"

        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(f"{base_url}/images/generations", json={
                    "prompt": prompt,
                    "image": b64,
                    "strength": strength,
                    "response_format": "b64_json",
                })
                if resp.status_code == 200:
                    data = resp.json()
                    img_data = data.get("data", [{}])[0].get("b64_json", "")
                    if img_data:
                        return {"image": img_data}
                return {"error": f"Style transfer failed ({resp.status_code})"}
        except Exception as e:
            return {"error": str(e)}

    # ---- GET /api/gallery/tags ----
    @router.get("/api/gallery/tags")
    async def gallery_tags(request: Request) -> Dict[str, Any]:
        """Return distinct tags across all active gallery images."""
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryImage.tags).filter(
                GalleryImage.is_active == True, GalleryImage.tags != None, GalleryImage.tags != ""
            )
            q = _owner_filter(q, user)
            rows = q.all()
            tag_set = set()
            for (raw,) in rows:
                for t in raw.split(","):
                    t = t.strip()
                    if t:
                        tag_set.add(t)
            return {"tags": sorted(tag_set)}
        finally:
            db.close()

    # ---- GET /api/gallery/library ----
    @router.get("/api/gallery/library")
    async def gallery_library(
        request: Request,
        search: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        model: Optional[str] = Query(None),
        album: Optional[str] = Query(None),
        favorites: bool = Query(False),
        sort: str = Query("recent"),
        seed: Optional[int] = Query(None),
        offset: int = Query(0, ge=0),
        limit: int = Query(24, ge=1, le=100),
    ) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            # Distinct tags for filter UI
            tag_q = db.query(GalleryImage.tags).filter(
                GalleryImage.is_active == True, GalleryImage.tags != None, GalleryImage.tags != ""
            )
            tag_q = _owner_filter(tag_q, user)
            tag_rows = tag_q.all()
            all_tags = set()
            for (raw,) in tag_rows:
                for t in raw.split(","):
                    t = t.strip()
                    if t:
                        all_tags.add(t)

            # Distinct models for filter UI
            model_q = db.query(GalleryImage.model).filter(
                GalleryImage.is_active == True, GalleryImage.model != None
            )
            model_q = _owner_filter(model_q, user)
            model_rows = model_q.distinct().all()
            all_models = sorted([m for (m,) in model_rows if m])

            # Base query with left join to sessions for session_name
            q = (
                db.query(GalleryImage, DbSession.name)
                .outerjoin(DbSession, GalleryImage.session_id == DbSession.id)
                .filter(GalleryImage.is_active == True)
            )
            q = _owner_filter(q, user)

            # Search filter (prompt + tags + ai_tags)
            if search:
                term = f"%{search}%"
                from sqlalchemy import or_
                q = q.filter(or_(
                    GalleryImage.prompt.ilike(term),
                    GalleryImage.tags.ilike(term),
                    GalleryImage.ai_tags.ilike(term),
                ))

            # Tag filter. The UI stacks multiple tag pills by passing them
            # comma-separated — each tag adds a separate AND-filter so the
            # result set narrows as the user piles tags on. A single tag
            # (no commas) is the original behaviour.
            if tag:
                from sqlalchemy import or_ as _or
                for one in (t.strip() for t in tag.split(",")):
                    if not one:
                        continue
                    q = q.filter(_or(
                        GalleryImage.tags.ilike(f"%{one}%"),
                        GalleryImage.ai_tags.ilike(f"%{one}%"),
                    ))

            # Model filter
            if model:
                q = q.filter(GalleryImage.model == model)

            # Album filter
            if album:
                q = q.filter(GalleryImage.album_id == album)

            # Favorites filter
            if favorites:
                q = q.filter(GalleryImage.favorite == True)

            # Total before pagination
            total = q.count()
            # How many of those have AI tags — surfaced as "X/Y photos tagged"
            # in the AI-tagging settings header.
            total_tagged = q.filter(
                GalleryImage.ai_tags.isnot(None), GalleryImage.ai_tags != ""
            ).count()

            # Sorting
            if sort == "shuffle":
                # Seeded shuffle: fetch all matching IDs, shuffle them
                # deterministically with `seed`, then re-query for just the
                # page we want. Stable across pagination as long as the
                # client keeps the same seed.
                import random as _random
                id_rows = q.with_entities(GalleryImage.id).all()
                all_ids = [r[0] for r in id_rows]
                rng = _random.Random(seed if seed is not None else 0)
                rng.shuffle(all_ids)
                page_ids = all_ids[offset:offset + limit]
                if page_ids:
                    page_rows = (
                        db.query(GalleryImage, DbSession.name)
                        .outerjoin(DbSession, GalleryImage.session_id == DbSession.id)
                        .filter(GalleryImage.id.in_(page_ids))
                        .all()
                    )
                    # Restore the shuffled order
                    by_id = {img.id: (img, session_name) for img, session_name in page_rows}
                    rows = [by_id[i] for i in page_ids if i in by_id]
                else:
                    rows = []
            else:
                if sort == "oldest":
                    q = q.order_by(GalleryImage.created_at.asc())
                else:  # recent
                    q = q.order_by(GalleryImage.created_at.desc())
                rows = q.offset(offset).limit(limit).all()

            items = []
            for img, session_name in rows:
                items.append(_image_to_dict(img, session_name))

            return {
                "items": items,
                "total": total,
                "total_tagged": total_tagged,
                "tags": sorted(all_tags),
                "models": all_models,
            }
        except Exception as e:
            logger.error(f"Failed to fetch gallery library: {e}")
            raise HTTPException(500, f"Failed to fetch gallery library: {e}")
        finally:
            db.close()

    # ---- Album CRUD (must be before {image_id} catch-all) ----

    @router.get("/api/gallery/albums")
    async def list_albums(request: Request):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryAlbum)
            q = _owner_filter(q, user, GalleryAlbum)
            albums = q.order_by(GalleryAlbum.created_at.desc()).all()
            result = []
            for a in albums:
                _count_q = db.query(GalleryImage).filter(
                    GalleryImage.album_id == a.id, GalleryImage.is_active == True
                )
                _count_q = _owner_filter(_count_q, user)
                count = _count_q.count()
                cover_url = None
                if a.cover_id:
                    cover_q = db.query(GalleryImage).filter(GalleryImage.id == a.cover_id)
                    cover = _owner_filter(cover_q, user).first()
                    if cover:
                        cover_url = f"/api/generated-image/{cover.filename}"
                elif count > 0:
                    _cover_q = db.query(GalleryImage).filter(
                        GalleryImage.album_id == a.id, GalleryImage.is_active == True
                    )
                    _cover_q = _owner_filter(_cover_q, user)
                    first = _cover_q.order_by(GalleryImage.created_at.desc()).first()
                    if first:
                        cover_url = f"/api/generated-image/{first.filename}"
                result.append({
                    "id": a.id, "name": a.name, "description": a.description or "",
                    "cover_url": cover_url, "count": count,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                })
            return {"albums": result}
        finally:
            db.close()

    @router.post("/api/gallery/albums")
    async def create_album(request: Request):
        import uuid
        user = require_user(request)
        data = await request.json()
        name = (data.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Album name required")
        db = SessionLocal()
        try:
            a = GalleryAlbum(
                id=str(uuid.uuid4()), name=name,
                description=data.get("description", ""),
                owner=user,
            )
            db.add(a)
            db.commit()
            return {"ok": True, "id": a.id, "name": a.name}
        finally:
            db.close()

    @router.get("/api/gallery/stats")
    async def gallery_stats(request: Request):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            from sqlalchemy import func
            base = db.query(GalleryImage).filter(GalleryImage.is_active == True)
            size_q = db.query(func.sum(GalleryImage.file_size)).filter(GalleryImage.is_active == True)
            album_q = db.query(GalleryAlbum)
            base = _owner_filter(base, user)
            size_q = _owner_filter(size_q, user)
            album_q = _owner_filter(album_q, user, GalleryAlbum)
            total = base.count()
            total_size = size_q.scalar() or 0
            fav_count = base.filter(GalleryImage.favorite == True).count()
            album_count = album_q.count()
            return {
                "total_photos": total,
                "total_size": total_size,
                "total_size_human": _human_size(total_size),
                "favorites": fav_count,
                "albums": album_count,
            }
        finally:
            db.close()

    @router.post("/api/gallery/ai-tag-batch")
    async def ai_tag_batch(
        request: Request,
        album_id: Optional[str] = Query(None),
        limit: int = Query(200),
    ):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(
                GalleryImage.is_active == True,
                (GalleryImage.ai_tags == None) | (GalleryImage.ai_tags == ""),
            )
            q = _owner_filter(q, user)
            if album_id:
                q = q.filter(GalleryImage.album_id == album_id)
            untagged = q.count()
            ids = [img.id for img in q.limit(max(1, min(limit, 500))).all()]
            return {"ok": True, "queued": len(ids), "total_untagged": untagged, "image_ids": ids}
        finally:
            db.close()

    # ---- GET /api/gallery/{image_id} ----
    @router.get("/api/gallery/{image_id}")
    async def get_gallery_image(request: Request, image_id: str) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            row = (
                db.query(GalleryImage, DbSession.name)
                .outerjoin(DbSession, GalleryImage.session_id == DbSession.id)
                .filter(GalleryImage.id == image_id)
                .first()
            )
            if not row:
                raise HTTPException(404, "Image not found")
            img, session_name = row
            if not _gallery_owner_matches(img.owner, user):
                raise HTTPException(404, "Image not found")
            return _image_to_dict(img, session_name)
        finally:
            db.close()

    # ---- PATCH /api/gallery/{image_id} ----
    @router.patch("/api/gallery/{image_id}")
    async def patch_gallery_image(request: Request, image_id: str, req: GalleryPatch) -> Dict[str, Any]:
        user = require_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not _gallery_owner_matches(img.owner, user):
                raise HTTPException(404, "Image not found")
            if req.tags is not None:
                # Drop any tag from the user-tags field that already lives in
                # ai_tags — earlier flows wrote AI suggestions to both fields
                # and the UI showed every photo with the same chips twice.
                ai_set = {t.strip().lower() for t in (img.ai_tags or '').split(',') if t.strip()}
                cleaned = []
                seen = set()
                for raw in (req.tags or '').split(','):
                    t = raw.strip()
                    k = t.lower()
                    if not t or k in seen or k in ai_set:
                        continue
                    seen.add(k)
                    cleaned.append(t)
                img.tags = ', '.join(cleaned)
            if req.favorite is not None:
                img.favorite = req.favorite
            if req.album_id is not None:
                if req.album_id:
                    # Validate the target album belongs to the caller before
                    # moving the image into it — mirrors add_to_album, so you
                    # cannot file your image into another user's album.
                    _get_or_404_album(db, req.album_id, user)
                    img.album_id = req.album_id
                else:
                    img.album_id = None
            db.commit()
            db.refresh(img)
            return _image_to_dict(img)
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(500, str(e))
        finally:
            db.close()

    # ---- POST /api/gallery/download-zip ----
    # Bundle the given image ids into a single .zip for download. Used by the
    # gallery's bulk "Download" when many photos are selected (one file instead
    # of a flood of individual downloads).
    @router.post("/api/gallery/download-zip")
    async def gallery_download_zip(request: Request):
        user = require_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        try:
            data = await request.json()
        except Exception:
            data = {}
        ids = data.get("ids") or []
        if not ids:
            raise HTTPException(400, "No images specified")
        db = SessionLocal()
        try:
            imgs = db.query(GalleryImage).filter(
                GalleryImage.id.in_(ids),
                GalleryImage.owner == user,
            ).all()
            if not imgs:
                raise HTTPException(404, "No images found")
            import io
            import re
            import zipfile
            buf = io.BytesIO()
            used = set()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for img in imgs:
                    src = _gallery_image_path(img.filename)
                    if not src.exists():
                        continue
                    ext = src.suffix or ".png"
                    base = (img.prompt or "").strip() or src.stem
                    base = re.sub(r"[^\w\-. ]+", "", base)[:60].strip() or img.id
                    name = f"{base}{ext}"
                    i = 1
                    while name in used:
                        name = f"{base}-{i}{ext}"
                        i += 1
                    used.add(name)
                    zf.write(src, arcname=name)
            if not used:
                raise HTTPException(404, "No image files found on disk")
            from fastapi import Response
            return Response(
                content=buf.getvalue(),
                media_type="application/zip",
                headers={"Content-Disposition": 'attachment; filename="gallery-photos.zip"'},
            )
        finally:
            db.close()

    # ---- POST /api/gallery/clear-user-tags ----
    # Wipe the `tags` field on every image owned by the current user.
    # Leaves `ai_tags` intact. Use after a bug populated user-tags with
    # AI-suggested values you never added.
    @router.post("/api/gallery/clear-user-tags")
    async def clear_gallery_user_tags(request: Request) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(GalleryImage.is_active == True)
            q = _owner_filter(q, user)
            cleared = 0
            for img in q.all():
                if img.tags:
                    img.tags = ''
                    cleared += 1
            db.commit()
            return {"ok": True, "cleared": cleared}
        except Exception as e:
            db.rollback()
            raise HTTPException(500, str(e))
        finally:
            db.close()

    # ---- POST /api/gallery/clear-ai-tags ----
    # Wipe the `ai_tags` field on every image owned by the current user.
    # Leaves user `tags` intact. Use when AI-suggested tags like "dog" /
    # "woman" have leaked into the gallery and you want them gone.
    @router.post("/api/gallery/clear-ai-tags")
    async def clear_gallery_ai_tags(request: Request, image_id: Optional[str] = Query(None)) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(GalleryImage.is_active == True)
            q = _owner_filter(q, user)
            if image_id:  # clear just one photo's AI tags
                q = q.filter(GalleryImage.id == image_id)
            cleared = 0
            for img in q.all():
                if img.ai_tags:
                    img.ai_tags = ''
                    cleared += 1
            db.commit()
            return {"ok": True, "cleared": cleared}
        except Exception as e:
            db.rollback()
            raise HTTPException(500, str(e))
        finally:
            db.close()

    # ---- POST /api/gallery/dedupe-tags ----
    # One-shot cleanup: for every image owned by the current user, drop any
    # tag from `tags` that also appears in `ai_tags` (case-insensitive).
    # Returns how many rows were touched + how many tags removed.
    @router.post("/api/gallery/dedupe-tags")
    async def dedupe_gallery_tags(request: Request) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(GalleryImage.is_active == True)
            q = _owner_filter(q, user)
            rows_touched = 0
            tags_removed = 0
            for img in q.all():
                ai_set = {t.strip().lower() for t in (img.ai_tags or '').split(',') if t.strip()}
                if not ai_set:
                    continue
                original = [t.strip() for t in (img.tags or '').split(',') if t.strip()]
                cleaned = []
                seen = set()
                for t in original:
                    k = t.lower()
                    if k in ai_set or k in seen:
                        continue
                    seen.add(k)
                    cleaned.append(t)
                if len(cleaned) != len(original):
                    rows_touched += 1
                    tags_removed += len(original) - len(cleaned)
                    img.tags = ', '.join(cleaned)
            db.commit()
            return {"ok": True, "rows_touched": rows_touched, "tags_removed": tags_removed}
        except Exception as e:
            db.rollback()
            raise HTTPException(500, str(e))
        finally:
            db.close()

    # ---- DELETE /api/gallery/{image_id} ----
    @router.delete("/api/gallery/{image_id}")
    async def delete_gallery_image(request: Request, image_id: str) -> Dict[str, str]:
        user = require_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not _gallery_owner_matches(img.owner, user):
                raise HTTPException(404, "Image not found")

            img_filename = img.filename
            # Remove the file from disk
            img_path = _gallery_image_path(img_filename)
            if img_path.exists():
                img_path.unlink()

            # Soft-delete the record
            img.is_active = False
            db.commit()

            # Strip stale chat-history references so the image bubble
            # (and its prompt caption) doesn't come back after a server
            # reboot replays the session. We remove the matching tool
            # event entirely; if that leaves the message with no other
            # tool events AND a "Generated image for: …" body, drop the
            # whole row so there's no remnant.
            try:
                from core.database import ChatMessage as _ChatMessage
                from sqlalchemy import or_ as _or
                import json as _json
                # Match by image_id OR by filename — older messages
                # (saved before we threaded image_id through the SSE)
                # only carry image_url containing the filename.
                msgs = db.query(_ChatMessage).filter(
                    _ChatMessage.meta_data.isnot(None),
                    _or(
                        _ChatMessage.meta_data.like(f"%{image_id}%"),
                        _ChatMessage.meta_data.like(f"%{img_filename}%"),
                    ),
                ).all()
                rows_to_delete = []
                for m in msgs:
                    if not m.meta_data:
                        continue
                    try:
                        meta = _json.loads(m.meta_data)
                    except Exception:
                        continue
                    events = meta.get("tool_events") or []
                    new_events = []
                    removed_any = False
                    for ev in events:
                        if not isinstance(ev, dict):
                            new_events.append(ev)
                            continue
                        is_match = ev.get("image_id") == image_id or (
                            ev.get("image_url") and img_filename in ev["image_url"]
                        )
                        if is_match:
                            removed_any = True
                            continue
                        new_events.append(ev)
                    if not removed_any:
                        continue
                    # If the message has no other tool events left, drop
                    # it AND the immediately preceding user prompt that
                    # asked for the image, so no remnant of the exchange
                    # survives.
                    if not new_events:
                        rows_to_delete.append(m)
                        prev = (
                            db.query(_ChatMessage)
                            .filter(
                                _ChatMessage.session_id == m.session_id,
                                _ChatMessage.timestamp < m.timestamp,
                            )
                            .order_by(_ChatMessage.timestamp.desc())
                            .first()
                        )
                        if prev and prev.role == "user":
                            prev_meta = {}
                            try:
                                prev_meta = _json.loads(prev.meta_data) if prev.meta_data else {}
                            except Exception:
                                prev_meta = {}
                            # Only purge the prompt if it has no tool
                            # events of its own (i.e. it's a pure user
                            # message, not an agent step).
                            if not (prev_meta.get("tool_events") or []):
                                rows_to_delete.append(prev)
                    else:
                        meta["tool_events"] = new_events
                        m.meta_data = _json.dumps(meta)
                for m in rows_to_delete:
                    db.delete(m)
                if msgs:
                    db.commit()
            except Exception as _e:
                # Cleanup is best-effort — never block the delete itself.
                logger.warning(f"chat-history cleanup after image delete failed: {_e}")

            return {"status": "deleted", "id": image_id}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(500, str(e))
        finally:
            db.close()

    # ---- GET /api/image/inpaint/progress/{id} — live progress for an active run ----
    @router.get("/api/image/inpaint/progress/{progress_id}")
    async def inpaint_progress_stream(progress_id: str, request: Request):
        user = require_privilege(request, "can_generate_images")
        progress_id = _normalize_inpaint_progress_id(progress_id)
        if not progress_id:
            raise HTTPException(404, "Unknown inpaint progress stream")

        async def event_stream():
            cursor = 0
            started = time.time()
            while True:
                record = _INPAINT_PROGRESS.get(progress_id)
                if record and record.get("owner") and user and record.get("owner") != user:
                    yield f"data: {json.dumps({'phase': 'forbidden', 'message': 'Progress stream is not available', 'done': True, 'error': True})}\n\n"
                    break
                events = list(record.get("events") or []) if record else []
                while cursor < len(events):
                    yield f"data: {json.dumps(events[cursor])}\n\n"
                    cursor += 1
                if record and record.get("done") and cursor >= len(events):
                    _INPAINT_PROGRESS.pop(progress_id, None)
                    break
                if await request.is_disconnected():
                    break
                if not record and time.time() - started > 300:
                    break
                await asyncio.sleep(0.35)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- POST /api/image/inpaint — proxy to diffusion server OR OpenAI ----
    @router.post("/api/image/inpaint")
    async def inpaint_proxy(request: Request):
        """Forward inpaint request. If the selected endpoint is OpenAI, re-shape
        the request for /v1/images/edits (multipart, inverted mask). Otherwise
        proxy through to a self-hosted diffusion server's /v1/images/inpaint."""
        import base64, json, re
        import httpx
        user = require_privilege(request, "can_generate_images")
        try:
            content_length = int(request.headers.get("content-length") or 0)
        except Exception:
            content_length = 0
        if content_length > 32 * 1024 * 1024:
            raise HTTPException(413, "Inpaint request is too large. Try a smaller mask area or resize the canvas.")
        try:
            body = await request.json()
        except MemoryError:
            raise HTTPException(413, "Inpaint request is too large. Try a smaller mask area or resize the canvas.")
        progress_id = _normalize_inpaint_progress_id(body.pop("_progress_id", ""))

        def progress(
            phase: str,
            message: str = "",
            *,
            percent: int | None = None,
            done: bool = False,
            error: bool = False,
        ) -> None:
            _push_inpaint_progress(
                progress_id,
                user,
                phase,
                message,
                percent=percent,
                done=done,
                error=error,
            )

        progress("accepted", "Backend received the inpaint request.", percent=52)
        # Use endpoint from request body (editor dropdown) or fall back to DB lookup
        endpoint_id = (body.pop("_endpoint_id", "") or "").strip()
        base = (body.pop("_endpoint", "") or "").rstrip("/")
        # SSRF hardening: validate a client-supplied endpoint before any
        # outbound request (mirrors routes/embedding_routes.py).
        if base and not endpoint_id:
            from src.url_safety import check_outbound_url
            ok, reason = check_outbound_url(
                base,
                block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
            )
            if not ok:
                progress("failed", f"Rejected endpoint URL: {reason}", percent=100, done=True, error=True)
                raise HTTPException(400, f"Rejected endpoint URL: {reason}")
        chosen_model = (body.pop("_model", "") or "").strip()
        api_key = None
        if endpoint_id:
            db = SessionLocal()
            try:
                ep = _visible_image_endpoint_for_id(db, endpoint_id, user)
                if not ep:
                    progress("failed", "The selected image endpoint is not registered for this user.", percent=100, done=True, error=True)
                    raise HTTPException(403, "Choose a registered image endpoint")
                base = ep.base_url.rstrip("/")
                api_key = ep.api_key
            finally:
                db.close()
        elif not base:
            db = SessionLocal()
            try:
                ep = _first_visible_image_endpoint(db, user)
                if not ep:
                    progress("failed", "No image generation endpoint is configured.", percent=100, done=True, error=True)
                    raise HTTPException(400, "No image generation endpoint configured. Serve a diffusion model via Cookbook first.")
                base = ep.base_url.rstrip("/")
                api_key = ep.api_key
            finally:
                db.close()
        else:
            # Pull api_key from the matching DB row so OpenAI auth works.
            # Users may have stored base_url with/without /v1 suffix and with/without
            # trailing slash, so compare normalized forms.
            def _norm_url(u: str) -> str:
                if not u:
                    return u
                u = u.rstrip("/")
                if u.endswith("/v1"):
                    u = u[:-3]
                return u
            _target = _norm_url(base)
            db = SessionLocal()
            try:
                ep = _visible_image_endpoint_for_base(db, _target, user)
                if ep:
                    base = (ep.base_url or base).rstrip("/")
                    api_key = ep.api_key
                elif user and not _current_user_is_admin(request, user):
                    progress("failed", "The selected image endpoint is not registered for this user.", percent=100, done=True, error=True)
                    raise HTTPException(403, "Choose a registered image endpoint")
            finally:
                db.close()

        if not base.endswith("/v1"):
            base += "/v1"

        base_root = base[:-3].rstrip("/") if base.endswith("/v1") else base.rstrip("/")
        is_openai = "api.openai.com" in base

        def _is_openai_compatible_image_edit_base(value: str) -> bool:
            lower = str(value or "").lower()
            return (
                "api.openai.com" in lower
                or "compatible-mode" in lower
                or "dashscope" in lower
                or "aliyuncs.com" in lower
            )

        is_openai_style_edit = _is_openai_compatible_image_edit_base(base)
        endpoint_label = chosen_model or base_root or base
        progress("endpoint", f"Selected {endpoint_label}.", percent=56)

        def _strip_data_url(value: str) -> str:
            value = str(value or "").strip()
            if "," in value and value.lower().startswith("data:"):
                return value.split(",", 1)[1]
            return value

        def _looks_like_provider_image_value(value) -> bool:
            text = str(value or "").strip()
            if not text:
                return False
            lower = text.lower()
            if lower.startswith(("data:image/", "http://", "https://")):
                return True
            clean = re.sub(r"\s+", "", _strip_data_url(text))
            if clean.startswith(("iVBOR", "/9j/", "UklGR")):
                return True
            if len(clean) < 128:
                return False
            try:
                raw = base64.b64decode(clean, validate=True)
                return raw.startswith(b"\x89PNG") or raw.startswith(b"\xff\xd8") or raw.startswith(b"RIFF")
            except Exception:
                return False

        def _image_value_from_text(value: str) -> str:
            text = str(value or "").strip()
            match = re.search(r"data:image/(?:png|jpe?g|webp);base64,[A-Za-z0-9+/=\s]+", text, re.I)
            if match:
                return match.group(0)
            match = re.search(r'"(?:b64_json|image|base64|image_base64|imageBase64|url|image_url|imageUrl)"\s*:\s*"([^"]{128,})"', text, re.I)
            if match:
                candidate = match.group(1).replace("\\/", "/").replace("\\n", "").replace("\\r", "")
                return candidate if _looks_like_provider_image_value(candidate) else ""
            return ""

        def _first_provider_image_value(node, depth=0) -> str:
            if node is None or depth > 5:
                return ""
            if isinstance(node, str):
                value = node.strip()
                if _looks_like_provider_image_value(value):
                    return value
                embedded = _image_value_from_text(value)
                if embedded:
                    return embedded
                if (value.startswith("{") and value.endswith("}")) or (value.startswith("[") and value.endswith("]")):
                    try:
                        return _first_provider_image_value(json.loads(value), depth + 1)
                    except Exception:
                        return ""
                return ""
            if isinstance(node, list):
                for item in node:
                    found = _first_provider_image_value(item, depth + 1)
                    if found:
                        return found
                return ""
            if not isinstance(node, dict):
                return ""
            preferred = [
                "image", "b64_json", "base64", "image_base64", "imageBase64",
                "url", "image_url", "imageUrl", "data", "images",
                "content", "message", "choices", "output", "outputs", "result", "results", "artifact", "artifacts",
            ]
            for key in preferred:
                if key in node:
                    found = _first_provider_image_value(node.get(key), depth + 1)
                    if found:
                        return found
            for value in node.values():
                found = _first_provider_image_value(value, depth + 1)
                if found:
                    return found
            return ""

        def _provider_error_text(node, depth=0) -> str:
            if node is None or depth > 4:
                return ""
            if isinstance(node, str):
                text = node.replace("\n", " ").strip()
                return "" if _looks_like_provider_image_value(text) else text[:220]
            if isinstance(node, list):
                for item in node:
                    found = _provider_error_text(item, depth + 1)
                    if found:
                        return found
                return ""
            if not isinstance(node, dict):
                return ""
            for key in ("error", "detail", "message", "reason", "status_message"):
                if key in node:
                    found = _provider_error_text(node.get(key), depth + 1)
                    if found:
                        return found
            return ""

        def _provider_no_image_detail(node) -> str:
            err = _provider_error_text(node)
            if err:
                return err
            if isinstance(node, dict):
                keys = ", ".join(str(k) for k in list(node.keys())[:10])
                return f"server returned no image (keys: {keys})" if keys else "server returned no image"
            text = str(node or "").strip()
            return f"server returned no image: {text[:180]}" if text else "server returned no image"

        async def _provider_image_value_to_b64(value: str, client) -> str:
            value = str(value or "").strip()
            lower = value.lower()
            if lower.startswith(("http://", "https://")):
                r = await client.get(value)
                if r.status_code != 200:
                    raise HTTPException(502, f"Image endpoint returned URL that could not be downloaded: HTTP {r.status_code}")
                return base64.b64encode(r.content).decode()
            return re.sub(r"\s+", "", _strip_data_url(value))

        async def _normalized_provider_image_response(text: str, client):
            try:
                parsed = json.loads(text or "{}")
            except Exception:
                parsed = text or ""
            image = _first_provider_image_value(parsed)
            if not image:
                return None, _provider_no_image_detail(parsed)
            return await _provider_image_value_to_b64(image, client), ""

        if is_openai_style_edit:
            # OpenAI path: /v1/images/edits with gpt-image-1.
            # Mask convention differs from Stable Diffusion:
            #   SD:     white pixels = regenerate, black = keep
            #   OpenAI: transparent alpha = regenerate, opaque = keep
            # So we convert the incoming PNG mask into an alpha-channel PNG.
            provider_label = "OpenAI" if is_openai else "OpenAI-compatible image endpoint"
            progress("openai_prepare", f"Preparing {provider_label} edit request.", percent=60)
            if not api_key:
                progress("failed", f"{provider_label} has no stored API key.", percent=100, done=True, error=True)
                raise HTTPException(400, f"{provider_label} has no api_key stored - edit it in Endpoints settings.")
            import base64, io
            try:
                from PIL import Image
            except ImportError:
                progress("failed", "Pillow is not installed on the server.", percent=100, done=True, error=True)
                raise HTTPException(500, "Pillow not installed on server")

            try:
                img_bytes = base64.b64decode(body["image"])
                mask_bytes = base64.b64decode(body["mask"])
                source_png = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                mask_png = Image.open(io.BytesIO(mask_bytes)).convert("L")  # luminance
                # Build OpenAI mask: RGBA where alpha=255 means keep, 0 means regenerate.
                # SD mask: white (255) = regenerate → alpha 0.  Black (0) = keep → alpha 255.
                # RGB must be white for keep areas; start from fully-white opaque and
                # overwrite alpha so visual contents match the expected semantic.
                alpha = mask_png.point(lambda p: 255 - p)
                oa_mask = Image.new("RGBA", source_png.size, (255, 255, 255, 255))
                oa_mask.putalpha(alpha)

                src_buf = io.BytesIO()
                source_png.save(src_buf, format="PNG")
                src_buf.seek(0)
                mask_buf = io.BytesIO()
                oa_mask.save(mask_buf, format="PNG")
                mask_buf.seek(0)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(400, f"Failed to prepare OpenAI request: {e}")

            width = int(body.get("width") or 1024)
            height = int(body.get("height") or 1024)
            # gpt-image-1 only accepts 1024x1024, 1024x1536, 1536x1024 (no 'auto'
            # for edits). Pick the closest to preserve aspect, default square.
            if width > height * 1.15:
                size = "1536x1024"
            elif height > width * 1.15:
                size = "1024x1536"
            else:
                size = "1024x1024"

            files = {
                "image": ("source.png", src_buf.getvalue(), "image/png"),
                "mask": ("mask.png", mask_buf.getvalue(), "image/png"),
            }
            # Honor explicit model selection from the editor. Only native OpenAI
            # should silently fall back to gpt-image-1; compatible providers need
            # their own edit-capable model name.
            oa_model = chosen_model or ("gpt-image-1" if is_openai else "")
            if not oa_model:
                progress("failed", "No image-edit model is selected for this endpoint.", percent=100, done=True, error=True)
                raise HTTPException(400, "Select an image-edit model for this endpoint.")
            if "dall-e-3" in oa_model.lower():
                progress("failed", "dall-e-3 does not support image edits.", percent=100, done=True, error=True)
                raise HTTPException(400, "dall-e-3 doesn't support image edits — pick gpt-image-1 or dall-e-2")
            data = {
                "model": oa_model,
                "prompt": body.get("prompt", ""),
                "size": size,
                "n": "1",
            }
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    async def _chat_image_edit(previous_error=""):
                        chat_prompt = (
                            f"{body.get('prompt', '')}\n\n"
                            "Use the first image as the source. The second image is a mask: "
                            "white pixels mark the area to edit, black pixels should stay unchanged."
                        ).strip()
                        chat_payload = {
                            "messages": [{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": chat_prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{body.get('image', '')}"}},
                                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{body.get('mask', '')}"}},
                                ],
                            }],
                            "stream": False,
                            "extra_body": {
                                "num_inference_steps": 50,
                                "guidance_scale": 1,
                                "size": size,
                                "output_format": "png",
                            },
                        }
                        if oa_model:
                            chat_payload["model"] = oa_model
                        progress("model_wait", "Trying /v1/chat/completions image edit fallback.", percent=72)
                        cr = await client.post(f"{base}/chat/completions", headers=headers, json=chat_payload)
                        if cr.status_code < 200 or cr.status_code >= 300:
                            suffix = f" Previous /v1/images/edits error: {previous_error[:180]}" if previous_error else ""
                            raise HTTPException(
                                cr.status_code,
                                f"Image edit chat fallback failed at /v1/chat/completions: {cr.text[:300]}{suffix}",
                            )
                        image_b64, no_image_detail = await _normalized_provider_image_response(cr.text, client)
                        if not image_b64:
                            suffix = f" Previous /v1/images/edits error: {previous_error[:180]}" if previous_error else ""
                            raise HTTPException(
                                502,
                                f"Image edit chat fallback returned no image: {no_image_detail}{suffix}",
                            )
                        return image_b64

                    async def _json_or_chat_image_edit(previous_error=""):
                        mask_b64 = base64.b64encode(mask_buf.getvalue()).decode()
                        json_payload = {
                            "model": oa_model,
                            "prompt": body.get("prompt", ""),
                            "image": body.get("image", ""),
                            "mask_image": mask_b64,
                            "size": size,
                            "n": 1,
                            "response_format": "b64_json",
                            "output_format": "png",
                        }
                        progress("model_wait", "Trying /v1/images/edits JSON fallback.", percent=70)
                        jr = await client.post(f"{base}/images/edits", headers=headers, json=json_payload)
                        if jr.status_code >= 200 and jr.status_code < 300:
                            image_b64, no_image_detail = await _normalized_provider_image_response(jr.text, client)
                            if image_b64:
                                return image_b64
                            previous_error = f"/images/edits JSON: {no_image_detail}"
                        else:
                            previous_error = f"/images/edits JSON: HTTP {jr.status_code}: {jr.text[:300]}"
                        return await _chat_image_edit(previous_error)

                    progress("model_wait", f"Sending edit request to {oa_model}.", percent=66)
                    r = await client.post(f"{base}/images/edits", headers=headers, data=data, files=files)
                    if r.status_code < 200 or r.status_code >= 300:
                        last_err = f"/images/edits: HTTP {r.status_code}: {r.text[:300]}"
                        if is_openai:
                            progress("failed", f"OpenAI edit failed with HTTP {r.status_code}.", percent=100, done=True, error=True)
                            raise HTTPException(r.status_code, f"OpenAI edit failed: {r.text[:300]}")
                        raw_b64 = await _json_or_chat_image_edit(last_err)
                    else:
                        progress("response", f"{provider_label} returned an edited image.", percent=78)
                        raw_b64, no_image_detail = await _normalized_provider_image_response(r.text, client)
                        if not raw_b64 and not is_openai:
                            raw_b64 = await _chat_image_edit(f"/images/edits: {no_image_detail}")
                    if not raw_b64:
                        progress("failed", f"{provider_label} returned no image.", percent=100, done=True, error=True)
                        raise HTTPException(502, f"{provider_label} returned no image")

                    # OpenAI's edits API doesn't truly preserve unmasked
                    # pixels — gpt-image-1 regenerates the whole image,
                    # so even areas the user didn't mask come back
                    # slightly different. Composite the model output onto
                    # the ORIGINAL source using the user's mask, so only
                    # the masked region actually changes.
                    try:
                        progress("composite", "Compositing edited pixels into the masked region.", percent=84)
                        generated = Image.open(io.BytesIO(base64.b64decode(raw_b64))).convert("RGBA")
                        # Match the generated image to the source dims.
                        if generated.size != source_png.size:
                            generated = generated.resize(source_png.size, Image.LANCZOS)
                        # mask_png: white = regenerate (use generated),
                        #           black = keep (use source).
                        # Composite: result = source * (1 - mask_norm) + generated * mask_norm
                        # Image.composite does exactly that with `mask`.
                        blended = Image.composite(generated, source_png, mask_png)
                        out_buf = io.BytesIO()
                        blended.save(out_buf, format="PNG")
                        progress("backend_complete", "Backend response is ready.", percent=88, done=True)
                        return {"image": base64.b64encode(out_buf.getvalue()).decode()}
                    except Exception as comp_err:
                        # If compositing fails for any reason, fall back
                        # to the raw OpenAI output rather than blocking.
                        logger.warning(f"Inpaint compose failed, returning raw: {comp_err}")
                        progress("backend_complete", "Backend response is ready; returning raw provider image.", percent=88, done=True)
                        return {"image": raw_b64}
            except httpx.TimeoutException:
                progress("failed", "OpenAI inpaint timed out.", percent=100, done=True, error=True)
                raise HTTPException(504, "OpenAI inpaint timed out (120s)")

        # Self-hosted diffusion server path
        try:
            # Forward chosen_model so the diffusion server can route if it ever
            # supports multiple models per process. Harmless if ignored.
            if chosen_model:
                body["model"] = chosen_model
            progress("diffusion_prepare", "Preparing request for the local image endpoint.", percent=60)
            async with httpx.AsyncClient(timeout=240) as client:
                last_error = ""
                paths = ("/images/inpaint", "/images/edits", "/images/edit", "/api/image/inpaint")
                for idx, path in enumerate(paths):
                    target = f"{base_root}{path}" if path.startswith("/api/") else f"{base}{path}"
                    payload = dict(body)
                    if chosen_model:
                        payload["model"] = chosen_model
                    if path in {"/images/edits", "/images/edit"}:
                        if payload.get("mask") and not payload.get("mask_image"):
                            payload["mask_image"] = payload["mask"]
                        payload.setdefault("response_format", "b64_json")
                        payload.setdefault("output_format", "png")
                        payload.setdefault("n", 1)
                    try:
                        progress("model_wait", f"Trying {path} on the image endpoint.", percent=64 + idx * 3)
                        r = await client.post(target, json=payload)
                    except httpx.TimeoutException:
                        raise
                    except Exception as exc:
                        last_error = f"{path}: {exc}"
                        progress("route_retry", f"{path} did not connect; trying next route.", percent=66 + idx * 3)
                        continue
                    if r.status_code < 200 or r.status_code >= 300:
                        last_error = f"{path}: HTTP {r.status_code}: {r.text[:300]}"
                        progress("route_retry", f"{path} returned HTTP {r.status_code}; trying next route.", percent=66 + idx * 3)
                        continue
                    progress("normalize_response", f"{path} returned a response; extracting image data.", percent=78)
                    image_b64, no_image_detail = await _normalized_provider_image_response(r.text, client)
                    if image_b64:
                        progress("backend_complete", "Backend response is ready.", percent=88, done=True)
                        return {"image": image_b64}
                    last_error = f"{path}: {no_image_detail}"
                    progress("route_retry", f"{path} returned no image; trying next route.", percent=76)
                raise HTTPException(
                    502,
                    f"No compatible inpaint route worked on {base}. Last error: {last_error[:300] if last_error else 'none'}",
                )
        except httpx.TimeoutException:
            progress("failed", "Inpaint request timed out.", percent=100, done=True, error=True)
            raise HTTPException(504, "Inpaint request timed out (240s)")
        except HTTPException as exc:
            progress("failed", str(exc.detail), percent=100, done=True, error=True)
            raise
        except Exception as e:
            progress("failed", f"Inpaint error: {str(e)}", percent=100, done=True, error=True)
            raise HTTPException(502, f"Inpaint error: {str(e)}")

    # ---- POST /api/image/harmonize — proper img2img call ----
    # Earlier version routed through inpaint with a full-white mask, but
    # most backends interpret "100% mask coverage" as "regenerate from
    # scratch using the prompt", ignoring the source. Real img2img sends
    # the image alongside a `strength` (denoising strength) and the model
    # mixes that fraction of new noise into the existing pixels.
    @router.post("/api/image/harmonize")
    async def harmonize_image(request: Request):
        """Harmonize = img2img. The model preserves (1 - strength) of the
        original and regenerates `strength` fraction. With strength ~0.4
        you get edge blending + lighting unification while keeping the
        composition recognisable."""
        import httpx, base64 as _b64
        user = require_privilege(request, "can_generate_images")
        body = await request.json()

        image_b64 = body.get("image")
        if not image_b64:
            raise HTTPException(400, "No image provided")

        endpoint_id = (body.get("_endpoint_id") or "").strip()
        endpoint = (body.get("_endpoint") or "").rstrip("/")
        # SSRF hardening: a client-supplied endpoint is fetched server-side
        # below, so validate it first (mirrors routes/embedding_routes.py).
        # Local-first means loopback/LAN is allowed by default; the cloud
        # metadata range and non-HTTP(S) schemes are always rejected.
        if endpoint and not endpoint_id:
            from src.url_safety import check_outbound_url
            ok, reason = check_outbound_url(
                endpoint,
                block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
            )
            if not ok:
                raise HTTPException(400, f"Rejected endpoint URL: {reason}")
        model = (body.get("_model") or "").strip()

        base = endpoint
        api_key = None
        if endpoint_id:
            db = SessionLocal()
            try:
                ep = _visible_image_endpoint_for_id(db, endpoint_id, user)
                if not ep:
                    raise HTTPException(403, "Choose a registered image endpoint")
                base = ep.base_url.rstrip("/")
                api_key = ep.api_key
            finally:
                db.close()
        elif not base:
            db = SessionLocal()
            try:
                ep = _first_visible_image_endpoint(db, user)
                if not ep:
                    raise HTTPException(400, "No image generation endpoint configured.")
                base = ep.base_url.rstrip("/")
                api_key = ep.api_key
            finally:
                db.close()
        else:
            db = SessionLocal()
            try:
                ep = _visible_image_endpoint_for_base(db, base, user)
                if ep:
                    base = (ep.base_url or base).rstrip("/")
                    api_key = ep.api_key
                elif user and not _current_user_is_admin(request, user):
                    raise HTTPException(403, "Choose a registered image endpoint")
            finally:
                db.close()

        if not base.endswith("/v1"):
            base += "/v1"

        prompt = body.get("prompt") or "natural lighting, harmonious color, seamless blend"
        # Legacy single-strength control (old clients) → maps to color_match
        strength = body.get("strength", 0.45)
        try:
            strength = float(strength)
        except Exception:
            strength = 0.45
        strength = max(0.05, min(0.95, strength))
        # New two-stage controls. Clients may send either color_match/seam_fix
        # explicitly, or fall back to strength→color_match for legacy.
        try:
            color_match = float(body.get("color_match", strength))
        except Exception:
            color_match = strength
        try:
            seam_fix = float(body.get("seam_fix", 0.0))
        except Exception:
            seam_fix = 0.0
        color_match = max(0.0, min(1.0, color_match))
        seam_fix = max(0.0, min(1.0, seam_fix))
        body_mask_b64 = body.get("body_mask") or body.get("mask")
        seam_mask_b64 = body.get("seam_mask")

        # OpenAI's image API has no img2img mode — its edits endpoint
        # regenerates pixels from the prompt rather than preserving the
        # source. Earlier hack (alpha-blend the regen back at `strength`)
        # produced visibly broken results, so we refuse and tell the
        # user to spin up a real diffusion endpoint instead.
        if "api.openai.com" in base:
            raise HTTPException(400,
                "Harmonize needs a diffusion server that supports img2img "
                "(SD WebUI / Forge / Comfy). OpenAI's API doesn't expose "
                "one. Cookbook → Models can serve an SD-compatible model "
                "locally in a few clicks.")

        # Try img2img-shaped routes in order. Most self-hosted servers
        # expose at least one of these. Whatever returns 200 wins.
        # /images/harmonize is our own diffusion_server.py's native endpoint —
        # try it first since it's purpose-built for this and tolerates models
        # that only ship an inpaint pipeline.
        harmonize_payload = {
            "image": image_b64,
            "prompt": prompt,
            "color_match": color_match,
            "seam_fix": seam_fix,
            # Legacy field names so an un-restarted older diffusion server
            # still recognises the body mask. The new server prefers
            # `body_mask` over `mask`, so sending both is safe.
            "strength": color_match,
        }
        if body_mask_b64:
            harmonize_payload["body_mask"] = body_mask_b64
            harmonize_payload["mask"] = body_mask_b64
        if seam_mask_b64:
            harmonize_payload["seam_mask"] = seam_mask_b64

        candidates = [
            ("/images/harmonize", "json", harmonize_payload),
            ("/images/img2img", "json", {
                "image": image_b64,
                "prompt": prompt,
                "strength": strength,
                **({"model": model} if model else {}),
            }),
            ("/images/variations", "json", {
                "image": image_b64,
                "prompt": prompt,
                "strength": strength,
                **({"model": model} if model else {}),
            }),
            # Last-resort fallback: AUTOMATIC1111-style sdapi route.
            ("/sdapi/v1/img2img", "json_a1111", {
                "init_images": [f"data:image/png;base64,{image_b64}"],
                "prompt": prompt,
                "denoising_strength": strength,
                "steps": 30,
                **({"override_settings": {"sd_model_checkpoint": model}} if model else {}),
            }),
        ]

        # Strip the /v1 for the AUTOMATIC1111 path which uses /sdapi/v1/...
        base_root = base[:-3] if base.endswith("/v1") else base

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        last_err = None
        # Cold-start SDXL inpaint can take 60-90s on first request (loading
        # weights to GPU). 240s gives headroom for both that and a full
        # 1024×1024 inference pass on slower setups.
        async with httpx.AsyncClient(timeout=240) as client:
            for path, kind, payload in candidates:
                target = base_root + path if path.startswith("/sdapi") else base + path
                try:
                    r = await client.post(target, json=payload, headers=headers)
                    if r.status_code == 404:
                        last_err = f"{path}: 404"
                        continue  # try next variant
                    if r.status_code != 200:
                        last_err = f"{path}: {r.status_code} {r.text[:120]}"
                        continue
                    data = r.json()
                    # Normalise return shape.
                    if isinstance(data, dict):
                        # Server returned 200 with an explicit error field —
                        # surface it now instead of trying the other routes
                        # (otherwise the real error gets buried under 404s).
                        if data.get("error") and not data.get("image"):
                            raise HTTPException(502,
                                f"Diffusion server error at {path}: {data['error']}")
                        if data.get("image"):
                            return {"image": data["image"]}
                        if data.get("images") and isinstance(data["images"], list):
                            img0 = data["images"][0]
                            if isinstance(img0, str):
                                # A1111 sometimes returns "data:image/png;base64,..." prefix
                                if img0.startswith("data:"):
                                    img0 = img0.split(",", 1)[1]
                                return {"image": img0}
                        # OpenAI-style {"data":[{"b64_json": ...}]}
                        if data.get("data"):
                            item = data["data"][0]
                            if item.get("b64_json"):
                                return {"image": item["b64_json"]}
                            if item.get("url"):
                                async with httpx.AsyncClient(timeout=60) as c2:
                                    ir = await c2.get(item["url"])
                                    if ir.status_code == 200:
                                        return {"image": _b64.b64encode(ir.content).decode()}
                    last_err = f"{path}: server returned no image"
                except httpx.ConnectError as e:
                    raise HTTPException(502, f"Can't reach diffusion server at {base}: {e}")
                except httpx.TimeoutException:
                    raise HTTPException(504, "Harmonize timed out (240s) — restart the diffusion server or lower Color match / disable Seam fix")
        raise HTTPException(502,
            f"None of the img2img routes worked on {base}. "
            f"Last response: {last_err or 'unknown'}. "
            "Your diffusion server needs to expose one of /v1/images/harmonize, "
            "/v1/images/img2img, /v1/images/variations, or /sdapi/v1/img2img.")

    # ---- POST /api/image/sharpen ----
    @router.post("/api/image/sharpen")
    async def sharpen_image(request: Request):
        """Apply unsharp-mask sharpening to an image."""
        require_privilege(request, "can_generate_images")
        body = await request.json()
        image_b64 = body.get("image")
        amount = body.get("amount", 50) / 100.0

        from PIL import Image, ImageFilter
        import base64, io

        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

        # Unsharp mask: radius=2, percent=amount*200, threshold=3
        sharpened = img.filter(ImageFilter.UnsharpMask(radius=2, percent=int(amount * 200), threshold=3))

        buf = io.BytesIO()
        sharpened.save(buf, format="PNG")
        return {"image": base64.b64encode(buf.getvalue()).decode()}

    # ---- POST /api/image/denoise ----
    # AI denoise via Real-ESRGAN with the realesr-general-x4v3 weights at
    # outscale=1 + denoise_strength. Falls back to a "package missing"
    # error so the client can prompt the user to install via Cookbook.
    @router.post("/api/image/denoise")
    async def denoise_image(request: Request):
        require_privilege(request, "can_generate_images")
        body = await request.json()
        image_b64 = body.get("image")
        if not image_b64:
            raise HTTPException(400, "No image provided")
        try:
            strength = float(body.get("strength", 0.5))
        except Exception:
            strength = 0.5
        strength = max(0.0, min(1.0, strength))
        try:
            import base64, io
            from PIL import Image
            import numpy as np
        except ImportError as e:
            raise HTTPException(500, f"Server missing dependency: {e}")
        # Decode source image (RGB; Real-ESRGAN doesn't preserve alpha).
        img_bytes = base64.b64decode(image_b64)
        src = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        try:
            from realesrgan import RealESRGANer
        except ImportError:
            return {"error": "realesrgan not installed. Install it from Cookbook → Dependencies (search 'realesrgan')."}
        try:
            # General-purpose lightweight model with denoise control.
            from realesrgan.archs.srvgg_arch import SRVGGNetCompact
            model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64,
                                    num_conv=32, upscale=4, act_type='prelu')
            upsampler = RealESRGANer(
                scale=4,
                model_path='https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth',
                dni_weight=[strength, 1.0 - strength],
                model=model,
                tile=400, tile_pad=10, pre_pad=0, half=False,
            )
            arr = np.array(src)
            output, _ = upsampler.enhance(arr, outscale=1)
            out_img = Image.fromarray(output)
            buf = io.BytesIO()
            out_img.save(buf, format="PNG")
            return {"image": base64.b64encode(buf.getvalue()).decode()}
        except Exception as e:
            logger.warning(f"Denoise failed: {e}")
            return {"error": f"Denoise failed: {e}"}

    # ---- POST /api/image/upscale-local ----
    # Local Real-ESRGAN upscale (2× or 4×). Self-contained — no diffusion
    # server required. Used by the editor's AI Upscale button.
    @router.post("/api/image/upscale-local")
    async def upscale_image_local(request: Request):
        require_privilege(request, "can_generate_images")
        body = await request.json()
        image_b64 = body.get("image")
        if not image_b64:
            raise HTTPException(400, "No image provided")
        try:
            scale = int(body.get("scale", 2))
        except Exception:
            scale = 2
        scale = 2 if scale not in (2, 4) else scale
        try:
            import base64, io
            from PIL import Image
            import numpy as np
        except ImportError as e:
            raise HTTPException(500, f"Server missing dependency: {e}")
        img_bytes = base64.b64decode(image_b64)
        src = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        try:
            from basicsr.archs.rrdbnet_arch import RRDBNet
            from realesrgan import RealESRGANer
        except ImportError:
            return {"error": "realesrgan not installed. Install it from Cookbook → Dependencies (search 'realesrgan')."}
        try:
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                            num_block=23, num_grow_ch=32, scale=4)
            upsampler = RealESRGANer(
                scale=4,
                model_path='https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth',
                model=model,
                tile=400, tile_pad=10, pre_pad=0, half=False,
            )
            arr = np.array(src)
            output, _ = upsampler.enhance(arr, outscale=scale)
            out_img = Image.fromarray(output)
            buf = io.BytesIO()
            out_img.save(buf, format="PNG")
            return {"image": base64.b64encode(buf.getvalue()).decode()}
        except Exception as e:
            logger.warning(f"Upscale failed: {e}")
            return {"error": f"Upscale failed: {e}"}

    # ---- POST /api/image/remove-bg ----
    @router.post("/api/image/remove-bg")
    async def remove_background(request: Request):
        """Remove background from an image. If the client passes a `hint_mask`
        (white-where-the-user-wants-the-subject PNG, same dims as the
        image), we constrain the output:

          1. Crop the image to the mask's bounding box (with padding) so
             the model only sees the region the user cares about.
          2. Run rembg on that crop.
          3. Paste the result back at the original offset.
          4. Multiply the final alpha by the user's mask, so anything
             outside the hint becomes transparent regardless of what the
             model thought was foreground.
        """
        user = require_privilege(request, "can_generate_images")
        body = await request.json()
        image_b64 = body.get("image")
        hint_b64 = body.get("hint_mask")
        background_b64 = (
            body.get("background_mask")
            or body.get("bg_hint_mask")
            or body.get("background_hint_mask")
        )
        try:
            bg_strength = float(body.get("strength", body.get("bg_strength", 0.7)))
        except Exception:
            bg_strength = 0.7
        if bg_strength > 1:
            bg_strength = bg_strength / 100.0
        bg_strength = max(0.1, min(1.0, bg_strength))
        known_rembg_models = {"u2netp", "silueta", "isnet-general-use"}
        selected_endpoint_id = str(body.get("_endpoint_id") or "").strip()
        selected_endpoint = str(body.get("_endpoint") or "").strip()
        selected_model = str(body.get("_model") or "").strip()
        requested_rembg_model = str(
            body.get("_rembg_model")
            or body.get("rembg_model")
            or (selected_model if selected_model in known_rembg_models else "")
            or ""
        ).strip()
        raw_pipeline = str(
            body.get("bg_remove_pipeline")
            or body.get("bgremove_pipeline")
            or body.get("pipeline")
            or body.get("_pipeline")
            or "auto"
        ).strip().lower().replace("_", "-")
        if raw_pipeline in {"provider", "api", "local", "local-model", "local-models", "image-model", "image-models", "model"}:
            bg_remove_pipeline = "model"
        elif raw_pipeline in {"natural", "native", "ml", "rembg", "rembg-natural"}:
            bg_remove_pipeline = "rembg"
        elif raw_pipeline in {"heuristic", "sample", "sampled", "sampled-background", "color", "colour", "color-match", "colour-match"}:
            bg_remove_pipeline = "heuristic"
        else:
            bg_remove_pipeline = "auto"

        from PIL import Image
        import base64, io

        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        W, H = img.size

        hint = None
        bbox = None
        if hint_b64:
            try:
                hint_bytes = base64.b64decode(hint_b64)
                hint = Image.open(io.BytesIO(hint_bytes)).convert("L")
                # Resize the hint to match if dimensions disagree
                if hint.size != img.size:
                    hint = hint.resize(img.size, Image.NEAREST)
                # Bounding box of any non-zero pixel (with 8 px padding)
                bbox = hint.getbbox()
                if bbox:
                    pad = 8
                    bbox = (
                        max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                        min(W, bbox[2] + pad), min(H, bbox[3] + pad),
                    )
            except Exception:
                hint = None
                bbox = None

        background_hint = None
        if background_b64:
            try:
                background_bytes = base64.b64decode(background_b64)
                background_hint = Image.open(io.BytesIO(background_bytes)).convert("L")
                if background_hint.size != img.size:
                    background_hint = background_hint.resize(img.size, Image.NEAREST)
            except Exception:
                background_hint = None

        def _installed_rembg_model(model_name):
            if model_name == "u2netp":
                return True
            try:
                from routes.shell_routes import _rembg_model_path
                path = _rembg_model_path(model_name)
                return bool(path and path.exists() and path.is_file() and path.stat().st_size > 0)
            except Exception:
                return False

        def _preferred_rembg_models():
            if requested_rembg_model in {"u2netp", "silueta", "isnet-general-use"}:
                return [requested_rembg_model]
            preferred = [
                model_name
                for model_name in ("isnet-general-use", "silueta")
                if _installed_rembg_model(model_name)
            ]
            preferred.append("u2netp")
            return preferred

        _rembg_sessions = {}

        def _remove_with_preferred_rembg(src_img):
            from rembg import new_session, remove
            last_error = None
            for model_name in _preferred_rembg_models():
                try:
                    session = _rembg_sessions.get(model_name)
                    if session is None:
                        session = new_session(model_name)
                        _rembg_sessions[model_name] = session
                    return remove(src_img, session=session)
                except Exception as exc:
                    last_error = exc
            if last_error:
                raise last_error
            return remove(src_img)

        def _strip_data_url(value):
            value = str(value or "").strip()
            if "," in value and value.lower().startswith("data:"):
                return value.split(",", 1)[1]
            return value

        def _image_value_from_text(value):
            import json as _json
            import re as _re
            text = str(value or "").strip()
            match = _re.search(r"data:image/(?:png|jpe?g|webp);base64,[A-Za-z0-9+/=\s]+", text, _re.I)
            if match:
                return match.group(0)
            match = _re.search(r'"(?:b64_json|image|base64|image_base64|url)"\s*:\s*"([^"]{128,})"', text, _re.I)
            if match:
                candidate = match.group(1).replace("\\/", "/").replace("\\n", "").replace("\\r", "")
                if candidate.lower().startswith(("data:image/", "http://", "https://")):
                    return candidate
                clean = _strip_data_url(candidate)
                if clean.startswith(("iVBOR", "/9j/", "UklGR")) or len(clean) > 128:
                    return candidate
            if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
                try:
                    return _first_provider_image_value(_json.loads(text), 1)
                except Exception:
                    return ""
            return ""

        def _first_provider_image_value(node, depth=0):
            if node is None or depth > 5:
                return ""
            if isinstance(node, str):
                value = node.strip()
                lower = value.lower()
                if lower.startswith(("data:image/", "http://", "https://")):
                    return value
                b64 = _strip_data_url(value)
                if b64.startswith(("iVBOR", "/9j/", "UklGR")) or len(b64) > 128:
                    return value
                embedded = _image_value_from_text(value)
                if embedded:
                    return embedded
                return ""
            if isinstance(node, list):
                for item in node:
                    found = _first_provider_image_value(item, depth + 1)
                    if found:
                        return found
                return ""
            if not isinstance(node, dict):
                return ""
            preferred = [
                "image", "b64_json", "base64", "image_base64", "imageBase64",
                "url", "image_url", "imageUrl", "data", "images",
                "content", "message", "choices", "output", "outputs", "result", "results", "artifact", "artifacts",
            ]
            for key in preferred:
                if key in node:
                    found = _first_provider_image_value(node.get(key), depth + 1)
                    if found:
                        return found
            for value in node.values():
                found = _first_provider_image_value(value, depth + 1)
                if found:
                    return found
            return ""

        def _provider_error_text(node, depth=0):
            if node is None or depth > 4:
                return ""
            if isinstance(node, str):
                text = node.replace("\n", " ").strip()
                return "" if _first_provider_image_value(text) else text[:220]
            if isinstance(node, list):
                for item in node:
                    found = _provider_error_text(item, depth + 1)
                    if found:
                        return found
                return ""
            if not isinstance(node, dict):
                return ""
            for key in ("error", "detail", "message", "reason", "status_message"):
                if key in node:
                    found = _provider_error_text(node.get(key), depth + 1)
                    if found:
                        return found
            return ""

        def _provider_no_image_detail(node):
            err = _provider_error_text(node)
            if err:
                return err
            if isinstance(node, dict):
                keys = ", ".join(str(k) for k in list(node.keys())[:10])
                return f"server returned no image (keys: {keys})" if keys else "server returned no image"
            return "server returned no image"

        def _openai_edit_size(width, height):
            if width > height * 1.15:
                return "1536x1024"
            if height > width * 1.15:
                return "1024x1536"
            return "1024x1024"

        def _wants_json_image_edit(status_code, text):
            lower = str(text or "").lower()
            return (
                status_code == 415
                or ("unsupported media type" in lower and "application/json" in lower)
                or "post requests must use 'application/json'" in lower
                or "post requests must use application/json" in lower
            )

        def _apply_hint_to_provider_result(result_b64):
            clean = _strip_data_url(result_b64)
            if hint is None:
                return clean
            try:
                from PIL import ImageChops
                out = Image.open(io.BytesIO(base64.b64decode(clean))).convert("RGBA")
                if out.size != img.size:
                    out = out.resize(img.size, Image.LANCZOS)
                r, g, b, a = out.split()
                a = ImageChops.multiply(a, hint)
                out = Image.merge("RGBA", (r, g, b, a))
                buf = io.BytesIO()
                out.save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode()
            except Exception:
                return clean

        def _provider_result_has_transparency(result_b64):
            try:
                out = Image.open(io.BytesIO(base64.b64decode(_strip_data_url(result_b64)))).convert("RGBA")
                alpha = out.split()[3]
                values = alpha.getdata()
                transparentish = 0
                min_alpha = 255
                for value in values:
                    if value < min_alpha:
                        min_alpha = value
                    if value < 245:
                        transparentish += 1
                count = max(1, out.width * out.height)
                return min_alpha < 245 and transparentish >= max(16, count // 1000)
            except Exception:
                return False

        def _model_prefers_openai_edit(model_name):
            m = str(model_name or "").lower()
            if "dall-e-3" in m:
                return False
            return (
                "gpt-image" in m
                or "chatgpt-image" in m
                or "dall-e-2" in m
                or ("qwen" in m and "image" in m and any(token in m for token in ("edit", "inpaint", "fill")))
                or ("seedream" in m and any(token in m for token in ("edit", "inpaint", "fill")))
                or "kontext" in m
                or "inpaint" in m
                or "edit" in m
                or "fill" in m
            )

        async def _provider_image_value_to_b64(value, client):
            value = str(value or "").strip()
            lower = value.lower()
            if lower.startswith(("http://", "https://")):
                r = await client.get(value)
                if r.status_code != 200:
                    raise HTTPException(502, f"Image model returned URL that could not be downloaded: HTTP {r.status_code}")
                return base64.b64encode(r.content).decode()
            return _strip_data_url(value)

        async def _remove_with_provider():
            import httpx

            base = selected_endpoint.rstrip("/")
            model = "" if selected_model in known_rembg_models else selected_model
            api_key = None
            if selected_endpoint_id:
                db = SessionLocal()
                try:
                    ep = _visible_image_endpoint_for_id(db, selected_endpoint_id, user)
                    if not ep:
                        raise HTTPException(403, "Choose a registered image endpoint")
                    base = ep.base_url.rstrip("/")
                    api_key = ep.api_key
                finally:
                    db.close()
            elif base:
                from src.url_safety import check_outbound_url
                ok, reason = check_outbound_url(
                    base,
                    block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
                )
                if not ok:
                    raise HTTPException(400, f"Rejected endpoint URL: {reason}")
                db = SessionLocal()
                try:
                    ep = _visible_image_endpoint_for_base(db, base, user)
                    if ep:
                        base = (ep.base_url or base).rstrip("/")
                        api_key = ep.api_key
                    elif user and not _current_user_is_admin(request, user):
                        raise HTTPException(403, "Choose a registered image endpoint")
                finally:
                    db.close()
            else:
                db = SessionLocal()
                try:
                    ep = _first_visible_image_endpoint(db, user)
                    if not ep:
                        raise HTTPException(400, "No image endpoint configured for background removal.")
                    base = ep.base_url.rstrip("/")
                    api_key = ep.api_key
                    if not model:
                        models = getattr(ep, "models", None) or []
                        if isinstance(models, str):
                            try:
                                import json as _json
                                models = _json.loads(models)
                            except Exception:
                                models = []
                        model = next((str(m) for m in models if m), "")
                finally:
                    db.close()

            if not base.endswith("/v1"):
                base += "/v1"
            base_root = base[:-3].rstrip("/") if base.endswith("/v1") else base.rstrip("/")
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            prompt = (
                "Remove the background and return a transparent PNG. Preserve the foreground subject exactly, "
                "including faces, fur, hair strands, whiskers, clothing edges, and fine detail."
            )
            if background_hint is not None:
                prompt += (
                    " A separate background_mask marks user-painted background samples. Treat those pixels as "
                    "background guidance, but do not remove matching foreground detail unless it is directly marked."
                )
            payload = {
                "image": image_b64,
                "prompt": prompt,
                "response_format": "b64_json",
                "strength": bg_strength,
            }
            if model:
                payload["model"] = model
            if hint_b64:
                payload["hint_mask"] = hint_b64
            if background_b64:
                payload["background_mask"] = background_b64

            timeout = httpx.Timeout(connect=20.0, read=240.0, write=30.0, pool=20.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                last_err = None
                openai_style_edit = "api.openai.com" in base or _model_prefers_openai_edit(model)
                async def _chat_image_edit(previous_error=""):
                    chat_payload = {
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                            ],
                        }],
                        "stream": False,
                        "extra_body": {
                            "num_inference_steps": 50,
                            "guidance_scale": 1,
                            "size": _openai_edit_size(W, H),
                            "output_format": "png",
                        },
                    }
                    if model:
                        chat_payload["model"] = model
                    cr = await client.post(f"{base}/chat/completions", headers=headers, json=chat_payload)
                    if cr.status_code < 200 or cr.status_code >= 300:
                        suffix = f" Previous /v1/images/edits error: {previous_error[:180]}" if previous_error else ""
                        raise HTTPException(
                            cr.status_code,
                            f"Image edit chat fallback failed at /v1/chat/completions: {cr.text[:300]}{suffix}",
                        )
                    try:
                        data_json = cr.json()
                    except Exception:
                        suffix = f" Previous /v1/images/edits error: {previous_error[:180]}" if previous_error else ""
                        raise HTTPException(
                            502,
                            f"Image edit chat fallback returned invalid JSON: {cr.text[:220]}{suffix}",
                        )
                    found = _first_provider_image_value(data_json)
                    if not found:
                        suffix = f" Previous /v1/images/edits error: {previous_error[:180]}" if previous_error else ""
                        raise HTTPException(
                            502,
                            f"Image edit chat fallback returned no image: {_provider_no_image_detail(data_json)}{suffix}",
                        )
                    return _apply_hint_to_provider_result(await _provider_image_value_to_b64(found, client))

                if openai_style_edit:
                    if not api_key:
                        if "api.openai.com" in base:
                            raise HTTPException(400, "OpenAI endpoint has no api_key stored - edit it in Endpoints settings.")
                    mask = Image.new("RGBA", (W, H), (255, 255, 255, 0))
                    mask_buf = io.BytesIO()
                    mask.save(mask_buf, format="PNG")
                    data = {
                        "model": model or "gpt-image-1",
                        "prompt": prompt,
                        "size": _openai_edit_size(W, H),
                        "n": "1",
                    }
                    files = {
                        "image": ("source.png", img_bytes, "image/png"),
                        "mask": ("mask.png", mask_buf.getvalue(), "image/png"),
                    }
                    r = await client.post(f"{base}/images/edits", headers=headers, data=data, files=files)
                    if r.status_code < 200 or r.status_code >= 300:
                        last_err = f"/images/edits: HTTP {r.status_code}: {r.text[:300]}"
                        if "api.openai.com" in base:
                            raise HTTPException(r.status_code, f"OpenAI background remove failed: {r.text[:300]}")
                        if _wants_json_image_edit(r.status_code, r.text):
                            mask_b64 = base64.b64encode(mask_buf.getvalue()).decode()
                            json_payload = {
                                "model": model or "gpt-image-1",
                                "prompt": prompt,
                                "image": image_b64,
                                "mask_image": mask_b64,
                                "size": _openai_edit_size(W, H),
                                "n": 1,
                                "response_format": "b64_json",
                                "output_format": "png",
                            }
                            jr = await client.post(f"{base}/images/edits", headers=headers, json=json_payload)
                            if jr.status_code < 200 or jr.status_code >= 300:
                                last_err = f"/images/edits JSON: HTTP {jr.status_code}: {jr.text[:300]}"
                                return await _chat_image_edit(last_err)
                            data_json = jr.json()
                            found = _first_provider_image_value(data_json)
                            if not found:
                                last_err = f"/images/edits JSON: {_provider_no_image_detail(data_json)}"
                                return await _chat_image_edit(last_err)
                            return _apply_hint_to_provider_result(await _provider_image_value_to_b64(found, client))
                        return await _chat_image_edit(last_err)
                    else:
                        data_json = r.json()
                        found = _first_provider_image_value(data_json)
                        if not found:
                            last_err = f"/images/edits: {_provider_no_image_detail(data_json)}"
                            if "api.openai.com" in base:
                                raise HTTPException(502, "OpenAI returned no image")
                            return await _chat_image_edit(last_err)
                        else:
                            return _apply_hint_to_provider_result(await _provider_image_value_to_b64(found, client))

                for path in ("/images/remove-bg", "/images/background-remove", "/images/rembg"):
                    target = base_root + path if path.startswith("/api/") else base + path
                    try:
                        r = await client.post(target, json=payload, headers=headers)
                    except httpx.TimeoutException:
                        raise HTTPException(504, "Image model background removal timed out")
                    except httpx.ConnectError as exc:
                        raise HTTPException(502, f"Can't reach image endpoint at {base}: {exc}")
                    if r.status_code == 404:
                        last_err = f"{path}: 404"
                        continue
                    if r.status_code < 200 or r.status_code >= 300:
                        try:
                            err_json = r.json()
                            err_text = _provider_error_text(err_json)
                        except Exception:
                            err_text = r.text[:220]
                        last_err = f"{path}: HTTP {r.status_code}{': ' + err_text if err_text else ''}"
                        continue
                    try:
                        data_json = r.json()
                    except Exception:
                        last_err = f"{path}: response was not JSON"
                        continue
                    if isinstance(data_json, dict) and data_json.get("error") and not _first_provider_image_value(data_json):
                        last_err = f"{path}: {_provider_error_text(data_json) or data_json.get('error')}"
                        continue
                    found = _first_provider_image_value(data_json)
                    if not found:
                        last_err = f"{path}: server returned no image"
                        continue
                    return _apply_hint_to_provider_result(await _provider_image_value_to_b64(found, client))
                raise HTTPException(
                    502,
                    "Selected image endpoint does not expose a background-remove route. "
                    f"Last response: {last_err or 'unknown'}",
                )

        def _subject_keep_mask(src_img, sample_mask, allow_model=True):
            if sample_mask is None:
                return None
            try:
                from PIL import ImageChops, ImageFilter
                keep = None
                if allow_model:
                    try:
                        cut = _remove_with_preferred_rembg(src_img)
                        keep = cut.convert("RGBA").split()[3]
                    except Exception:
                        try:
                            from transformers import pipeline
                            pipe = pipeline("image-segmentation", model="briaai/RMBG-1.4", trust_remote_code=True)
                            keep = pipe(src_img, return_mask=True).convert("L")
                        except Exception:
                            keep = None
                if keep is None:
                    keep = _portrait_heuristic_keep_mask(src_img)
                if keep is None:
                    return None
                if keep.size != src_img.size:
                    keep = keep.resize(src_img.size, Image.NEAREST)
                sample_override = sample_mask.filter(ImageFilter.MaxFilter(9))
                keep = keep.point(lambda v: 255 if v > 18 else 0).filter(ImageFilter.MaxFilter(3))
                keep = ImageChops.subtract(keep, sample_override)
                return keep if keep.getbbox() else None
            except Exception:
                return None

        def _portrait_heuristic_keep_mask(src_img):
            try:
                import numpy as np
                from PIL import ImageFilter
            except Exception:
                return None
            arr = np.array(src_img.convert("RGBA"))
            r = arr[:, :, 0].astype(np.int16)
            g = arr[:, :, 1].astype(np.int16)
            b = arr[:, :, 2].astype(np.int16)
            a = arr[:, :, 3]
            maxc = np.maximum.reduce([r, g, b])
            minc = np.minimum.reduce([r, g, b])
            skin = (
                (a > 8) &
                (r > 55) & (g > 35) & (b > 18) &
                ((maxc - minc) > 12) &
                (r > b) &
                ((r - g) > -8)
            )
            if int(skin.sum()) < 24:
                return None
            from collections import deque
            visited = np.zeros_like(skin, dtype=bool)
            best = None
            best_score = 0.0
            height, width = skin.shape
            for start in np.flatnonzero(skin):
                sy, sx = divmod(int(start), width)
                if visited[sy, sx]:
                    continue
                q = deque([int(start)])
                visited[sy, sx] = True
                points = []
                min_x = max_x = sx
                min_y = max_y = sy
                while q:
                    idx = q.popleft()
                    y, x = divmod(idx, width)
                    points.append(idx)
                    min_x = min(min_x, x)
                    max_x = max(max_x, x)
                    min_y = min(min_y, y)
                    max_y = max(max_y, y)
                    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                        ny, nx = y + dy, x + dx
                        if ny < 0 or ny >= height or nx < 0 or nx >= width:
                            continue
                        if visited[ny, nx] or not skin[ny, nx]:
                            continue
                        visited[ny, nx] = True
                        q.append(ny * width + nx)
                area = len(points)
                if area < 24:
                    continue
                comp_w = max(1, max_x - min_x + 1)
                comp_h = max(1, max_y - min_y + 1)
                touches_edge = min_x <= 1 or max_x >= width - 2 or min_y <= 1 or max_y >= height - 2
                score = float(area)
                if touches_edge:
                    score *= 0.08
                if comp_w > width * 0.58 or comp_h > height * 0.72:
                    score *= 0.18
                if comp_w * comp_h > width * height * 0.32:
                    score *= 0.15
                center_favor = 1.0 - min(1.0, abs(((min_x + max_x) / 2.0) - width / 2.0) / max(1.0, width / 2.0))
                score *= 0.55 + center_favor * 0.45
                if score > best_score:
                    best_score = score
                    best = points
            if not best:
                return None
            skin = np.zeros_like(skin, dtype=bool)
            for idx in best:
                y, x = divmod(idx, width)
                skin[y, x] = True
            ys, xs = np.nonzero(skin)
            y1, y2 = int(ys.min()), int(ys.max())
            x1, x2 = int(xs.min()), int(xs.max())
            pad_x = max(10, int((x2 - x1 + 1) * 0.55))
            pad_top = max(14, int((y2 - y1 + 1) * 0.95))
            pad_bot = max(8, int((y2 - y1 + 1) * 0.45))
            rx1 = max(0, x1 - pad_x)
            rx2 = min(width - 1, x2 + pad_x)
            ry1 = max(0, y1 - pad_top)
            ry2 = min(height - 1, y2 + pad_bot)
            roi = np.zeros_like(skin)
            roi[ry1:ry2 + 1, rx1:rx2 + 1] = True
            brightness = (r + g + b) / 3.0
            dark_hair = (brightness < 95) & ((maxc - minc) < 70)
            brown_hair = (r >= g - 12) & (g >= b - 18) & (brightness >= 45) & (brightness < 150) & ((maxc - minc) > 12)
            blond_hair = (r > 115) & (g > 90) & (b < 125) & (r >= g - 20)
            hair = roi & (a > 8) & (dark_hair | brown_hair | blond_hair)
            keep = skin.copy()
            q = deque(int(i) for i in np.flatnonzero(skin))
            max_keep = int(skin.sum()) + max(96, int(skin.sum() * 1.6))
            while q:
                if int(keep.sum()) >= max_keep:
                    break
                idx = q.popleft()
                y, x = divmod(idx, width)
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                    if int(keep.sum()) >= max_keep:
                        break
                    ny, nx = y + dy, x + dx
                    if ny < 0 or ny >= keep.shape[0] or nx < 0 or nx >= width:
                        continue
                    if keep[ny, nx] or not hair[ny, nx]:
                        continue
                    keep[ny, nx] = True
                    q.append(ny * width + nx)
            if int(keep.sum()) < 24:
                return None
            return Image.fromarray((keep.astype("uint8") * 255), "L").filter(ImageFilter.MaxFilter(5))

        def _combine_keep_masks(*masks):
            from PIL import ImageChops
            out = None
            for m in masks:
                if m is None:
                    continue
                out = m if out is None else ImageChops.lighter(out, m)
            return out

        def _remove_from_background_sample(src_img, sample_mask, keep_mask=None, strength=0.7, hard_keep_mask=None):
            if sample_mask is None:
                return None
            try:
                import numpy as np
                from collections import deque
                from PIL import ImageChops, ImageFilter
            except Exception:
                return None
            arr = np.array(src_img.convert("RGBA"))
            alpha = arr[:, :, 3]
            sample_seed = (np.array(sample_mask, dtype=np.uint8) > 8) & (alpha > 8)
            if int(sample_seed.sum()) == 0:
                return None

            strength = max(0.1, min(1.0, float(strength or 0.7)))
            height, width = sample_seed.shape
            rgb = arr[:, :, :3].astype(np.float32)
            samples = rgb[sample_seed]
            mean = samples.mean(axis=0)
            sample_dist = np.sqrt(((samples - mean) ** 2).sum(axis=1))
            spread = float(np.percentile(sample_dist, 90)) if samples.size else 0.0
            mean_threshold = float(np.clip(spread * (1.35 + strength * 1.65) + 26.0 + 92.0 * strength, 28.0, 220.0))
            local_threshold = float(18.0 + 70.0 * strength)
            range_margin = float(np.clip(spread * 0.55 + 18.0 + 82.0 * strength, 22.0, 150.0))
            lo = np.maximum(0.0, np.percentile(samples, 5, axis=0) - range_margin)
            hi = np.minimum(255.0, np.percentile(samples, 95, axis=0) + range_margin)
            broad_similar = (
                (np.sqrt(((rgb - mean) ** 2).sum(axis=2)) <= mean_threshold)
                | np.all((rgb >= lo) & (rgb <= hi), axis=2)
                | (alpha <= 8)
            )
            tight_margin = float(np.clip(spread * 0.28 + 10.0 + 32.0 * strength, 12.0, 70.0))
            tight_lo = np.maximum(0.0, np.percentile(samples, 10, axis=0) - tight_margin)
            tight_hi = np.minimum(255.0, np.percentile(samples, 90, axis=0) + tight_margin)
            strict_similar = (
                (np.sqrt(((rgb - mean) ** 2).sum(axis=2)) <= max(32.0, mean_threshold * 0.72))
                | np.all((rgb >= tight_lo) & (rgb <= tight_hi), axis=2)
                | (alpha <= 8)
            )
            seed = np.array(
                Image.fromarray((sample_seed.astype("uint8") * 255), "L").filter(ImageFilter.MaxFilter(7)),
                dtype=np.uint8,
            ) > 8
            seed &= broad_similar & (alpha > 8)
            seed |= sample_seed

            def _enclosed_stroke_region(mask_img):
                stroke = np.array(mask_img, dtype=np.uint8) > 8
                if int(stroke.sum()) < 16:
                    return None
                barrier_img = Image.fromarray((stroke.astype("uint8") * 255), "L").filter(ImageFilter.MaxFilter(9))
                barrier = np.array(barrier_img, dtype=np.uint8) > 8
                outside = np.zeros(stroke.shape, dtype=bool)
                fill = deque()
                for x in range(width):
                    if not barrier[0, x]:
                        outside[0, x] = True
                        fill.append(x)
                    if not barrier[height - 1, x] and not outside[height - 1, x]:
                        outside[height - 1, x] = True
                        fill.append((height - 1) * width + x)
                for y in range(height):
                    if not barrier[y, 0] and not outside[y, 0]:
                        outside[y, 0] = True
                        fill.append(y * width)
                    if not barrier[y, width - 1] and not outside[y, width - 1]:
                        outside[y, width - 1] = True
                        fill.append(y * width + width - 1)
                while fill:
                    idx = fill.popleft()
                    y, x = divmod(idx, width)
                    if x > 0 and not barrier[y, x - 1] and not outside[y, x - 1]:
                        outside[y, x - 1] = True
                        fill.append(idx - 1)
                    if x < width - 1 and not barrier[y, x + 1] and not outside[y, x + 1]:
                        outside[y, x + 1] = True
                        fill.append(idx + 1)
                    if y > 0 and not barrier[y - 1, x] and not outside[y - 1, x]:
                        outside[y - 1, x] = True
                        fill.append(idx - width)
                    if y < height - 1 and not barrier[y + 1, x] and not outside[y + 1, x]:
                        outside[y + 1, x] = True
                        fill.append(idx + width)
                enclosed = ~(outside | barrier)
                enclosed_count = int(enclosed.sum())
                if enclosed_count < max(64, int(stroke.sum() * 2)):
                    return None
                if enclosed_count > int(width * height * 0.96):
                    return None
                return enclosed | seed

            work_area = _enclosed_stroke_region(sample_mask)
            wall_threshold = float(44.0 + 36.0 * strength)
            edge_wall = np.zeros(seed.shape, dtype=bool)
            if width > 1:
                diff_x = np.sqrt(((rgb[:, 1:] - rgb[:, :-1]) ** 2).sum(axis=2))
                wall_x = diff_x > wall_threshold
                edge_wall[:, 1:] |= wall_x
                edge_wall[:, :-1] |= wall_x
            if height > 1:
                diff_y = np.sqrt(((rgb[1:, :] - rgb[:-1, :]) ** 2).sum(axis=2))
                wall_y = diff_y > wall_threshold
                edge_wall[1:, :] |= wall_y
                edge_wall[:-1, :] |= wall_y
            edge_wall &= ~broad_similar
            edge_wall &= alpha > 8
            edge_wall = np.array(
                Image.fromarray((edge_wall.astype("uint8") * 255), "L").filter(ImageFilter.MaxFilter(3)),
                dtype=np.uint8,
            ) > 8
            edge_wall[seed] = False

            hard_protected = np.zeros(seed.shape, dtype=bool)
            if hard_keep_mask is not None:
                hard_protected = np.array(hard_keep_mask, dtype=np.uint8) > 8
                hard_protected[seed] = False
            soft_protected = np.zeros(seed.shape, dtype=bool)
            if keep_mask is not None:
                soft_protected = np.array(keep_mask, dtype=np.uint8) > 8
                soft_protected[seed] = False
            # A closed cyan stroke is only a search boundary. Subject pixels
            # inside it still need protection unless they strongly match the
            # sampled background.
            protected = hard_protected | (soft_protected & ~strict_similar)

            bg = np.zeros(seed.shape, dtype=bool)
            q = deque(int(i) for i in np.flatnonzero(seed))
            flat_rgb = rgb.reshape((-1, 3))

            def _accept(neighbor_idx, parent_idx):
                y, x = divmod(neighbor_idx, width)
                if protected[y, x]:
                    return False
                if edge_wall[y, x] and not seed[y, x]:
                    return False
                if work_area is not None:
                    if not work_area[y, x] and not seed[y, x]:
                        return False
                if broad_similar[y, x]:
                    return True
                parent_rgb = flat_rgb[parent_idx]
                candidate_rgb = flat_rgb[neighbor_idx]
                return float(np.sqrt(((candidate_rgb - parent_rgb) ** 2).sum())) <= local_threshold

            while q:
                idx = q.popleft()
                y, x = divmod(idx, width)
                if bg[y, x]:
                    continue
                bg[y, x] = True
                if x > 0 and not bg[y, x - 1] and _accept(idx - 1, idx):
                    q.append(idx - 1)
                if x < width - 1 and not bg[y, x + 1] and _accept(idx + 1, idx):
                    q.append(idx + 1)
                if y > 0 and not bg[y - 1, x] and _accept(idx - width, idx):
                    q.append(idx - width)
                if y < height - 1 and not bg[y + 1, x] and _accept(idx + width, idx):
                    q.append(idx + width)

            def _recover_sampled_background_islands(background):
                eligible = broad_similar & ~background & ~protected & ~edge_wall
                if work_area is not None:
                    eligible &= work_area
                if int(eligible.sum()) == 0:
                    return background

                recovered = background.copy()
                visited = np.zeros(seed.shape, dtype=bool)
                min_island_area = max(64, int(width * height * 0.0012))
                for start in np.flatnonzero(eligible):
                    start = int(start)
                    sy, sx = divmod(start, width)
                    if visited[sy, sx] or not eligible[sy, sx]:
                        continue

                    component = []
                    touches_background = False
                    touches_edge = False
                    strict_count = 0
                    fill = deque([start])
                    visited[sy, sx] = True
                    while fill:
                        idx = fill.popleft()
                        y, x = divmod(idx, width)
                        component.append(idx)
                        if x == 0 or y == 0 or x == width - 1 or y == height - 1:
                            touches_edge = True
                        if strict_similar[y, x]:
                            strict_count += 1
                        for ny, nx in ((y, x - 1), (y, x + 1), (y - 1, x), (y + 1, x)):
                            if ny < 0 or ny >= height or nx < 0 or nx >= width:
                                continue
                            if recovered[ny, nx]:
                                touches_background = True
                                continue
                            if not eligible[ny, nx] or visited[ny, nx]:
                                continue
                            visited[ny, nx] = True
                            fill.append(ny * width + nx)

                    strict_ratio = strict_count / max(1, len(component))
                    if touches_background or touches_edge or (len(component) >= min_island_area and strict_ratio >= 0.82):
                        for idx in component:
                            y, x = divmod(idx, width)
                            recovered[y, x] = True

                return recovered

            bg = _recover_sampled_background_islands(bg)

            if int(bg.sum()) == 0:
                return None
            grow_size = 3 if strength < 0.55 else (5 if strength < 0.85 else 7)
            remove_mask = Image.fromarray((bg.astype("uint8") * 255), "L").filter(ImageFilter.MaxFilter(grow_size))
            if keep_mask is not None or hard_keep_mask is not None:
                keep_out = Image.fromarray((protected.astype("uint8") * 255), "L")
                remove_mask = ImageChops.subtract(remove_mask, keep_out)
            out = src_img.copy()
            r, g, b, a = out.split()
            a = ImageChops.subtract(a, remove_mask)
            out = Image.merge("RGBA", (r, g, b, a))
            return out

        provider_error = None
        has_provider_selection = bool(selected_endpoint_id) or bool(selected_endpoint) or bool(selected_model and selected_model not in known_rembg_models)
        if bg_remove_pipeline == "model" or (bg_remove_pipeline == "auto" and has_provider_selection):
            try:
                provider_image = await _remove_with_provider()
                if _provider_result_has_transparency(provider_image):
                    return {"image": provider_image, "source": "provider"}
                provider_error = "Selected image model returned no transparent background."
            except HTTPException as exc:
                provider_error = str(exc.detail)
            except Exception as exc:
                provider_error = str(exc)
        if bg_remove_pipeline == "model" and provider_error:
            return {"error": f"Selected image model failed: {str(provider_error)[:260]}"}

        protected_keep = None
        sampled_result = None
        if bg_remove_pipeline in {"auto", "heuristic", "rembg"} and background_hint is not None:
            protected_keep = _subject_keep_mask(
                img,
                background_hint,
                allow_model=bg_remove_pipeline == "rembg",
            )
            sampled_result = _remove_from_background_sample(
                img,
                background_hint,
                protected_keep,
                bg_strength,
                hint,
            )
        if sampled_result is not None:
            result = sampled_result
            if hint is not None:
                r, g, b, a = result.split()
                from PIL import ImageChops
                a = ImageChops.multiply(a, hint)
                result = Image.merge("RGBA", (r, g, b, a))
            buf = io.BytesIO()
            result.save(buf, format="PNG")
            source = "rembg+heuristic" if bg_remove_pipeline == "rembg" else "heuristic"
            return {"image": base64.b64encode(buf.getvalue()).decode(), "source": source}

        if bg_remove_pipeline == "heuristic":
            return {"error": "Heuristic sample mode needs background sample strokes that match the background."}

        # Crop to the bbox if a hint was supplied so rembg sees just the
        # user's region of interest. Otherwise process the whole image.
        if bbox:
            crop = img.crop(bbox)
        else:
            crop = img

        try:
            cut = _remove_with_preferred_rembg(crop)
        except ImportError:
            try:
                from transformers import pipeline
                pipe = pipeline("image-segmentation", model="briaai/RMBG-1.4", trust_remote_code=True)
                mask_img = pipe(crop, return_mask=True).convert("L")
                tmp = crop.copy()
                tmp.putalpha(mask_img)
                cut = tmp
            except Exception:
                return {"error": "No background removal model available. Install rembg: pip install rembg"}

        # Compose the cropped result back into a full-size transparent canvas.
        if bbox:
            result = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            result.paste(cut, (bbox[0], bbox[1]), cut)
        else:
            result = cut.convert("RGBA")

        # Final alpha = result.alpha * hint (normalised). Anything outside
        # the user's hint is forced transparent.
        if hint is not None:
            r, g, b, a = result.split()
            # Multiply alphas — use ImageChops to stay in PIL-pure code.
            from PIL import ImageChops
            a = ImageChops.multiply(a, hint)
            result = Image.merge("RGBA", (r, g, b, a))

        # Edge cleanup (feather / grow) moved to the client so the user
        # can re-tune live without re-running the model. Server returns
        # the pristine cutout.

        buf = io.BytesIO()
        result.save(buf, format="PNG")
        return {"image": base64.b64encode(buf.getvalue()).decode(), "source": "rembg"}

    # ---- POST /api/image/enhance-face ----
    @router.post("/api/image/enhance-face")
    async def enhance_face(request: Request):
        """Face/portrait enhancement. Uses GFPGAN if available, falls back to PIL."""
        require_privilege(request, "can_generate_images")
        body = await request.json()
        image_b64 = body.get("image")
        if not image_b64:
            raise HTTPException(400, "No image provided")

        import base64, io, tempfile, os
        from PIL import Image, ImageFilter, ImageEnhance
        import numpy as np

        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

        # Try GFPGAN first (AI face restoration)
        try:
            from gfpgan import GFPGANer
            import cv2

            model_path = os.path.join(tempfile.gettempdir(), "gfpgan_models")
            os.makedirs(model_path, exist_ok=True)

            restorer = GFPGANer(
                model_path="https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth",
                upscale=1,
                arch="clean",
                channel_multiplier=2,
                bg_upsampler=None,
                model_rootpath=model_path,
            )

            img_bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            _, _, output = restorer.enhance(
                img_bgr,
                has_aligned=False,
                only_center_face=False,
                paste_back=True,
            )

            # Convert back to RGB
            result_rgb = cv2.cvtColor(output, cv2.COLOR_BGR2RGB)
            result_img = Image.fromarray(result_rgb)

            buf = io.BytesIO()
            result_img.save(buf, format="PNG")
            return {"image": base64.b64encode(buf.getvalue()).decode()}

        except ImportError:
            # GFPGAN not available — use PIL-based enhancement (no AI, but works everywhere)
            logger.info("GFPGAN not available — using PIL enhancement fallback")
            # Multi-step enhancement: denoise → sharpen → contrast → color boost
            enhanced = img.filter(ImageFilter.MedianFilter(size=3))  # light denoise
            enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))  # sharpen
            enhanced = ImageEnhance.Contrast(enhanced).enhance(1.15)  # slight contrast boost
            enhanced = ImageEnhance.Color(enhanced).enhance(1.1)  # subtle color boost
            enhanced = ImageEnhance.Brightness(enhanced).enhance(1.05)  # slight brightness lift

            buf = io.BytesIO()
            enhanced.save(buf, format="PNG")
            return {"image": base64.b64encode(buf.getvalue()).decode(), "method": "pil"}
        except Exception as e:
            raise HTTPException(500, f"Face enhancement failed: {str(e)}")

    # ---- Album management (path-param routes) ----

    def _get_or_404_album(db, album_id: str, user):
        album = db.query(GalleryAlbum).filter(GalleryAlbum.id == album_id).first()
        if not album:
            raise HTTPException(404, "Album not found")
        if not _gallery_owner_matches(album.owner, user):
            raise HTTPException(404, "Album not found")
        return album

    def _get_or_404_image(db, image_id: str, user):
        img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
        if not img:
            raise HTTPException(404, "Image not found")
        if not _gallery_owner_matches(img.owner, user):
            raise HTTPException(404, "Image not found")
        return img

    @router.put("/api/gallery/albums/{album_id}")
    async def update_album(request: Request, album_id: str):
        user = require_user(request)
        data = await request.json()
        db = SessionLocal()
        try:
            album = _get_or_404_album(db, album_id, user)
            if data.get("name") is not None:
                album.name = data["name"]
            if data.get("description") is not None:
                album.description = data["description"]
            if data.get("cover_id") is not None:
                cover_id = data["cover_id"] or None
                if cover_id:
                    _get_or_404_image(db, cover_id, user)
                album.cover_id = cover_id
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.delete("/api/gallery/albums/{album_id}")
    async def delete_album(request: Request, album_id: str):
        user = require_user(request)
        db = SessionLocal()
        try:
            album = _get_or_404_album(db, album_id, user)
            q = db.query(GalleryImage).filter(GalleryImage.album_id == album_id)
            if user is not None:
                q = q.filter(GalleryImage.owner == user)
            q.update({"album_id": None}, synchronize_session=False)
            db.delete(album)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/api/gallery/albums/{album_id}/add")
    async def add_to_album(request: Request, album_id: str):
        user = require_user(request)
        data = await request.json()
        ids = data.get("image_ids", [])
        db = SessionLocal()
        try:
            _get_or_404_album(db, album_id, user)
            # Only move images the caller owns
            q = db.query(GalleryImage).filter(GalleryImage.id.in_(ids))
            if user:
                q = q.filter(GalleryImage.owner == user)
            q.update({"album_id": album_id}, synchronize_session=False)
            db.commit()
            return {"ok": True, "count": len(ids)}
        finally:
            db.close()

    @router.post("/api/gallery/albums/{album_id}/remove")
    async def remove_from_album(request: Request, album_id: str):
        user = require_user(request)
        data = await request.json()
        ids = data.get("image_ids", [])
        db = SessionLocal()
        try:
            _get_or_404_album(db, album_id, user)
            q = db.query(GalleryImage).filter(
                GalleryImage.id.in_(ids), GalleryImage.album_id == album_id
            )
            if user:
                q = q.filter(GalleryImage.owner == user)
            q.update({"album_id": None}, synchronize_session=False)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ---- Favorite toggle ----

    @router.post("/api/gallery/{image_id}/favorite")
    async def toggle_favorite(request: Request, image_id: str):
        user = require_user(request)
        db = SessionLocal()
        try:
            img = _get_or_404_image(db, image_id, user)
            img.favorite = not img.favorite
            db.commit()
            return {"ok": True, "favorite": img.favorite}
        finally:
            db.close()

    # ---- AI auto-tag ----

    @router.post("/api/gallery/{image_id}/ai-tag")
    async def ai_tag_image(request: Request, image_id: str):
        """Send image to vision model for auto-tagging."""
        import base64, httpx
        from pathlib import Path

        user = require_user(request)
        db = SessionLocal()
        try:
            img = _get_or_404_image(db, image_id, user)

            img_path = _gallery_image_path(img.filename)
            if not img_path.exists():
                raise HTTPException(404, "Image file not found")

            # Read and encode
            img_bytes = img_path.read_bytes()
            b64 = base64.b64encode(img_bytes).decode()
            ext = img.filename.rsplit(".", 1)[-1].lower()
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")

            # Resolve vision model via admin Vision setting (same resolver used for docs)
            from src.document_processor import _load_vl_settings, _resolve_vl_model
            vl_settings = _load_vl_settings()
            if not vl_settings.get("vision_enabled", True):
                return {"error": "Vision is disabled — enable it in Settings → Vision"}
            configured = vl_settings.get("vision_model", "")
            try:
                chat_url, model_name, headers = _resolve_vl_model(configured, owner=user)
            except ValueError:
                return {"error": "No vision model configured — set one in Settings → Vision"}
            if not chat_url:
                return {"error": "No vision-capable endpoint configured"}

            # Call vision model — format differs between Anthropic and OpenAI
            from src.llm_core import _detect_provider, _restricts_temperature, _uses_max_completion_tokens
            provider = _detect_provider(chat_url)
            tag_prompt = (
                "Analyze this photo. Return ONLY a comma-separated list of tags. "
                "Include: objects, people (describe by appearance — age range, gender), "
                "scene/setting, activities, mood/atmosphere, colors, location type, "
                "time of day, weather if visible, any text/signs visible. "
                "Be specific but concise. 10-25 tags. No explanation, just tags."
            )

            if provider == "anthropic":
                payload = {
                    "model": model_name,
                    "max_tokens": 200,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {
                                "type": "base64", "media_type": mime, "data": b64,
                            }},
                            {"type": "text", "text": tag_prompt},
                        ],
                    }],
                }
            else:
                _tok_key = "max_completion_tokens" if _uses_max_completion_tokens(model_name) else "max_tokens"
                payload = {
                    "model": model_name,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": tag_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        ],
                    }],
                    _tok_key: 200,
                    "temperature": 0.3,
                }
                # Reasoning models (o1/o3/o4/gpt-5) reject an explicit temperature.
                if _restricts_temperature(model_name):
                    payload.pop("temperature", None)

            h = {"Content-Type": "application/json"}
            if headers:
                h.update(headers)

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(chat_url, json=payload, headers=h)
                if resp.status_code != 200:
                    body = resp.text[:500]
                    logger.error(f"Vision model {resp.status_code}: {body}")
                    return {"error": f"Vision model returned {resp.status_code}: {body[:200]}"}
                data = resp.json()
                # Anthropic returns content[0].text, OpenAI returns choices[0].message.content
                if provider == "anthropic":
                    content = (data.get("content") or [{}])[0].get("text", "")
                else:
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Clean up tags
            tags = [t.strip().lower() for t in content.split(",") if t.strip()]
            tag_str = ", ".join(tags[:30])
            img.ai_tags = tag_str
            db.commit()
            return {"ok": True, "ai_tags": tag_str}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"AI tagging failed: {e}")
            return {"error": str(e)}
        finally:
            db.close()

    return router
