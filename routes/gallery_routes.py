"""Gallery routes — browsable library for photos and AI-generated images."""

import os
import hashlib
import logging
import re
import uuid
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from core.database import SessionLocal, GalleryImage, GalleryAlbum, ModelEndpoint
from core.database import Session as DbSession
from src.auth_helpers import get_current_user, owner_filter, require_privilege
from src.upload_limits import (
    read_upload_limited,
    GALLERY_UPLOAD_MAX_BYTES,
    GALLERY_TRANSFORM_UPLOAD_MAX_BYTES,
)
from src.constants import GENERATED_IMAGES_DIR
from src.optional_deps import patch_realesrgan_torchvision_compat

from routes.gallery_helpers import (
    GalleryPatch, _extract_exif, _image_to_dict, _owner_filter, _human_size,
)

logger = logging.getLogger(__name__)


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


async def _fetch_result_image_b64(url: str) -> Optional[str]:
    """Fetch an image URL returned in an upstream response body, base64-encoded
    (or None on a non-200).

    The URL comes from the diffusion/OpenAI server's response, not from our own
    config, so a malicious or compromised endpoint could otherwise steer this
    fetch at an internal or cloud-metadata address. Validate it the same way the
    client-supplied endpoint is validated before the first request.
    """
    import base64
    import httpx
    from src.url_safety import check_outbound_url

    ok, reason = check_outbound_url(
        url,
        block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
    )
    if not ok:
        raise HTTPException(502, f"Upstream returned an unsafe image URL: {reason}")
    async with httpx.AsyncClient(timeout=60) as c2:
        ir = await c2.get(url)
        if ir.status_code == 200:
            return base64.b64encode(ir.content).decode()
    return None


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

        user = get_current_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not user or img.owner != user:
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
        user = get_current_user(request)
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
            if not user or img.owner != user:
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

        user = get_current_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not user or img.owner != user:
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
        user = get_current_user(request)
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
            if not user or img.owner != user:
                raise HTTPException(404, "Image not found")
            return _image_to_dict(img, session_name)
        finally:
            db.close()

    # ---- PATCH /api/gallery/{image_id} ----
    @router.patch("/api/gallery/{image_id}")
    async def patch_gallery_image(request: Request, image_id: str, req: GalleryPatch) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not user or img.owner != user:
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
        user = get_current_user(request)
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
        user = get_current_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not user or img.owner != user:
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

    # ---- POST /api/image/inpaint — proxy to diffusion server OR OpenAI ----
    @router.post("/api/image/inpaint")
    async def inpaint_proxy(request: Request):
        """Forward inpaint request. If the selected endpoint is OpenAI, re-shape
        the request for /v1/images/edits (multipart, inverted mask). Otherwise
        proxy through to a self-hosted diffusion server's /v1/images/inpaint."""
        import httpx
        user = require_privilege(request, "can_generate_images")
        body = await request.json()
        # Use endpoint from request body (editor dropdown) or fall back to DB lookup
        base = (body.pop("_endpoint", "") or "").rstrip("/")
        # SSRF hardening: validate a client-supplied endpoint before any
        # outbound request (mirrors routes/embedding_routes.py).
        if base:
            from src.url_safety import check_outbound_url
            ok, reason = check_outbound_url(
                base,
                block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
            )
            if not ok:
                raise HTTPException(400, f"Rejected endpoint URL: {reason}")
        chosen_model = (body.pop("_model", "") or "").strip()
        api_key = None
        if not base:
            db = SessionLocal()
            try:
                ep = _first_visible_image_endpoint(db, user)
                if not ep:
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
                    raise HTTPException(403, "Choose a registered image endpoint")
            finally:
                db.close()

        if not base.endswith("/v1"):
            base += "/v1"

        is_openai = "api.openai.com" in base

        if is_openai:
            # OpenAI path: /v1/images/edits with gpt-image-1.
            # Mask convention differs from Stable Diffusion:
            #   SD:     white pixels = regenerate, black = keep
            #   OpenAI: transparent alpha = regenerate, opaque = keep
            # So we convert the incoming PNG mask into an alpha-channel PNG.
            if not api_key:
                raise HTTPException(400, "OpenAI endpoint has no api_key stored — edit it in Endpoints settings.")
            import base64, io
            try:
                from PIL import Image
            except ImportError:
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
            # Honor explicit model selection from the editor; fall back to gpt-image-1.
            # dall-e-3 has no edit endpoint — refuse it loudly so the user picks again.
            oa_model = chosen_model or "gpt-image-1"
            if "dall-e-3" in oa_model:
                raise HTTPException(400, "dall-e-3 doesn't support image edits — pick gpt-image-1 or dall-e-2")
            data = {
                "model": oa_model,
                "prompt": body.get("prompt", ""),
                "size": size,
                "n": "1",
            }
            headers = {"Authorization": f"Bearer {api_key}"}
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    r = await client.post(f"{base}/images/edits", headers=headers, data=data, files=files)
                    if r.status_code != 200:
                        raise HTTPException(r.status_code, f"OpenAI edit failed: {r.text[:300]}")
                    result = r.json()
                    raw_b64 = None
                    if result.get("data"):
                        item = result["data"][0]
                        # gpt-image-1 returns b64_json by default; dall-e-2 may return url
                        if item.get("b64_json"):
                            raw_b64 = item["b64_json"]
                        elif item.get("url"):
                            raw_b64 = await _fetch_result_image_b64(item["url"])
                    if not raw_b64:
                        raise HTTPException(502, "OpenAI returned no image")

                    # OpenAI's edits API doesn't truly preserve unmasked
                    # pixels — gpt-image-1 regenerates the whole image,
                    # so even areas the user didn't mask come back
                    # slightly different. Composite the model output onto
                    # the ORIGINAL source using the user's mask, so only
                    # the masked region actually changes.
                    try:
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
                        return {"image": base64.b64encode(out_buf.getvalue()).decode()}
                    except Exception as comp_err:
                        # If compositing fails for any reason, fall back
                        # to the raw OpenAI output rather than blocking.
                        logger.warning(f"Inpaint compose failed, returning raw: {comp_err}")
                        return {"image": raw_b64}
            except httpx.TimeoutException:
                raise HTTPException(504, "OpenAI inpaint timed out (120s)")

        # Self-hosted diffusion server path
        try:
            # Forward chosen_model so the diffusion server can route if it ever
            # supports multiple models per process. Harmless if ignored.
            if chosen_model:
                body["model"] = chosen_model
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(f"{base}/images/inpaint", json=body)
                if r.status_code != 200:
                    raise HTTPException(r.status_code, f"Inpaint failed: {r.text[:200]}")
                return r.json()
        except httpx.TimeoutException:
            raise HTTPException(504, "Inpaint request timed out (120s)")
        except HTTPException:
            raise
        except Exception as e:
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
        import httpx
        user = require_privilege(request, "can_generate_images")
        body = await request.json()

        image_b64 = body.get("image")
        if not image_b64:
            raise HTTPException(400, "No image provided")

        endpoint = (body.get("_endpoint") or "").rstrip("/")
        # SSRF hardening: a client-supplied endpoint is fetched server-side
        # below, so validate it first (mirrors routes/embedding_routes.py).
        # Local-first means loopback/LAN is allowed by default; the cloud
        # metadata range and non-HTTP(S) schemes are always rejected.
        if endpoint:
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
        if not base:
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
                                img_b64 = await _fetch_result_image_b64(item["url"])
                                if img_b64:
                                    return {"image": img_b64}
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
            patch_realesrgan_torchvision_compat()
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
            patch_realesrgan_torchvision_compat()
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
        require_privilege(request, "can_generate_images")
        body = await request.json()
        image_b64 = body.get("image")
        hint_b64 = body.get("hint_mask")

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

        # Crop to the bbox if a hint was supplied so rembg sees just the
        # user's region of interest. Otherwise process the whole image.
        if bbox:
            crop = img.crop(bbox)
        else:
            crop = img

        try:
            from rembg import remove
            cut = remove(crop)
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
        return {"image": base64.b64encode(buf.getvalue()).decode()}

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
        if not user or album.owner != user:
            raise HTTPException(404, "Album not found")
        return album

    def _get_or_404_image(db, image_id: str, user):
        img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
        if not img:
            raise HTTPException(404, "Image not found")
        if not user or img.owner != user:
            raise HTTPException(404, "Image not found")
        return img

    @router.put("/api/gallery/albums/{album_id}")
    async def update_album(request: Request, album_id: str):
        user = get_current_user(request)
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
        user = get_current_user(request)
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
        user = get_current_user(request)
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
        user = get_current_user(request)
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
        user = get_current_user(request)
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

        user = get_current_user(request)
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
