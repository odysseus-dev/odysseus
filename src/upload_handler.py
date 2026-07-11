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

from src.upload_limits import format_byte_limit, get_chat_upload_max_bytes


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


class UploadCleanupSafetyError(RuntimeError):
    """Raised when cleanup cannot prove that destructive work is safe."""

# The extension is optional: save_upload builds the id as `{uuid.hex}{ext}`,
# and a file with no extension (Dockerfile, README, ...) yields a bare 32-hex
# id. Requiring `.ext` made those ids fail validation, so the stored file
# could never be resolved or downloaded again.
UPLOAD_ID_RE = re.compile(r"^[0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?$")
UPLOAD_ID_TOKEN_RE = re.compile(
    r"(?<![0-9a-fA-F])([0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?)(?![A-Za-z0-9])"
)
INTERNAL_UPLOAD_URL_RE = re.compile(
    r"(?:odysseus://attachment/|/api/upload/)"
    r"([0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?)"
    r"(?=$|[\s\"'<>\[\](){},;!?:&#]|\.(?![A-Za-z0-9]))"
)
PDF_SOURCE_UPLOAD_RE = re.compile(
    r"<!--\s*pdf(?:_form)?_source\b[^>]*\bupload_id="
    r"[\"']([0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?)[\"'][^>]*-->",
    re.IGNORECASE,
)
ATTACHMENT_REFERENCE_LINE_RE = re.compile(
    r"\[Attachment:[^\]\r\n]*\|\s*id="
    r"([0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?)"
    r"(?:\s*\||\s*\])",
    re.IGNORECASE,
)


def is_valid_upload_id(upload_id: str) -> bool:
    """Return True when *upload_id* matches the canonical uploads.json id format."""
    return UPLOAD_ID_RE.fullmatch(upload_id or "") is not None


def extract_upload_ids(value: Any) -> set[str]:
    """Return canonical upload IDs embedded in a persisted URL/text value."""
    if not isinstance(value, str) or not value:
        return set()
    return set(UPLOAD_ID_TOKEN_RE.findall(value))


def extract_internal_upload_ids(value: Any) -> set[str]:
    """Return IDs from explicit internal upload references only.

    Cleanup intentionally uses :func:`extract_upload_ids` conservatively, but
    write-time reservation must not treat an arbitrary 32-hex checksum in note
    or calendar text as an upload reference. Nested JSON-like values are
    supported because note checklist items are persisted as structured data.
    """
    if isinstance(value, dict):
        found: set[str] = set()
        for nested in value.values():
            found.update(extract_internal_upload_ids(nested))
        return found
    if isinstance(value, (list, tuple, set)):
        found: set[str] = set()
        for nested in value:
            found.update(extract_internal_upload_ids(nested))
        return found
    if not isinstance(value, str) or not value:
        return set()
    return (
        set(INTERNAL_UPLOAD_URL_RE.findall(value))
        | set(PDF_SOURCE_UPLOAD_RE.findall(value))
        | set(ATTACHMENT_REFERENCE_LINE_RE.findall(value))
    )


def reserve_upload_references(
    upload_handler: Any,
    owner: Optional[str],
    *values: Any,
) -> Optional[str]:
    """Reserve upload IDs in values before a caller persists references.

    Returns the first ID that cannot be owner-checked/reserved, otherwise
    ``None``. A missing handler is treated as no-op for backward-compatible
    route factories; production wires the shared UploadHandler instance.
    """
    if upload_handler is None:
        return None
    upload_ids: set[str] = set()
    for value in values:
        upload_ids.update(extract_internal_upload_ids(value))
    return reserve_upload_ids(upload_handler, owner, upload_ids)


def reserve_upload_ids(
    upload_handler: Any,
    owner: Optional[str],
    upload_ids: Any,
) -> Optional[str]:
    """Owner-reserve canonical IDs from a trusted structured reference field."""
    if upload_handler is None:
        return None
    canonical_ids = {
        str(upload_id).strip()
        for upload_id in (upload_ids or [])
        if is_valid_upload_id(str(upload_id).strip())
    }
    for upload_id in sorted(canonical_ids):
        try:
            resolved = upload_handler.reserve_upload(
                upload_id,
                owner=owner,
                allow_admin=False,
            )
        except Exception:
            resolved = None
        if not resolved:
            return upload_id
    return None


def reserve_message_upload_references(
    upload_handler: Any,
    owner: Optional[str],
    content: Any,
    metadata: Any = None,
) -> Optional[str]:
    """Reserve explicit chat references, including structured attachment IDs."""
    upload_ids = extract_internal_upload_ids(content)
    if metadata not in (None, ""):
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        if not isinstance(metadata, dict):
            raise ValueError("message metadata must be a JSON object")
        upload_ids.update(extract_internal_upload_ids(metadata))
        from src.attachment_refs import attachment_refs_from_metadata

        upload_ids.update(
            str(ref.get("attachment_id") or "").strip()
            for ref in attachment_refs_from_metadata(metadata)
            if ref.get("attachment_id")
        )
    return reserve_upload_ids(upload_handler, owner, upload_ids)


def _build_upload_id(safe_filename: str) -> str:
    """Build a unique upload id whose extension matches UPLOAD_ID_RE.

    secure_filename keeps '_' and '-', so an extension like '.jpg-1' (the
    suffix browsers append to duplicate downloads) or '.v1_final' produced an
    id that failed is_valid_upload_id, making the saved file permanently
    unreadable (every read path gates on validate_upload_id). Sanitize the
    extension to the single-alnum shape the id contract requires.
    """
    _, ext = os.path.splitext(safe_filename or "")
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)
    return uuid.uuid4().hex + (("." + ext) if ext else "")


def count_recent_uploads(timestamps, now: float, window: float = 10.0) -> int:
    """Number of upload events in *timestamps* within the last *window* seconds.

    Used by the per-IP concurrency guard. The count is of genuine prior upload
    events — it must NOT scale with how many files are in the *current* request,
    or a single multi-file batch would reject itself (issue #1346)."""
    if not timestamps:
        return 0
    cutoff = now - window
    return sum(1 for t in timestamps if t > cutoff)


class UploadHandler:
    def __init__(self, base_dir: str, upload_dir: str):
        self.base_dir = base_dir
        self.upload_dir = upload_dir
        self.max_upload_size = get_chat_upload_max_bytes()
        self.max_concurrent_uploads = 3
        self.cleanup_days = 30
        # Per-IP per-minute cap. save_upload() counts EACH file, and the chat
        # composer lets a user attach up to MAX_FILES (10, static/js/fileHandler.js)
        # in one batch — so this must comfortably exceed 10, or a single 6+ file
        # attach is rejected mid-batch (issue #1346: "5 work, 6 fail"). Burst abuse
        # is separately bounded by max_concurrent_uploads. Headroom for a few full
        # batches per minute.
        self.upload_rate_limit = 60  # max 60 file-uploads per minute per IP
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

        # In-memory index cache to avoid O(N) disk I/O on every request
        self._index_cache: Optional[Dict[str, Any]] = None
        self._index_mtime: float = 0.0
    
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
            '.yaml', '.nix', '.sql', '.sh', '.bash', '.c', '.cpp', '.h',
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
    
    @staticmethod
    def _parse_upload_timestamp(value: Any) -> Optional[datetime]:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except (TypeError, ValueError):
            return None

    @classmethod
    def _upload_metadata_is_recent(cls, info: Dict[str, Any], cutoff_date: datetime) -> bool:
        """Return True when upload metadata records activity inside retention."""
        for field in ("last_accessed", "created_at", "uploaded_at"):
            parsed = cls._parse_upload_timestamp(info.get(field))
            if parsed is None:
                continue
            if parsed >= cutoff_date:
                return True
        return False

    @classmethod
    def _upload_index_keys_for_file(
        cls,
        upload_index: Dict[str, Any],
        upload_id: str,
        file_path: str,
    ) -> list[str]:
        """Find a coherent set of index rows for one physical upload.

        Every related row must agree on ID, canonical path, owner, and a
        non-empty checksum. Each row must also contain the complete lifecycle
        timestamps written for new uploads. Ambiguous or incomplete index
        state cannot authorize destructive cleanup.
        """
        target_path = os.path.normcase(os.path.realpath(file_path))
        matches: list[str] = []
        owners: set[str] = set()
        checksums: set[str] = set()
        for key, info in upload_index.items():
            if not isinstance(info, dict):
                continue
            stored_path = info.get("path")
            stored_real_path = (
                os.path.normcase(os.path.realpath(stored_path))
                if isinstance(stored_path, str) and stored_path
                else None
            )
            same_id = info.get("id") == upload_id
            same_path = stored_real_path == target_path
            if not same_id and not same_path:
                continue
            if not same_id or not same_path:
                logger.warning(
                    "Skipping ambiguous cleanup candidate %s: related row has id=%r path=%r",
                    file_path,
                    info.get("id"),
                    stored_path,
                )
                return []

            owner = info.get("owner")
            if not isinstance(owner, str) or not owner.strip():
                logger.warning(
                    "Skipping incomplete cleanup candidate %s: matching row has no owner",
                    file_path,
                )
                return []

            row_checksums = {
                str(info.get(field)).strip().lower()
                for field in ("hash", "checksum_sha256")
                if info.get(field) is not None and str(info.get(field)).strip()
            }
            if not row_checksums:
                logger.warning(
                    "Skipping incomplete cleanup candidate %s: matching row has no checksum",
                    file_path,
                )
                return []
            if len(row_checksums) != 1:
                logger.warning(
                    "Skipping ambiguous cleanup candidate %s: matching row has conflicting checksums",
                    file_path,
                )
                return []

            lifecycle_fields = ("uploaded_at", "created_at", "last_accessed")
            if any(
                cls._parse_upload_timestamp(info.get(field)) is None
                for field in lifecycle_fields
            ):
                logger.warning(
                    "Skipping incomplete cleanup candidate %s: matching row lacks lifecycle timestamps",
                    file_path,
                )
                return []

            matches.append(key)
            owners.add(owner)
            checksums.update(row_checksums)

        if len(owners) > 1 or len(checksums) > 1:
            logger.warning(
                "Skipping ambiguous cleanup candidate %s: matching rows disagree on owner or checksum",
                file_path,
            )
            return []
        return matches

    def cleanup_old_uploads(
        self,
        referenced_upload_ids: Optional[set[str]] = None,
        referenced_upload_hashes: Optional[set[str]] = None,
    ):
        """Remove expired uploads proven unreferenced by a complete snapshot.

        ``None`` means reference discovery was not completed, so cleanup fails
        closed and removes nothing. The admin route supplies both sets after
        scanning persisted chats, documents, and gallery records.
        """
        if referenced_upload_ids is None or referenced_upload_hashes is None:
            logger.warning("Upload cleanup skipped: persisted reference snapshot unavailable")
            return 0

        try:
            cleanup_started_at = datetime.now()
            cutoff_date = cleanup_started_at - timedelta(days=self.cleanup_days)
            cleaned_count = 0

            referenced_ids = {str(value) for value in referenced_upload_ids}
            referenced_hashes = {str(value) for value in referenced_upload_hashes}
            uploads_db_path = os.path.join(self.upload_dir, "uploads.json")

            # Keep index mutation and file removal serialized with upload writes.
            # Each row removal is atomically persisted before the bytes are
            # deleted; if deletion fails, the previous index is restored.
            with self._index_lock:
                current_index = dict(self._load_upload_index(fail_on_error=True))

                for root, dirs, files in os.walk(self.upload_dir, followlinks=False):
                    is_junction = getattr(os.path, "isjunction", lambda _path: False)
                    dirs[:] = [
                        directory
                        for directory in dirs
                        if not os.path.islink(os.path.join(root, directory))
                        and not is_junction(os.path.join(root, directory))
                    ]
                    if root == self.upload_dir:
                        continue
                    if not self._inside_upload_dir(root):
                        dirs[:] = []
                        continue

                    path_parts = root.split(os.sep)
                    if len(path_parts) < 4:
                        continue
                    try:
                        dir_date = datetime(int(path_parts[-3]), int(path_parts[-2]), int(path_parts[-1]))
                    except (ValueError, IndexError):
                        continue
                    if dir_date >= cutoff_date:
                        continue

                    for file in files:
                        # Reference discovery only understands canonical upload
                        # IDs; unknown files fail closed instead of being swept.
                        if not self.validate_upload_id(file):
                            continue

                        file_path = os.path.join(root, file)
                        if not self._inside_upload_dir(file_path):
                            logger.warning(
                                "Skipping cleanup candidate outside upload directory: %s",
                                file_path,
                            )
                            continue
                        matching_keys = self._upload_index_keys_for_file(
                            current_index,
                            file,
                            file_path,
                        )
                        matching_rows = [
                            current_index[key]
                            for key in matching_keys
                            if isinstance(current_index.get(key), dict)
                        ]

                        # Files without authoritative live index rows are not
                        # eligible for destructive cleanup. Reference hashes,
                        # recency, and ownership cannot be proven for them.
                        if not matching_rows:
                            continue

                        is_referenced = file in referenced_ids or any(
                            str(info.get("id") or "") in referenced_ids
                            or str(info.get("hash") or "") in referenced_hashes
                            or str(info.get("checksum_sha256") or "") in referenced_hashes
                            for info in matching_rows
                        )
                        metadata_is_recent = any(
                            self._upload_metadata_is_recent(info, cutoff_date)
                            for info in matching_rows
                        )
                        if is_referenced or metadata_is_recent:
                            continue

                        reduced_index = {
                            key: value
                            for key, value in current_index.items()
                            if key not in matching_keys
                        }
                        if matching_keys:
                            try:
                                self._atomic_write_json(
                                    uploads_db_path,
                                    reduced_index,
                                    sync_backup=True,
                                )
                            except Exception as e:
                                try:
                                    self._atomic_write_json(
                                        uploads_db_path,
                                        current_index,
                                        sync_backup=True,
                                    )
                                except Exception:
                                    logger.exception(
                                        "Failed to restore upload indexes after reconciliation failed for %s",
                                        file_path,
                                    )
                                    raise UploadCleanupSafetyError(
                                        "upload index rollback failed before file removal"
                                    ) from e
                                logger.warning(
                                    "Failed to reconcile upload index before removing %s: %s",
                                    file_path,
                                    e,
                                )
                                continue

                        try:
                            os.remove(file_path)
                        except FileNotFoundError:
                            # The bytes are already absent. Keep the reduced
                            # lifecycle index instead of recreating a stale row.
                            current_index = reduced_index
                            logger.info(
                                "Reconciled missing expired upload from index: %s",
                                file_path,
                            )
                            continue
                        except Exception as e:
                            if matching_keys:
                                try:
                                    self._atomic_write_json(
                                        uploads_db_path,
                                        current_index,
                                        sync_backup=True,
                                    )
                                except Exception:
                                    logger.exception(
                                        "Failed to restore upload index after removal failed for %s",
                                        file_path,
                                    )
                                    raise UploadCleanupSafetyError(
                                        "upload index rollback failed after file removal was refused"
                                    ) from e
                            logger.warning(f"Failed to remove {file_path}: {e}")
                            continue

                        current_index = reduced_index
                        cleaned_count += 1
                        logger.info(f"Cleaned up old unreferenced upload: {file_path}")

                    try:
                        if not os.listdir(root):
                            os.rmdir(root)
                            logger.info(f"Removed empty upload directory: {root}")
                    except Exception as e:
                        logger.warning(f"Failed to inspect/remove directory {root}: {e}")

            logger.info(f"Upload cleanup completed: {cleaned_count} files removed")
            return cleaned_count
        except Exception as e:
            logger.error(f"Upload cleanup failed: {e}")
            raise UploadCleanupSafetyError("upload cleanup safety checks failed") from e
    
    def validate_upload_id(self, upload_id: str) -> bool:
        """Validate that the upload ID matches the expected pattern."""
        return is_valid_upload_id(upload_id)

    def _inside_upload_dir(self, path: str) -> bool:
        """Check if path is inside the upload directory."""
        base = os.path.normcase(os.path.realpath(self.upload_dir))
        p = os.path.normcase(os.path.realpath(path))
        try:
            return os.path.commonpath([base, p]) == base
        except Exception:
            return False

    def _atomic_write_json(
        self,
        path: str,
        data: dict,
        *,
        sync_backup: bool = False,
    ) -> None:
        """Write `data` to `path` atomically: write to a temp file in the
        same directory, then `os.replace` onto the target. The kernel
        guarantees `os.replace` is atomic on POSIX, so a reader either
        sees the old contents or the new contents, never a half-written
        file. Normally `.bak` retains the previous good state. Destructive
        lifecycle transitions use ``sync_backup=True`` so recovery cannot
        resurrect metadata for bytes that were deliberately removed.
        """
        directory = os.path.dirname(path) or "."

        def _replace_json(target: str) -> None:
            fd, tmp = tempfile.mkstemp(
                prefix=".uploads-",
                suffix=".tmp",
                dir=directory,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, target)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

        if sync_backup:
            _replace_json(path + ".bak")
        elif os.path.exists(path):
            try:
                shutil.copy2(path, path + ".bak")
            except OSError:
                pass

        _replace_json(path)
        # Update cache if this is the main index
        if path.endswith("uploads.json"):
            self._index_cache = data
            try:
                self._index_mtime = os.path.getmtime(path)
            except OSError:
                self._index_mtime = time.time()

    def _load_upload_index(self, *, fail_on_error: bool = False) -> Dict[str, Any]:
        """Load the upload index from disk/cache. Uses mtime-based validation
        to avoid redundant parsing on hot paths. When ``fail_on_error`` is
        true, a missing, malformed, or unreadable live index raises so
        destructive callers cannot mistake corruption for an empty store.
        """
        uploads_db_path = os.path.join(self.upload_dir, "uploads.json")
        candidates = (uploads_db_path, uploads_db_path + ".bak")
        if fail_on_error:
            # A backup is intentionally the previous snapshot. It is useful for
            # non-destructive reads, but cannot authorize deletion when the live
            # index is missing or corrupt.
            if not os.path.exists(uploads_db_path):
                raise ValueError("live uploads database is missing")
            existing_candidates = [uploads_db_path]
        else:
            existing_candidates = [path for path in candidates if os.path.exists(path)]
        if not existing_candidates:
            self._index_cache = {}
            self._index_mtime = 0.0
            return {}

        # Check cache validity
        try:
            mtime = max(os.path.getmtime(path) for path in existing_candidates)
            if (
                not fail_on_error
                and self._index_cache is not None
                and mtime <= self._index_mtime
            ):
                return self._index_cache
        except OSError:
            mtime = 0.0

        # Try the live file first, fall back to the .bak sibling if the
        # live file is truncated/corrupted.
        for candidate in existing_candidates:
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._index_cache = data
                    self._index_mtime = mtime
                    return data
            except Exception as e:
                logger.warning(f"Failed to read uploads database ({candidate}): {e}")
                continue

        if fail_on_error:
            raise ValueError("live uploads database is unreadable")
        self._index_cache = {}
        return {}

    def get_upload_info(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """Return the uploads.json metadata row for an upload ID, if present."""
        if not self.validate_upload_id(upload_id):
            return None
        for info in self._load_upload_index().values():
            if isinstance(info, dict) and info.get("id") == upload_id:
                return dict(info)
        return None

    def reserve_upload(
        self,
        upload_id: str,
        *,
        owner: Optional[str],
        auth_manager: Any = None,
        allow_admin: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Owner-check and reserve an indexed upload against cleanup.

        The live index lookup, ownership/path validation, and access touch all
        occur under the cleanup lock. A durable-reference writer must not
        commit when this returns ``None``.
        """
        if not self.validate_upload_id(upload_id):
            return None

        auth_configured = bool(auth_manager and getattr(auth_manager, "is_configured", False))
        if auth_configured and not owner:
            return None

        uploads_db_path = os.path.join(self.upload_dir, "uploads.json")
        with self._index_lock:
            try:
                current = dict(self._load_upload_index(fail_on_error=True))
            except Exception:
                logger.warning("Cannot reserve upload %s without a valid live index", upload_id)
                return None
            matching_keys = [
                key
                for key, info in current.items()
                if isinstance(info, dict) and info.get("id") == upload_id
            ]
            if not matching_keys:
                return None

            matching_rows = [dict(current[key]) for key in matching_keys]
            row_owners = {
                str(row.get("owner")) if row.get("owner") is not None else None
                for row in matching_rows
            }
            row_hashes = {
                str(row.get("hash") or row.get("checksum_sha256"))
                for row in matching_rows
                if row.get("hash") or row.get("checksum_sha256")
            }
            if len(row_owners) != 1 or len(row_hashes) > 1:
                logger.warning(
                    "Cannot reserve ambiguous upload index rows for %s",
                    upload_id,
                )
                return None

            is_admin = False
            if allow_admin and owner and auth_manager and hasattr(auth_manager, "is_admin"):
                try:
                    is_admin = bool(auth_manager.is_admin(owner))
                except Exception:
                    is_admin = False

            now = datetime.now()
            current_info = matching_rows[0]
            if owner and not is_admin and current_info.get("owner") != owner:
                return None
            if not owner and current_info.get("owner") is not None:
                return None

            existing_paths: set[str] = set()
            for row in matching_rows:
                stored_path = row.get("path")
                if not stored_path:
                    continue
                if not self._inside_upload_dir(stored_path):
                    logger.warning(
                        "Cannot reserve upload %s with an out-of-root index path",
                        upload_id,
                    )
                    return None
                if os.path.isfile(stored_path):
                    if os.path.basename(stored_path) != upload_id:
                        return None
                    existing_paths.add(os.path.normcase(os.path.realpath(stored_path)))
            if len(existing_paths) > 1:
                logger.warning("Cannot reserve upload %s with multiple indexed paths", upload_id)
                return None
            path = next(iter(existing_paths), None) or self._find_upload_path(upload_id)
            if not path or not os.path.isfile(path) or not self._inside_upload_dir(path):
                return None

            last_accessed = self._parse_upload_timestamp(current_info.get("last_accessed"))
            path_changed = current_info.get("path") != path
            needs_write = (
                path_changed
                or last_accessed is None
                or last_accessed < now - timedelta(minutes=5)
            )
            if needs_write:
                accessed_at = now.isoformat()
                updated_index = dict(current)
                for key in matching_keys:
                    updated = dict(updated_index[key])
                    updated["path"] = path
                    updated["last_accessed"] = accessed_at
                    updated_index[key] = updated
                try:
                    self._atomic_write_json(
                        uploads_db_path,
                        updated_index,
                        sync_backup=True,
                    )
                except Exception:
                    try:
                        self._atomic_write_json(
                            uploads_db_path,
                            current,
                            sync_backup=True,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to restore upload indexes after reservation failed for %s",
                            upload_id,
                        )
                    logger.exception("Failed to reserve upload %s against cleanup", upload_id)
                    return None
                current_info = dict(updated_index[matching_keys[0]])

            resolved = dict(current_info)
            resolved.setdefault("id", upload_id)
            resolved["path"] = path
            resolved.setdefault("name", os.path.basename(path))
            resolved.setdefault("original_name", resolved["name"])
            resolved.setdefault("mime", mimetypes.guess_type(path)[0] or "application/octet-stream")
            if resolved.get("hash") and not resolved.get("checksum_sha256"):
                resolved["checksum_sha256"] = resolved["hash"]
            if resolved.get("uploaded_at") and not resolved.get("created_at"):
                resolved["created_at"] = resolved["uploaded_at"]
            return resolved

    def _renamed_upload_index_key(self, key: str, info: Dict[str, Any], old_owner: str, new_owner: str) -> str:
        """Return the storage key to use after renaming an owned upload row.

        Harden against usernames with colons by using the explicit metadata
        fields instead of trying to parse the key string.
        """
        file_hash = info.get("hash")
        if file_hash:
            return f"{new_owner}:{file_hash}"

        # Fallback for rows without an explicit hash (should not happen in modern Odysseus)
        if isinstance(key, str) and ":" in key:
            # Join all but the last part if there are multiple colons
            parts = key.rsplit(":", 1)
            if len(parts) == 2:
                owner_part, rest = parts[0], parts[1]
                if owner_part.strip().lower() == old_owner.strip().lower():
                    return f"{new_owner}:{rest}"
        return key

    def _unique_upload_index_key(self, base_key: str, used_keys: set, reserved_keys: set, info: Dict[str, Any]) -> str:
        """Choose a deterministic collision key without overwriting an existing row."""
        if base_key not in used_keys and base_key not in reserved_keys:
            return base_key

        upload_id = str(info.get("id") or "renamed").strip() or "renamed"
        candidate = f"{base_key}:{upload_id}"
        if candidate not in used_keys and candidate not in reserved_keys:
            return candidate

        index = 2
        while True:
            candidate = f"{base_key}:{upload_id}:{index}"
            if candidate not in used_keys and candidate not in reserved_keys:
                return candidate
            index += 1

    def rename_owner(self, old_owner: str, new_owner: str) -> int:
        """Rename upload metadata ownership from old_owner to new_owner.

        Upload rows are keyed by owner-qualified hashes for dedupe and also
        carry an `owner` field for access checks. Both must move together when
        usernames change.
        """
        old_owner_normalized = str(old_owner or "").strip().lower()
        new_owner = str(new_owner or "").strip()
        if not old_owner_normalized or not new_owner:
            return 0
        if old_owner_normalized == new_owner.lower():
            return 0

        uploads_db_path = os.path.join(self.upload_dir, "uploads.json")
        with self._index_lock:
            current = self._load_upload_index()
            if not current:
                return 0

            updated = {}
            renamed = 0
            original_keys = set(current.keys())

            for key, info in current.items():
                new_key = key
                new_info = info
                if isinstance(info, dict) and str(info.get("owner", "")).strip().lower() == old_owner_normalized:
                    new_info = dict(info)
                    new_info["owner"] = new_owner
                    base_key = self._renamed_upload_index_key(key, new_info, old_owner_normalized, new_owner)
                    new_key = self._unique_upload_index_key(
                        base_key,
                        set(updated.keys()),
                        original_keys - {key},
                        new_info,
                    )
                    if new_key != base_key:
                        logger.warning(
                            "Upload owner rename key collision for %s -> %s at %s; preserving row as %s",
                            old_owner_normalized,
                            new_owner,
                            base_key,
                            new_key,
                        )
                    renamed += 1
                updated[new_key] = new_info

            if renamed:
                self._atomic_write_json(uploads_db_path, updated)
            return renamed

    def _find_upload_path(self, upload_id: str) -> Optional[str]:
        """Find an upload file by ID while staying inside upload_dir."""
        if not self.validate_upload_id(upload_id):
            return None

        candidates: list[str] = []
        direct = os.path.join(self.upload_dir, upload_id)
        if os.path.isfile(direct) and self._inside_upload_dir(direct):
            candidates.append(os.path.realpath(direct))

        for root, dirs, files in os.walk(self.upload_dir, followlinks=False):
            is_junction = getattr(os.path, "isjunction", lambda _path: False)
            dirs[:] = [
                directory
                for directory in dirs
                if not os.path.islink(os.path.join(root, directory))
                and not is_junction(os.path.join(root, directory))
            ]
            if upload_id in files:
                path = os.path.join(root, upload_id)
                if os.path.isfile(path) and self._inside_upload_dir(path):
                    real_path = os.path.realpath(path)
                    if real_path not in candidates:
                        candidates.append(real_path)
                        if len(candidates) > 1:
                            logger.warning(
                                "Upload ID %s resolves to multiple physical files",
                                upload_id,
                            )
                            return None
        return candidates[0] if candidates else None

    def resolve_upload(
        self,
        upload_id: str,
        owner: Optional[str] = None,
        auth_manager: Any = None,
        allow_admin: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Resolve and reserve an upload only if the caller may read it.

        This is the owner-aware lookup used by internal processors. Public
        download routes already perform owner checks; chat/document paths must
        do the same before reading file bytes server-side. Reservation shares
        cleanup's lifecycle lock and prevents a newly persisted reference from
        racing final deletion.
        """
        return self.reserve_upload(
            upload_id,
            owner=owner,
            auth_manager=auth_manager,
            allow_admin=allow_admin,
        )
    
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
            
            files = self._load_upload_index()
            if files:
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
                detail=f"File size exceeds {format_byte_limit(self.max_upload_size)} limit"
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
                    existing_file.setdefault("checksum_sha256", file_hash)
                    if existing_file.get("uploaded_at"):
                        existing_file.setdefault("created_at", existing_file["uploaded_at"])
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
                    "checksum_sha256": existing_file.get("checksum_sha256") or file_hash,
                    "uploaded_at": existing_file["uploaded_at"],
                    "created_at": existing_file.get("created_at") or existing_file["uploaded_at"],
                    "owner": existing_file.get("owner"),
                    "width": existing_file.get("width"),
                    "height": existing_file.get("height"),
                    "is_duplicate": True
                }
        
        # Generate unique ID and determine save location
        file_id = _build_upload_id(safe_filename)
        
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
        created_at = datetime.now().isoformat()
        file_metadata = {
            "id": file_id,
            "path": file_path,
            "mime": content_type,
            "size": file_size,
            "name": safe_filename,
            "hash": file_hash,
            "checksum_sha256": file_hash,
            "original_name": original_filename,
            "uploaded_at": created_at,
            "created_at": created_at,
            "last_accessed": created_at,
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
