# routes/upload_routes.py
import os
import time
import json
import asyncio
import shutil
import uuid
from pathlib import Path
from fastapi import APIRouter, Request, File, UploadFile, HTTPException, Form
from typing import List, Optional
import logging
from core.middleware import require_admin
from core.database import (
    SessionLocal,
    ChatMessage as DbChatMessage,
    CalendarCal,
    CalendarEvent,
    Document,
    DocumentVersion,
    GalleryImage,
    Note,
    Session as DbSession,
)
from src.auth_helpers import effective_user, is_delegated_credential
from src.owner_identity import auth_disabled, is_request_sentinel_owner
from src.attachment_refs import attachment_refs_from_metadata
from src.constants import GENERATED_IMAGES_DIR
from src.path_confinement import PathEscape, canonical_root, confine, is_inside
from src.upload_handler import (
    UploadCleanupSafetyError,
    count_recent_uploads,
    extract_upload_ids,
    secure_filename,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/upload", tags=["upload"])
UPLOAD_READ_SCOPES = frozenset({"chat", "documents:read", "documents:write"})
UPLOAD_WRITE_SCOPES = frozenset({"chat", "documents:write"})
UPLOAD_RESPONSE_HEADERS = {"X-Content-Type-Options": "nosniff"}

def _upload_ids_from_persisted_text(value: object) -> set[str]:
    """Return canonical upload IDs embedded in persisted text.

    This covers attachment reference lines/URIs and the PDF source markers
    stored by the document editor. False positives are intentionally
    conservative: retaining an extra upload is safer than deleting referenced
    bytes.
    """
    return extract_upload_ids(value)


def _upload_ids_from_message_metadata(raw_metadata: object) -> set[str]:
    """Extract attachment IDs from a persisted chat metadata JSON value.

    Malformed metadata raises instead of being treated as an empty reference
    set. The admin cleanup route catches that failure and aborts cleanup.
    """
    if raw_metadata in (None, ""):
        return set()
    if isinstance(raw_metadata, str):
        metadata = json.loads(raw_metadata)
    else:
        metadata = raw_metadata
    if not isinstance(metadata, dict):
        raise ValueError("chat message metadata must be a JSON object")

    attachments = metadata.get("attachments")
    if attachments is not None:
        if not isinstance(attachments, list) or any(
            not isinstance(item, dict) for item in attachments
        ):
            raise ValueError("chat message attachments metadata is malformed")

    ids = {
        str(ref["attachment_id"])
        for ref in attachment_refs_from_metadata(metadata)
        if ref.get("attachment_id")
    }
    # Preserve canonical IDs even in older metadata shapes not normalized by
    # attachment_refs_from_metadata().
    ids.update(_upload_ids_from_persisted_text(json.dumps(metadata)))
    return ids


def _collect_persisted_upload_references() -> tuple[set[str], set[str]]:
    """Collect upload IDs/hashes still referenced by durable application data.

    The caller must treat any exception as an incomplete scan and fail closed.
    There is no distinct artifact table in the current schema; artifact-like
    attachment references persisted in chat/document text are covered by the
    canonical-ID scan.
    """
    referenced_ids: set[str] = set()
    referenced_hashes: set[str] = set()
    db = SessionLocal()
    try:
        for content, raw_metadata in db.query(
            DbChatMessage.content,
            DbChatMessage.meta_data,
        ).yield_per(500):
            referenced_ids.update(_upload_ids_from_persisted_text(content))
            referenced_ids.update(_upload_ids_from_message_metadata(raw_metadata))

        for (content,) in db.query(Document.current_content).yield_per(500):
            referenced_ids.update(_upload_ids_from_persisted_text(content))

        for (content,) in db.query(DocumentVersion.content).yield_per(500):
            referenced_ids.update(_upload_ids_from_persisted_text(content))

        for filename, file_hash in db.query(
            GalleryImage.filename,
            GalleryImage.file_hash,
        ).yield_per(500):
            referenced_ids.update(_upload_ids_from_persisted_text(filename))
            if file_hash:
                referenced_hashes.add(str(file_hash))

        for image_url, color, content, items in db.query(
            Note.image_url,
            Note.color,
            Note.content,
            Note.items,
        ).yield_per(500):
            for value in (image_url, color, content, items):
                referenced_ids.update(_upload_ids_from_persisted_text(value))

        for (color,) in db.query(CalendarCal.color).yield_per(500):
            referenced_ids.update(_upload_ids_from_persisted_text(color))

        for color, description, location in db.query(
            CalendarEvent.color,
            CalendarEvent.description,
            CalendarEvent.location,
        ).yield_per(500):
            for value in (color, description, location):
                referenced_ids.update(_upload_ids_from_persisted_text(value))

        return referenced_ids, referenced_hashes
    finally:
        db.close()


def _run_reference_safe_cleanup(upload_handler) -> int:
    referenced_ids, referenced_hashes = _collect_persisted_upload_references()
    return upload_handler.cleanup_old_uploads(
        referenced_upload_ids=referenced_ids,
        referenced_upload_hashes=referenced_hashes,
    )

def setup_upload_routes(upload_handler):
    """Setup upload routes with the provided handler"""

    def _upload_root() -> str:
        from src.constants import UPLOAD_DIR
        return canonical_root(getattr(upload_handler, "upload_dir", UPLOAD_DIR))

    def _path_inside_upload_dir(path: str) -> bool:
        return is_inside(_upload_root(), path)

    def _resolve_upload_path(file_id: str) -> str:
        safe_id = secure_filename(os.path.basename(file_id))
        if not safe_id:
            raise HTTPException(400, "Invalid file ID")
        root = _upload_root()

        direct = os.path.normpath(os.path.join(root, safe_id))
        if os.path.lexists(direct):
            try:
                canonical_direct = confine(root, direct, allow_root=False)
            except (PathEscape, ValueError):
                raise HTTPException(403, "Access denied")
            if os.path.isfile(canonical_direct):
                return canonical_direct
            raise HTTPException(404, "File not found")

        info = _load_upload_info(safe_id)
        stored_path = (info or {}).get("path")
        if stored_path:
            try:
                canonical_stored = confine(root, stored_path, allow_root=False)
                if os.path.isfile(canonical_stored):
                    return canonical_stored
            except (PathEscape, ValueError):
                raise HTTPException(403, "Access denied")

        for walk_root, _dirs, files in os.walk(root, followlinks=False):
            if safe_id not in files:
                continue
            cand = os.path.normpath(os.path.join(walk_root, safe_id))
            try:
                canonical_cand = confine(root, cand, allow_root=False)
            except (PathEscape, ValueError):
                raise HTTPException(403, "Access denied")
            if os.path.isfile(canonical_cand):
                return canonical_cand
            raise HTTPException(404, "File not found")

        raise HTTPException(404, "File not found")

    def _verify_upload_access(
        request: Request,
        file_owner: str | None,
        *,
        write: bool = False,
    ) -> None:
        if auth_disabled():
            return

        auth_mgr = getattr(request.app.state, "auth_manager", None)
        if auth_mgr is None:
            raise HTTPException(503, "Authentication service unavailable")

        if not getattr(auth_mgr, "is_configured", False):
            raise HTTPException(401, "Setup required")

        current_user = effective_user(request)
        if not current_user or is_request_sentinel_owner(current_user):
            raise HTTPException(403, "Access denied")

        # Delegated credentials (bearer API tokens):
        # Establish token owner identity but NEVER inherit interactive administrator privileges.
        if is_delegated_credential(request):
            token_owner = getattr(request.state, "api_token_owner", None)
            if not token_owner or is_request_sentinel_owner(token_owner):
                raise HTTPException(403, "API token has no valid owner")

            token_scopes = set(getattr(request.state, "api_token_scopes", []) or [])
            required_scopes = UPLOAD_WRITE_SCOPES if write else UPLOAD_READ_SCOPES
            if not token_scopes.intersection(required_scopes):
                raise HTTPException(403, f"API token missing required scope: {' or '.join(sorted(required_scopes))}")

            # Non-owners and legacy ownerless uploads are strictly forbidden for delegated tokens.
            if file_owner is None or current_user != file_owner:
                raise HTTPException(404, "File not found")
            return

        # Interactive session (cookie-authenticated human):
        # Owner access
        if file_owner is not None and current_user == file_owner:
            return

        # Non-owner or legacy ownerless upload: administrator only
        if not (hasattr(auth_mgr, "is_admin") and auth_mgr.is_admin(current_user)):
            raise HTTPException(404, "File not found")

    def _valid_session_id_for_owner(db, session_id: str | None, owner: str | None) -> str | None:
        if not session_id:
            return None
        sess = db.query(DbSession).filter(DbSession.id == session_id).first()
        if not sess:
            return None
        if owner and sess.owner and sess.owner != owner:
            return None
        return session_id

    def _promote_chat_image_to_gallery(meta: dict, owner: str | None, session_id: str | None = None,
                                       gallery_id: str | None = None) -> str | None:
        """Make chat-uploaded images visible in Gallery without changing chat storage."""
        is_image_file = getattr(upload_handler, "is_image_file", None)
        if not callable(is_image_file):
            return None
        if not is_image_file(meta.get("name", ""), meta.get("mime", "")):
            return None

        source_path = meta.get("path")
        if not source_path or not os.path.isfile(source_path):
            return None

        db = SessionLocal()
        try:
            file_hash = meta.get("hash")
            if gallery_id:
                existing = db.query(GalleryImage).filter(
                    GalleryImage.id == gallery_id,
                    GalleryImage.is_active == True,  # noqa: E712
                ).first()
                if existing and (not owner or existing.owner == owner):
                    image_dir = Path(GENERATED_IMAGES_DIR)
                    image_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, image_dir / existing.filename)
                    existing.file_hash = file_hash
                    existing.file_size = meta.get("size")
                    existing.width = meta.get("width")
                    existing.height = meta.get("height")
                    db.commit()
                    return existing.id
            if file_hash:
                q = db.query(GalleryImage).filter(
                    GalleryImage.file_hash == file_hash,
                    GalleryImage.is_active == True,  # noqa: E712
                )
                if owner:
                    q = q.filter(GalleryImage.owner == owner)
                existing = q.first()
                if existing:
                    return existing.id

            image_dir = Path(GENERATED_IMAGES_DIR)
            image_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(meta.get("name") or source_path).suffix.lower()
            if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                mime_ext = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/jpg": ".jpg",
                    "image/webp": ".webp",
                    "image/gif": ".gif",
                }.get(meta.get("mime", ""))
                ext = mime_ext or ".png"
            filename = f"{uuid.uuid4().hex[:12]}{ext}"
            dest_path = image_dir / filename
            shutil.copy2(source_path, dest_path)

            image_id = str(uuid.uuid4())
            db.add(GalleryImage(
                id=image_id,
                filename=filename,
                prompt=meta.get("name") or "Chat upload",
                model="chat-upload",
                owner=owner,
                session_id=_valid_session_id_for_owner(db, session_id, owner),
                file_hash=file_hash,
                width=meta.get("width"),
                height=meta.get("height"),
                file_size=meta.get("size"),
            ))
            db.commit()
            return image_id
        except Exception as e:
            db.rollback()
            logger.warning("Failed to add chat image upload to gallery: %s", e)
            return None
        finally:
            db.close()
    
    @router.post("")
    async def api_upload(
        request: Request,
        files: List[UploadFile] = File(...),
        session_id: Optional[str] = Form(None),
        gallery_id: Optional[str] = Form(None),
    ):
        """Upload files with enhanced security and organization."""
        if not isinstance(session_id, str):
            session_id = None
        if not files:
            raise HTTPException(400, "No files uploaded")

        if not auth_disabled():
            app = getattr(request, "app", None)
            if app is not None:
                auth_mgr = getattr(getattr(app, "state", None), "auth_manager", None)
                if auth_mgr is None:
                    raise HTTPException(503, "Authentication service unavailable")
                if not getattr(auth_mgr, "is_configured", False):
                    raise HTTPException(401, "Setup required")
            current_user = effective_user(request)
            if not current_user or is_request_sentinel_owner(current_user):
                raise HTTPException(403, "Access denied")
            if is_delegated_credential(request):
                token_owner = getattr(request.state, "api_token_owner", None)
                if not token_owner or is_request_sentinel_owner(token_owner):
                    raise HTTPException(403, "API token has no valid owner")
                token_scopes = set(getattr(request.state, "api_token_scopes", []) or [])
                if not token_scopes.intersection(UPLOAD_WRITE_SCOPES):
                    raise HTTPException(403, f"API token missing required scope: {' or '.join(sorted(UPLOAD_WRITE_SCOPES))}")
            
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
                owner = effective_user(request)
                if is_request_sentinel_owner(owner):
                    owner = None
                meta = upload_handler.save_upload(u, client_ip, owner=owner)
                promoted_gallery_id = _promote_chat_image_to_gallery(meta, owner, session_id, gallery_id)
                item = {
                    "id": meta["id"],
                    "name": meta["name"],
                    "mime": meta["mime"],
                    "size": meta["size"],
                    "hash": meta["hash"],
                    "checksum_sha256": meta.get("checksum_sha256") or meta["hash"],
                    "uploaded_at": meta["uploaded_at"],
                    "created_at": meta.get("created_at") or meta["uploaded_at"],
                    "width": meta.get("width"),
                    "height": meta.get("height"),
                    "is_duplicate": meta.get("is_duplicate", False)
                }
                if promoted_gallery_id:
                    item["gallery_id"] = promoted_gallery_id
                out.append(item)
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
        try:
            cleaned_count = await asyncio.to_thread(
                _run_reference_safe_cleanup,
                upload_handler,
            )
        except UploadCleanupSafetyError:
            logger.exception("Upload cleanup aborted because index safety checks failed")
            raise HTTPException(
                503,
                "Upload cleanup aborted because upload index integrity could not be verified",
            )
        except Exception:
            logger.exception("Upload cleanup skipped because reference discovery failed")
            raise HTTPException(
                503,
                "Upload cleanup skipped because persisted references could not be verified",
            )
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

    @router.api_route("/{file_id}", methods=["GET", "HEAD"])
    async def download_file(request: Request, file_id: str, thumb: int = 0):
        """Serve an uploaded file by its ID or probe via HEAD. `?thumb=1` returns
        a small cached JPEG thumbnail for images (used by chat attachment previews)
        so the client isn't downloading the full-resolution photo just to show it tiny."""
        if not upload_handler.validate_upload_id(file_id):
            raise HTTPException(400, "Invalid file ID")
        safe_file_id = secure_filename(os.path.basename(file_id))
        import mimetypes as _mt
        # Look up original filename and owner from uploads.json
        original_name = safe_file_id
        # _load_upload_index() tolerates a missing/corrupt uploads.json (it falls
        # back to the .bak sibling, then to {}), so a truncated DB degrades to
        # "no metadata" instead of a 500 from an unhandled JSONDecodeError.
        db = upload_handler._load_upload_index()
        info = next((fi for fi in db.values() if fi.get("id") == safe_file_id), None)
        if info:
            original_name = info.get("name", safe_file_id)
        file_owner = info.get("owner") if info else None
        _verify_upload_access(request, file_owner)
        upload_root = _upload_root()
        path = _resolve_upload_path(safe_file_id)
        try:
            path = confine(upload_root, path, allow_root=False)
        except (PathEscape, ValueError):
            raise HTTPException(403, "Access denied")
        if not path.startswith(upload_root + os.sep) or not _path_inside_upload_dir(path):
            raise HTTPException(403, "Access denied")
        mime = (info or {}).get("mime") or _mt.guess_type(path)[0] or "application/octet-stream"
        from fastapi.responses import FileResponse
        # Downscaled thumbnail for image previews — generated once and cached.
        # Skip generation during HEAD probe to avoid loading/processing whole image.
        if thumb and mime.startswith("image/"):
            thumb_dir = os.path.normpath(os.path.join(upload_root, ".thumbs"))
            thumb_path = os.path.normpath(os.path.join(thumb_dir, safe_file_id + ".jpg"))
            try:
                thumb_path = confine(upload_root, thumb_path, allow_root=False)
            except (PathEscape, ValueError):
                raise HTTPException(403, "Access denied")
            if not thumb_path.startswith(thumb_dir + os.sep) or not _path_inside_upload_dir(thumb_path):
                raise HTTPException(403, "Access denied")
            if request.method == "HEAD":
                if (os.path.isfile(thumb_path)
                        and os.path.getmtime(thumb_path) >= os.path.getmtime(path)):
                    return FileResponse(thumb_path, media_type="image/jpeg", headers=UPLOAD_RESPONSE_HEADERS)
                # GET may generate a JPEG or fall back to the original on failure.
                # Neither representation is known without decoding the image. HEAD
                # must not advertise the original/stale file's headers as a thumb.
                from fastapi.responses import Response
                response = Response(status_code=200, headers=UPLOAD_RESPONSE_HEADERS)
                del response.headers["content-length"]
                return response
            else:
                try:
                    from PIL import Image, ImageOps
                    os.makedirs(thumb_dir, exist_ok=True)
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
        # Corruption-tolerant load (see download_file): a bad uploads.json yields
        # {} rather than raising JSONDecodeError out of the vision path.
        db = upload_handler._load_upload_index()
        return next((fi for fi in db.values() if fi.get("id") == file_id), None)

    def _vision_cache_path(file_id: str) -> str:
        safe_id = secure_filename(os.path.basename(file_id))
        cache_dir = os.path.normpath(os.path.join(_upload_root(), ".vision"))
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.normpath(os.path.join(cache_dir, safe_id + ".txt"))

    def _sync_gallery_caption_for_upload(info: dict | None, owner: str | None, text: str) -> None:
        """Copy upload OCR/vision text onto the promoted gallery image row."""
        if not info:
            return
        file_hash = info.get("hash")
        if not file_hash:
            return
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(
                GalleryImage.file_hash == file_hash,
                GalleryImage.is_active == True,  # noqa: E712
            )
            if owner:
                q = q.filter(GalleryImage.owner == owner)
            img = q.first()
            if not img:
                return
            img.caption = (text or "").strip()
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Failed to sync OCR caption to gallery image: %s", e)
        finally:
            db.close()

    @router.get("/{file_id}/vision")
    async def get_vision_text(request: Request, file_id: str, force: int = 0):
        """Return the vision-model OCR/description for an uploaded image.
        Cached under UPLOAD_DIR/.vision/{file_id}.txt — first call computes,
        subsequent loads are instant. Pass force=1 to recompute."""
        if not upload_handler.validate_upload_id(file_id):
            raise HTTPException(400, "Invalid file ID")
        info = _load_upload_info(file_id)
        file_owner = info.get("owner") if info else None
        _verify_upload_access(request, file_owner)
        current_user = effective_user(request)
        path = _resolve_upload_path(file_id)
        import mimetypes as _mt
        mime = (info or {}).get("mime") or _mt.guess_type(path)[0] or ""
        if not mime.startswith("image/"):
            raise HTTPException(400, "Not an image")
        cache_path = _vision_cache_path(file_id)
        if not force and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    cached_text = f.read()
                _sync_gallery_caption_for_upload(info, file_owner or current_user, cached_text)
                return {"text": cached_text, "cached": True}
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
        _sync_gallery_caption_for_upload(info, file_owner or current_user, text)
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
        file_owner = info.get("owner")
        _verify_upload_access(request, file_owner, write=True)
        current_user = effective_user(request)
        _resolve_upload_path(file_id)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "Request body must be valid JSON")
        text = (body or {}).get("text", "")
        if not isinstance(text, str):
            raise HTTPException(400, "text must be a string")
        with open(_vision_cache_path(file_id), "w", encoding="utf-8") as f:
            f.write(text)
        _sync_gallery_caption_for_upload(info, file_owner or current_user, text)
        return {"ok": True}

    async def periodic_rate_limit_cleanup():
        """Background task to run cleanup every hour"""
        while True:
            await asyncio.sleep(3600)
            upload_handler.cleanup_rate_limits()
    
    return router, periodic_rate_limit_cleanup
