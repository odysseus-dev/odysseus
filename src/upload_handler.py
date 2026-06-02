# src/upload_handler.py
import os
import re
import json
import uuid
import time
import hashlib
import mimetypes
import shutil
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from fastapi import HTTPException, UploadFile
def secure_filename(filename: str) -> str:
    """Sanitize a filename (replaces werkzeug.utils.secure_filename)."""
    import unicodedata
    filename = unicodedata.normalize("NFKD", filename)
    filename = filename.encode("ascii", "ignore").decode("ascii")
    # Replace path separators with underscores
    for sep in (os.sep, os.altsep or "", "/", "\\"):
        if sep:
            filename = filename.replace(sep, "_")
    # Keep only safe characters
    filename = re.sub(r"[^\w\s\-.]", "", filename).strip()
    filename = re.sub(r"[\s]+", "_", filename)
    # Don't allow dotfiles
    filename = filename.lstrip(".")
    return filename or "unnamed"
import logging

logger = logging.getLogger(__name__)

# The extension is optional: save_upload builds the id as `{uuid.hex}{ext}`,
# and a file with no extension (Dockerfile, README, ...) yields a bare 32-hex
# id. Requiring `.ext` made those ids fail validation, so the stored file
# could never be resolved or downloaded again.
UPLOAD_ID_RE = re.compile(r"^[0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?$")


def is_valid_upload_id(upload_id: str) -> bool:
    """Return True when *upload_id* matches the canonical uploads.json id format."""
    return UPLOAD_ID_RE.fullmatch(upload_id or "") is not None


class UploadHandler:
    def __init__(self, base_dir: str, upload_dir: str):
        self.base_dir = base_dir
        self.upload_dir = upload_dir
        self.max_upload_size = 10 * 1024 * 1024  # 10MB
        self.max_concurrent_uploads = 3
        self.cleanup_days = 30
        self.upload_rate_limit = 5  # Max 5 uploads per minute per IP
        self.upload_rate_window = 60  # 60 seconds
        
        # Track upload rates
        self.upload_rate_log: Dict[str, list] = {}
        self._upload_rate_lock = threading.Lock()
        self._upload_rate_counter = 0
        self._upload_rate_max_entries = 1000
        # Serialise the read-modify-write of uploads.json within one
        # Python process. Scope: single FastAPI worker (the default
        # uvicorn deployment). Cross-process / multi-worker deployments
        # need an additional file-level lock (flock) or a database;
        # the atomic-rename write below keeps on-disk state consistent
        # on its own but does not serialise writers across processes.
        self._index_lock = threading.Lock()
        
        # Create upload directory
        os.makedirs(self.upload_dir, exist_ok=True)
        
        # Initialize file detector
        try:
            import magic
            self.file_detector = magic.Magic(mime=True)
        except Exception:
            self.file_detector = None
            logger.warning("python-magic not available, falling back to basic detection")
    
    def inside_base_dir(self, path: str) -> bool:
        """Check if path is inside base directory"""
        base = os.path.realpath(self.base_dir)
        p = os.path.realpath(path)
        try:
            return os.path.commonpath([base, p]) == base
        except Exception:
            return False
    
    def get_upload_dir(self):
        """Get date-based upload directory"""
        now = datetime.now()
        upload_dir = os.path.join(self.upload_dir, now.strftime("%Y"), now.strftime("%m"), now.strftime("%d"))
        os.makedirs(upload_dir, exist_ok=True)
        return upload_dir
    
    def calculate_file_hash(self, file_obj) -> str:
        """Calculate SHA-256 hash of file content."""
        file_obj.seek(0)
        hash_sha256 = hashlib.sha256()
        for chunk in iter(lambda: file_obj.read(4096), b""):
            hash_sha256.update(chunk)
        file_obj.seek(0)
        return hash_sha256.hexdigest()
    
    def detect_content_type(self, file_obj, original_filename: str) -> str:
        """Detect MIME type based on file content, with extension fallback."""
        content_type = "application/octet-stream"
        if self.file_detector:
            try:
                file_obj.seek(0)
                content_type = self.file_detector.from_buffer(file_obj.read(1024))
                file_obj.seek(0)
            except Exception as e:
                logger.warning(f"Failed to detect content type: {e}")
        
        if not content_type or content_type == "application/octet-stream":
            _, ext = os.path.splitext(original_filename.lower())
            if ext:
                content_type = mimetypes.guess_type(original_filename)[0] or content_type
        
        return content_type
        
    def is_image_file(self, filename: str, content_type: str = None) -> bool:
        """Check if a file is an image based on extension or content type."""
        image_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
        image_mime_types = {
            'image/png', 'image/jpeg', 'image/jpg', 'image/webp', 'image/gif'
        }
        
        # Check by extension
        _, ext = os.path.splitext(filename.lower())
        if ext in image_extensions:
            return True
            
        # Check by content type if provided
        if content_type and content_type in image_mime_types:
            return True
            
        return False
        
    def is_document_file(self, filename: str, content_type: str = None) -> bool:
        """Check if a file is a document based on extension or content type."""
        document_extensions = {
            '.pdf', '.docx', '.xlsx', '.pptx', '.xls', '.epub',
            '.txt', '.py', '.js', '.html', '.htm',
            '.css', '.json', '.md', '.csv', '.log', '.xml', '.yml', 
            '.yaml', '.sql', '.sh', '.bash', '.c', '.cpp', '.h', 
            '.java', '.go', '.rs', '.php', '.rb', '.ts', '.jsx', '.tsx'
        }
        document_mime_types = {
            'application/pdf', 
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'application/vnd.ms-excel',
            'application/epub+zip',
            'text/plain'
        }
        
        # Check by extension
        _, ext = os.path.splitext(filename.lower())
        if ext in document_extensions:
            return True
            
        # Check by content type if provided
        if content_type and content_type in document_mime_types:
            return True
            
        return False
            
    def is_audio_file(self, filename: str, content_type: str = None) -> bool:
        """Check if a file is an audio file based on extension or content type."""
        audio_extensions = {'.webm', '.wav', '.mp3', '.m4a', '.ogg'}
        audio_mime_types = {
            'audio/webm', 'audio/wav', 'audio/mpeg', 'audio/mp4', 'audio/ogg'
        }
        
        # Check by extension
        _, ext = os.path.splitext(filename.lower())
        if ext in audio_extensions:
            return True
            
        # Check by content type if provided
        if content_type and content_type in audio_mime_types:
            return True
            
        return False
    
    def is_safe_file_type(self, content_type: str, filename: str) -> bool:
        """Check if file type is safe to store and serve."""
        dangerous_types = {
            'application/x-executable', 'application/x-sharedlib',
            'application/x-dll', 'application/x-msdownload',
            'application/x-sh', 'application/x-bat', 'application/x-vbs',
            'application/javascript', 'application/x-javascript'
        }
        
        dangerous_extensions = {
            '.exe', '.dll', '.bat', '.cmd', '.vbs', 
            '.ps1', '.jsp', '.asp', '.aspx'
        }
        
        if content_type in dangerous_types:
            return False
        
        _, ext = os.path.splitext(filename.lower())
        if ext in dangerous_extensions:
            return False
        
        return True
    
    def cleanup_old_uploads(self):
        """Remove uploaded files older than CLEANUP_DAYS days."""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.cleanup_days)
            cleaned_count = 0
            
            for root, dirs, files in os.walk(self.upload_dir):
                if root == self.upload_dir:
                    continue
                    
                path_parts = root.split(os.sep)
                if len(path_parts) >= 4:
                    try:
                        dir_date = datetime(int(path_parts[-3]), int(path_parts[-2]), int(path_parts[-1]))
                        if dir_date < cutoff_date:
                            for file in files:
                                file_path = os.path.join(root, file)
                                try:
                                    os.remove(file_path)
                                    cleaned_count += 1
                                    logger.info(f"Cleaned up old upload: {file_path}")
                                except Exception as e:
                                    logger.warning(f"Failed to remove {file_path}: {e}")
                            
                            try:
                                os.rmdir(root)
                                logger.info(f"Removed empty upload directory: {root}")
                            except Exception as e:
                                logger.warning(f"Failed to remove directory {root}: {e}")
                    except (ValueError, IndexError):
                        continue
            
            logger.info(f"Upload cleanup completed: {cleaned_count} files removed")
            return cleaned_count
        except Exception as e:
            logger.error(f"Upload cleanup failed: {e}")
            return 0
    
    def validate_upload_id(self, upload_id: str) -> bool:
        """Validate that the upload ID matches the expected pattern."""
        return is_valid_upload_id(upload_id)

    def _inside_upload_dir(self, path: str) -> bool:
        """Check if path is inside the upload directory."""
        base = os.path.realpath(self.upload_dir)
        p = os.path.realpath(path)
        try:
            return os.path.commonpath([base, p]) == base
        except Exception:
            return False

    def _atomic_write_json(self, path: str, data: dict) -> None:
        """Write `data` to `path` atomically: write to a temp file in the
        same directory, then `os.replace` onto the target. The kernel
        guarantees `os.replace` is atomic on POSIX, so a reader either
        sees the old contents or the new contents, never a half-written
        file. Also keeps a `.bak` sibling of the previous good state.
        """
        directory = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".uploads-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(path):
                bak = path + ".bak"
                try:
                    shutil.copy2(path, bak)
                except OSError:
                    pass
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _load_upload_index(self) -> Dict[str, Any]:
        uploads_db_path = os.path.join(self.upload_dir, "uploads.json")
        if not os.path.exists(uploads_db_path):
            return {}
        # Try the live file first, fall back to the .bak sibling if the
        # live file is truncated/corrupted (e.g. a previous writer was
        # SIGKILL'd mid-rename before the new code path was deployed).
        for candidate in (uploads_db_path, uploads_db_path + ".bak"):
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except Exception as e:
                logger.warning(f"Failed to read uploads database ({candidate}): {e}")
                continue
        return {}

    def get_upload_info(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """Return the uploads.json metadata row for an upload ID, if present."""
        if not self.validate_upload_id(upload_id):
            return None
        for info in self._load_upload_index().values():
            if isinstance(info, dict) and info.get("id") == upload_id:
                return dict(info)
        return None

    def _find_upload_path(self, upload_id: str) -> Optional[str]:
        """Find an upload file by ID while staying inside upload_dir."""
        if not self.validate_upload_id(upload_id):
            return None

        direct = os.path.join(self.upload_dir, upload_id)
        if os.path.exists(direct) and self._inside_upload_dir(direct):
            return direct

        for root, _dirs, files in os.walk(self.upload_dir, followlinks=False):
            if upload_id in files:
                path = os.path.join(root, upload_id)
                if self._inside_upload_dir(path):
                    return path
        return None

    def resolve_upload(
        self,
        upload_id: str,
        owner: Optional[str] = None,
        auth_manager: Any = None,
        allow_admin: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Resolve an upload ID to metadata only if the caller may read it.

        This is the owner-aware lookup used by internal processors. Public
        download routes already perform owner checks; chat/document paths must
        do the same before reading file bytes server-side.
        """
        if not self.validate_upload_id(upload_id):
            logger.warning(f"Invalid upload ID format: {upload_id}")
            return None

        auth_configured = bool(auth_manager and getattr(auth_manager, "is_configured", False))
        if auth_configured and not owner:
            return None

        info = self.get_upload_info(upload_id) or {}
        is_admin = False
        if allow_admin and owner and auth_manager and hasattr(auth_manager, "is_admin"):
            try:
                is_admin = bool(auth_manager.is_admin(owner))
            except Exception:
                is_admin = False

        if owner and not is_admin:
            if info.get("owner") != owner:
                logger.warning("Upload %s denied for owner %s", upload_id, owner)
                return None
        if not owner and info.get("owner") is not None:
            logger.warning("Upload %s denied without an authenticated owner", upload_id)
            return None

        path = info.get("path")
        if not path or not os.path.exists(path) or not self._inside_upload_dir(path):
            path = self._find_upload_path(upload_id)
        if not path:
            return None
        if not self._inside_upload_dir(path):
            logger.warning(f"Upload path outside upload directory: {path}")
            return None

        resolved = dict(info)
        resolved.setdefault("id", upload_id)
        resolved["path"] = path
        resolved.setdefault("name", os.path.basename(path))
        resolved.setdefault("original_name", resolved["name"])
        resolved.setdefault("mime", mimetypes.guess_type(path)[0] or "application/octet-stream")
        return resolved
    
    def cleanup_rate_limits(self):
        """Remove stale entries from upload_rate_log."""
        now = time.time()
        removed_ips = 0
        removed_timestamps = 0
        
        with self._upload_rate_lock:
            ips_to_delete = []
            for ip, timestamps in list(self.upload_rate_log.items()):
                new_ts = [t for t in timestamps if now - t < self.upload_rate_window]
                removed = len(timestamps) - len(new_ts)
                removed_timestamps += removed
                if new_ts:
                    self.upload_rate_log[ip] = new_ts
                else:
                    ips_to_delete.append(ip)
            
            for ip in ips_to_delete:
                del self.upload_rate_log[ip]
                removed_ips += 1
            
            if len(self.upload_rate_log) > self._upload_rate_max_entries:
                sorted_ips = sorted(
                    self.upload_rate_log.items(),
                    key=lambda item: max(item[1]) if item[1] else 0,
                    reverse=True
                )
                keep = dict(sorted_ips[:self._upload_rate_max_entries])
                dropped = len(self.upload_rate_log) - len(keep)
                self.upload_rate_log = keep
                logger.info(f"Rate-limit dict size exceeded. Dropped {dropped} oldest IP entries.")
        
        logger.info(f"Rate-limit cleanup: removed {removed_ips} IPs, {removed_timestamps} timestamps.")
    
    def get_upload_stats(self) -> Dict[str, Any]:
        """Get statistics about uploaded files."""
        try:
            total_files = 0
            total_size = 0
            file_types = {}
            
            uploads_db_path = os.path.join(self.upload_dir, "uploads.json")
            if os.path.exists(uploads_db_path):
                with open(uploads_db_path, "r", encoding="utf-8") as f:
                    files = json.load(f)
                
                total_files = len(files)
                for file_info in files.values():
                    total_size += file_info.get("size", 0)
                    mime = file_info.get("mime", "unknown")
                    file_types[mime] = file_types.get(mime, 0) + 1
            
            return {
                "total_files": total_files,
                "total_size": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "file_types": file_types,
                "cleanup_days": self.cleanup_days
            }
        except Exception as e:
            logger.error(f"Failed to get upload stats: {e}")
            return {"error": str(e)}
    
    def save_upload(self, u: UploadFile, client_ip: str, owner: str = None) -> dict:
        """Save uploaded file with enhanced security and organization."""
        # Rate limiting
        now = time.time()
        with self._upload_rate_lock:
            if client_ip not in self.upload_rate_log:
                self.upload_rate_log[client_ip] = []
            
            self.upload_rate_log[client_ip] = [
                timestamp for timestamp in self.upload_rate_log[client_ip]
                if now - timestamp < self.upload_rate_window
            ]
            
            if len(self.upload_rate_log[client_ip]) >= self.upload_rate_limit:
                raise HTTPException(
                    status_code=429,
                    detail="Upload rate limit exceeded. Please try again later."
                )
            
            self.upload_rate_log[client_ip].append(now)
            self._upload_rate_counter += 1
        
        if self._upload_rate_counter % 100 == 0:
            self.cleanup_rate_limits()
        
        # Validate file size
        file_obj = u.file
        file_obj.seek(0, 2)
        file_size = file_obj.tell()
        file_obj.seek(0)
        
        if file_size == 0:
            raise HTTPException(400, "File is empty")
            
        if file_size > self.max_upload_size:
            raise HTTPException(
                status_code=400,
                detail=f"File size exceeds {self.max_upload_size/1024/1024}MB limit"
            )
        
        # Get original filename and sanitize it
        original_filename = u.filename or f"upload_{int(time.time())}"
        safe_filename = secure_filename(original_filename)
        
        # Detect content type
        content_type = self.detect_content_type(file_obj, safe_filename)
        
        # Check if file type is safe
        if not self.is_safe_file_type(content_type, safe_filename):
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed: {content_type}"
            )
        
        # Calculate file hash for deduplication
        file_hash = self.calculate_file_hash(file_obj)
        
        # Check for duplicate files.
        # The duplicate-detection lookup AND the write must both happen
        # under _index_lock: a duplicate upload racing with a new-entry
        # insert must not overwrite a newer snapshot of the index with
        # the stale one read before the insert.
        uploads_db_path = os.path.join(self.upload_dir, "uploads.json")
        existing_file = None
        existing_key = None
        with self._index_lock:
            existing_files = self._load_upload_index()
            stale_keys = []
            for key, info in existing_files.items():
                if info.get("hash") == file_hash and info.get("owner") == owner:
                    stored_path = info.get("path")
                    if stored_path and os.path.exists(stored_path) and self._inside_upload_dir(stored_path):
                        existing_key = key
                        existing_file = info
                        break
                    stale_keys.append(key)
            if stale_keys:
                for key in stale_keys:
                    existing_files.pop(key, None)
                try:
                    self._atomic_write_json(uploads_db_path, existing_files)
                    logger.info("Removed %d stale upload index entries for missing duplicates", len(stale_keys))
                except Exception as e:
                    logger.warning(f"Failed to remove stale upload index entries: {e}")
        if existing_file:
            logger.info(f"Duplicate file upload detected: {original_filename} -> {existing_file['id']}")

            existing_file["last_accessed"] = datetime.now().isoformat()
            with self._index_lock:
                try:
                    current = self._load_upload_index()
                    # Re-resolve the key inside the lock: a concurrent
                    # insert can have changed the dict's keys.
                    live_key = existing_key
                    if live_key not in current:
                        for k, v in current.items():
                            if v.get("hash") == file_hash and v.get("owner") == owner:
                                live_key = k
                                existing_file = v
                                break
                    if live_key is None:
                        # No matching entry anymore (e.g. cleaned up between
                        # the outer read and the write). Fall through to the
                        # fresh-insert path below; release the lock first.
                        raise LookupError("upload entry vanished mid-dedupe")
                    existing_file["last_accessed"] = datetime.now().isoformat()
                    current[live_key] = existing_file
                    self._atomic_write_json(uploads_db_path, current)
                except LookupError:
                    existing_file = None
                except Exception as e:
                    logger.warning(f"Failed to update uploads database: {e}")

            if existing_file:
                return {
                    "id": existing_file["id"],
                    "path": existing_file["path"],
                    "mime": existing_file["mime"],
                    "size": existing_file["size"],
                    "name": existing_file["original_name"],
                    "hash": file_hash,
                    "uploaded_at": existing_file["uploaded_at"],
                    "owner": existing_file.get("owner"),
                    "width": existing_file.get("width"),
                    "height": existing_file.get("height"),
                    "is_duplicate": True
                }
        
        # Generate unique ID and determine save location
        _, ext = os.path.splitext(safe_filename)
        file_id = f"{uuid.uuid4().hex}{ext}"
        
        # Create date-based directory structure
        upload_dir = self.get_upload_dir()
        file_path = os.path.join(upload_dir, file_id)
        
        # Save the file
        try:
            with open(file_path, "wb") as f:
                while chunk := file_obj.read(8192):
                    f.write(chunk)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")
        
        # Create file metadata
        file_metadata = {
            "id": file_id,
            "path": file_path,
            "mime": content_type,
            "size": file_size,
            "name": safe_filename,
            "hash": file_hash,
            "original_name": original_filename,
            "uploaded_at": datetime.now().isoformat(),
            "last_accessed": datetime.now().isoformat(),
            "client_ip": client_ip,
            "owner": owner,
        }
        # Capture image dimensions (EXIF-rotated) so the chat thumbnail skeleton
        # can size itself to the right aspect ratio before the bytes arrive.
        if content_type.startswith("image/"):
            try:
                from PIL import Image, ImageOps
                with Image.open(file_path) as _im:
                    _im = ImageOps.exif_transpose(_im)
                    file_metadata["width"] = _im.width
                    file_metadata["height"] = _im.height
            except Exception as e:
                logger.warning(f"Failed to read image dimensions for {file_id}: {e}")
        
        # Update uploads database
        with self._index_lock:
            try:
                current = self._load_upload_index() if os.path.exists(uploads_db_path) else {}
                storage_key = f"{owner}:{file_hash}" if owner else file_hash
                current[storage_key] = file_metadata
                self._atomic_write_json(uploads_db_path, current)
            except Exception as e:
                logger.warning(f"Failed to update uploads database: {e}")
        
        logger.info(f"File uploaded successfully: {original_filename} ({file_size} bytes)")
        return file_metadata
