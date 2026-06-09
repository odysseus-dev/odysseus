# routes/upload_routes.py
import os
import time
import json
import asyncio
from fastapi import APIRouter, Request, File, UploadFile, HTTPException
from typing import List
import logging
from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.upload_handler import count_recent_uploads

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/upload", tags=["upload"])
UPLOAD_RESPONSE_HEADERS = {"X-Content-Type-Options": "nosniff"}

def setup_upload_routes(upload_handler):
    """Setup upload routes with the provided handler"""

    def _upload_root() -> str:
        from src.constants import UPLOAD_DIR
        return os.path.realpath(getattr(upload_handler, "upload_dir", UPLOAD_DIR))

    def _path_inside_upload_dir(path: str) -> bool:
        try:
            return os.path.commonpath([_upload_root(), os.path.realpath(path)]) == _upload_root()
        except Exception:
            return False

    def _resolve_upload_path(file_id: str) -> str:
        from src.constants import UPLOAD_DIR
        upload_root = getattr(upload_handler, "upload_dir", UPLOAD_DIR)
        direct = os.path.join(upload_root, file_id)
        if os.path.lexists(direct):
            if not _path_inside_upload_dir(direct):
                raise HTTPException(403, "Access denied")
            if os.path.isfile(direct):
                return direct
            raise HTTPException(404, "File not found")

        for root, _dirs, files in os.walk(upload_root, followlinks=False):
            if file_id not in files:
                continue
            path = os.path.join(root, file_id)
            if not _path_inside_upload_dir(path):
                raise HTTPException(403, "Access denied")
            if os.path.isfile(path):
                return path
            raise HTTPException(404, "File not found")

        raise HTTPException(404, "File not found")
    
    @router.post("")
    async def api_upload(request: Request, files: List[UploadFile] = File(...)):
        """Upload files with enhanced security and organization."""
        if not files:
            raise HTTPException(400, "No files uploaded")
            
        client_ip = request.client.host if request.client else "unknown"
        out = []

        # Limit concurrent uploads per IP. Count genuine recent upload events —
        # NOT the number of files in this batch. The previous check summed over
        # `files`, so a single multi-file request counted itself as N concurrent
        # uploads and tripped the limit (issue #1346: "attach more than one file
        # → the model doesn't even see them"). save_upload still enforces the
        # per-minute sliding-window rate limit per file.
        recent_uploads = count_recent_uploads(
            upload_handler.upload_rate_log.get(client_ip, []), time.time()
        )

        if recent_uploads >= upload_handler.max_concurrent_uploads:
            raise HTTPException(
                status_code=429,
                detail=f"Maximum concurrent uploads ({upload_handler.max_concurrent_uploads}) exceeded"
            )
        
        for u in files:
            try:
                meta = upload_handler.save_upload(u, client_ip, owner=get_current_user(request))
                out.append({
                    "id": meta["id"],
                    "name": meta["name"],
                    "mime": meta["mime"],
                    "size": meta["size"],
                    "hash": meta["hash"],
                    "uploaded_at": meta["uploaded_at"],
                    "width": meta.get("width"),
                    "height": meta.get("height"),
                    "is_duplicate": meta.get("is_duplicate", False)
                })
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to process upload {u.filename}: {str(e)}")
                continue
        
        if not out:
            raise HTTPException(500, "All file uploads failed")
            
        return {"files": out}
    
    @router.post("/cleanup")
    async def manual_cleanup(request: Request):
        """Manually trigger cleanup of old uploads."""
        require_admin(request)
        cleaned_count = upload_handler.cleanup_old_uploads()
        return {"status": "success", "files_cleaned": cleaned_count}

    @router.get("/stats")
    async def upload_stats(request: Request):
        """Get statistics about uploaded files."""
        require_admin(request)
        try:
            return upload_handler.get_upload_stats()
        except Exception as e:
            logger.error(f"Failed to get upload stats: {e}")
            raise HTTPException(500, "Failed to get upload statistics")

    @router.get("/{file_id}")
    async def download_file(request: Request, file_id: str, thumb: int = 0):
        """Serve an uploaded file by its ID. `?thumb=1` returns a small cached
        JPEG thumbnail for images (used by chat attachment previews) so the
        client isn't downloading the full-resolution photo just to show it tiny."""
        if not upload_handler.validate_upload_id(file_id):
            raise HTTPException(400, "Invalid file ID")
        import mimetypes as _mt
        # Look up original filename and owner from uploads.json
        original_name = file_id
        info = None
        uploads_db = os.path.join(_upload_root(), "uploads.json")
        if os.path.exists(uploads_db):
            with open(uploads_db, encoding="utf-8") as f:
                db = json.load(f)
            info = next((fi for fi in db.values() if fi.get("id") == file_id), None)
            if info:
                original_name = info.get("name", file_id)
        auth_mgr = getattr(request.app.state, "auth_manager", None)
        auth_configured = bool(auth_mgr and auth_mgr.is_configured)
        current_user = get_current_user(request)
        file_owner = info.get("owner") if info else None
        if auth_configured:
            if not current_user:
                raise HTTPException(403, "Access denied")
            if file_owner != current_user and not auth_mgr.is_admin(current_user):
                raise HTTPException(404, "File not found")
        path = _resolve_upload_path(file_id)
        mime = (info or {}).get("mime") or _mt.guess_type(path)[0] or "application/octet-stream"
        from fastapi.responses import FileResponse
        # Downscaled thumbnail for image previews — generated once and cached.
        if thumb and mime.startswith("image/"):
            try:
                from PIL import Image, ImageOps
                thumb_dir = os.path.join(_upload_root(), ".thumbs")
                os.makedirs(thumb_dir, exist_ok=True)
                thumb_path = os.path.join(thumb_dir, file_id + ".jpg")
                if (not os.path.exists(thumb_path)
                        or os.path.getmtime(thumb_path) < os.path.getmtime(path)):
                    im = Image.open(path)
                    # iPhone / camera JPEGs encode rotation in EXIF rather than
                    # the pixel data. Browsers honour that on the original via
                    # image-orientation:from-image, but PIL strips EXIF when it
                    # saves the JPEG thumb, leaving the pixels sideways. Bake
                    # the rotation into the pixels before thumbnailing.
                    im = ImageOps.exif_transpose(im)
                    im.thumbnail((320, 320))
                    if im.mode not in ("RGB", "L"):
                        im = im.convert("RGB")
                    im.save(thumb_path, "JPEG", quality=80)
                return FileResponse(thumb_path, media_type="image/jpeg", headers=UPLOAD_RESPONSE_HEADERS)
            except Exception as e:
                logger.warning(f"Thumbnail generation failed for {file_id}: {e}")
                # Fall through to the full image.
        return FileResponse(
            path,
            media_type=mime,
            filename=original_name,
            headers=UPLOAD_RESPONSE_HEADERS,
        )

    def _load_upload_info(file_id: str):
        """Look up the uploads.json record for a file_id, with owner/auth checks."""
        info = None
        uploads_db = os.path.join(_upload_root(), "uploads.json")
        if os.path.exists(uploads_db):
            with open(uploads_db, encoding="utf-8") as f:
                db = json.load(f)
            info = next((fi for fi in db.values() if fi.get("id") == file_id), None)
        return info

    def _vision_cache_path(file_id: str) -> str:
        cache_dir = os.path.join(_upload_root(), ".vision")
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, file_id + ".txt")

    @router.get("/{file_id}/vision")
    async def get_vision_text(request: Request, file_id: str, force: int = 0):
        """Return the vision-model OCR/description for an uploaded image.
        Cached under UPLOAD_DIR/.vision/{file_id}.txt — first call computes,
        subsequent loads are instant. Pass force=1 to recompute."""
        if not upload_handler.validate_upload_id(file_id):
            raise HTTPException(400, "Invalid file ID")
        info = _load_upload_info(file_id)
        auth_mgr = getattr(request.app.state, "auth_manager", None)
        auth_configured = bool(auth_mgr and auth_mgr.is_configured)
        current_user = get_current_user(request)
        file_owner = info.get("owner") if info else None
        if auth_configured:
            if not current_user:
                raise HTTPException(403, "Access denied")
            if file_owner != current_user and not auth_mgr.is_admin(current_user):
                raise HTTPException(404, "File not found")
        path = _resolve_upload_path(file_id)
        import mimetypes as _mt
        mime = (info or {}).get("mime") or _mt.guess_type(path)[0] or ""
        if not mime.startswith("image/"):
            raise HTTPException(400, "Not an image")
        cache_path = _vision_cache_path(file_id)
        if not force and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    return {"text": f.read(), "cached": True}
            except Exception as e:
                logger.warning(f"Vision cache read failed for {file_id}: {e}")
        from src.document_processor import analyze_image_with_vl
        try:
            text = analyze_image_with_vl(path, owner=current_user) or ""
        except Exception as e:
            logger.error(f"Vision analysis failed for {file_id}: {e}")
            raise HTTPException(500, f"Vision analysis failed: {e}")
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            logger.warning(f"Vision cache write failed for {file_id}: {e}")
        return {"text": text, "cached": False}

    @router.put("/{file_id}/vision")
    async def put_vision_text(request: Request, file_id: str):
        """Persist a user-edited vision/OCR text for an attachment. Stored in
        the same cache file so the chat send picks it up as the override."""
        if not upload_handler.validate_upload_id(file_id):
            raise HTTPException(400, "Invalid file ID")
        info = _load_upload_info(file_id)
        if not info:
            raise HTTPException(404, "File not found")
        auth_mgr = getattr(request.app.state, "auth_manager", None)
        auth_configured = bool(auth_mgr and auth_mgr.is_configured)
        current_user = get_current_user(request)
        file_owner = info.get("owner")
        if auth_configured:
            if not current_user:
                raise HTTPException(403, "Access denied")
            if file_owner != current_user and not auth_mgr.is_admin(current_user):
                raise HTTPException(404, "File not found")
        _resolve_upload_path(file_id)
        body = await request.json()
        text = (body or {}).get("text", "")
        if not isinstance(text, str):
            raise HTTPException(400, "text must be a string")
        with open(_vision_cache_path(file_id), "w", encoding="utf-8") as f:
            f.write(text)
        return {"ok": True}

    async def periodic_rate_limit_cleanup():
        """Background task to run cleanup every hour"""
        while True:
            await asyncio.sleep(3600)
            upload_handler.cleanup_rate_limits()
    
    return router, periodic_rate_limit_cleanup
