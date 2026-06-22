# routes/personal_routes.py
"""Routes for personal documents management."""
import os
import logging
import shutil
import uuid
from typing import Any, Dict, List, Tuple
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Depends
from src.request_models import DirectoryRequest
from core.constants import BASE_DIR, PERSONAL_DIR, PERSONAL_UPLOADS_DIR
from src.rag_singleton import get_rag_manager
from src.auth_helpers import require_privilege, require_user
from core.middleware import require_admin
from src.upload_handler import secure_filename
from src.upload_limits import PERSONAL_UPLOAD_MAX_BYTES

UPLOADS_DIR = PERSONAL_UPLOADS_DIR

logger = logging.getLogger(__name__)


def _personal_upload_dir_for_owner(owner: str | None, *, create: bool = True) -> str:
    """Return the per-owner upload directory used for direct RAG uploads."""
    owner_segment = secure_filename((owner or "local").strip())[:80] or "local"
    upload_dir = os.path.abspath(os.path.join(UPLOADS_DIR, owner_segment))
    base_abs = os.path.abspath(UPLOADS_DIR)
    if os.path.commonpath([upload_dir, base_abs]) != base_abs:
        raise ValueError("Unsafe upload owner path")
    if create:
        os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


def _unique_personal_upload_path(upload_dir: str, original_name: str | None) -> Tuple[str, str, str]:
    """Build a collision-resistant upload path while preserving a display name."""
    safe_name = secure_filename(os.path.basename(original_name or "upload"))
    if not safe_name or safe_name.startswith("."):
        safe_name = "upload"

    stem, ext = os.path.splitext(safe_name)
    stem = (stem or "upload")[:80]
    filename = f"{stem}-{uuid.uuid4().hex[:10]}{ext.lower()}"
    file_path = os.path.abspath(os.path.join(upload_dir, filename))
    upload_abs = os.path.abspath(upload_dir)
    if os.path.commonpath([file_path, upload_abs]) != upload_abs:
        raise ValueError("Unsafe upload filename")
    return file_path, filename, safe_name


def _unique_existing_target(path: str) -> str:
    """Return a non-existing sibling path for rename collision handling."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    while True:
        candidate = f"{stem}-{uuid.uuid4().hex[:10]}{ext}"
        if not os.path.exists(candidate):
            return candidate


def _remove_empty_tree(path: str) -> None:
    """Best-effort removal of empty directories under ``path``."""
    if not os.path.isdir(path):
        return
    for root, dirs, _files in os.walk(path, topdown=False):
        for dirname in dirs:
            candidate = os.path.join(root, dirname)
            try:
                os.rmdir(candidate)
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def rename_personal_upload_owner(
    old_owner: str,
    new_owner: str,
    *,
    personal_docs_manager: Any = None,
    rag_manager: Any = None,
) -> Dict[str, Any]:
    """Move direct personal uploads and rewrite RAG owner metadata on user rename."""
    old_dir = _personal_upload_dir_for_owner(old_owner, create=False)
    new_dir = _personal_upload_dir_for_owner(new_owner, create=False)
    path_map: Dict[str, str] = {}
    moved_files = 0

    if os.path.isdir(old_dir) and old_dir != new_dir:
        os.makedirs(new_dir, exist_ok=True)
        for root, _dirs, files in os.walk(old_dir):
            rel_root = os.path.relpath(root, old_dir)
            target_root = new_dir if rel_root == "." else os.path.join(new_dir, rel_root)
            os.makedirs(target_root, exist_ok=True)
            for filename in files:
                source = os.path.abspath(os.path.join(root, filename))
                target = _unique_existing_target(os.path.abspath(os.path.join(target_root, filename)))
                shutil.move(source, target)
                path_map[source] = target
                moved_files += 1
        _remove_empty_tree(old_dir)

    if personal_docs_manager is not None:
        rename_directory = getattr(personal_docs_manager, "rename_directory", None)
        if callable(rename_directory):
            rename_directory(old_dir, new_dir, path_map=path_map)

    rag_result = None
    if rag_manager is not None:
        rename_owner = getattr(rag_manager, "rename_owner", None)
        if callable(rename_owner):
            rag_result = rename_owner(
                old_owner,
                new_owner,
                path_map=path_map,
                path_prefixes=[(old_dir, new_dir)],
            )

    return {
        "old_dir": old_dir,
        "new_dir": new_dir,
        "moved_files": moved_files,
        "path_map": path_map,
        "rag_result": rag_result,
    }


def setup_personal_routes(personal_docs_manager, rag_manager, rag_available):
    """
    Setup personal documents related routes.

    Args:
        personal_docs_manager: PersonalDocsManager instance
        rag_manager: RAG manager instance (may be None)
        rag_available: Boolean indicating if RAG is available

    Returns:
        APIRouter instance with personal docs routes
    """
    router = APIRouter(prefix="/api/personal")

    def _rag():
        """Get the current RAG manager, retrying init if needed."""
        return get_rag_manager()

    def _resolve_allowed_personal_dir(directory: str) -> str:
        """Resolve a user-supplied personal-docs path under the allowed root."""
        if not directory:
            raise HTTPException(400, "Directory path is required")

        # realpath (not abspath) so a symlink inside PERSONAL_DIR that points
        # outside it is resolved before the commonpath confinement check below;
        # abspath only normalises `..` and would let such a symlink escape.
        base_abs = os.path.realpath(PERSONAL_DIR)
        candidate = directory if os.path.isabs(directory) else os.path.join(base_abs, directory)
        resolved = os.path.realpath(candidate)
        try:
            in_base = os.path.commonpath([resolved, base_abs]) == base_abs
        except ValueError:
            in_base = False
        if not in_base:
            raise HTTPException(403, "Directory must be inside personal documents")
        return resolved
    
    @router.get("")
    def api_personal_list(owner: str = Depends(require_user), _admin: None = Depends(require_admin)):
        """Enhanced version that includes directories"""
        files = [{"name": f["name"], "size": f["size"], "path": f.get("path", "")} for f in personal_docs_manager.index]
        directories = personal_docs_manager.get_indexed_directories() if hasattr(personal_docs_manager, "get_indexed_directories") else []
        return {"files": files, "directories": directories}
    
    @router.post("/reload")
    def api_personal_reload(owner: str = Depends(require_user), _admin: None = Depends(require_admin)):
        personal_docs_manager.refresh_index()
        return {"ok": True, "count": len(personal_docs_manager.index)}
    
    @router.post("/add_directory")
    async def add_directory_to_rag(
        request: Request,
        directory_request: DirectoryRequest,
        owner: str = Depends(require_user), _admin: None = Depends(require_admin),
    ):
        """
        Add a directory and all its subdirectories/files to the RAG index.
        
        Args:
            directory_request: Directory request model containing the directory path
            
        Returns:
            JSON response with indexing results
        """
        directory = directory_request.directory
        try:
            directory = _resolve_allowed_personal_dir(directory)
            
            # Security check - ensure directory exists and is accessible
            if not os.path.exists(directory):
                raise HTTPException(404, f"Directory not found: {directory}")
            
            if not os.path.isdir(directory):
                raise HTTPException(400, f"Path is not a directory: {directory}")
            
            logger.info(f"Adding directory to RAG: {directory}")
            
            # Use the RAGManager to index the directory
            rag = _rag()
            if rag:
                result = rag.index_personal_documents(directory, owner=owner)
                
                if result["success"]:
                    # Also update the personal_docs_manager to track this directory
                    personal_docs_manager.add_directory(directory, index=False)
                    
                    return {
                        "success": True,
                        "message": f"Successfully indexed {result['indexed_count']} chunks from {directory}",
                        "indexed_count": result["indexed_count"],
                        "failed_count": result.get("failed_count", 0),
                        "directory": directory
                    }
                else:
                    raise HTTPException(500, result.get("message", "Failed to index directory"))
            else:
                raise HTTPException(503, "RAG system is not available")
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error adding directory to RAG: {e}")
            raise HTTPException(500, f"Failed to add directory: {str(e)}")
    
    @router.delete("/remove_directory")
    async def remove_directory_from_rag(directory: str = Query(...), owner: str = Depends(require_user), _admin: None = Depends(require_admin)):
        """
        Remove a directory from the RAG index.

        Args:
            directory: Path to the directory to remove

        Returns:
            JSON response confirming removal
        """
        try:
            # Confine to PERSONAL_DIR — parity with add_directory_to_rag (which
            # resolves the path the same way). Without this, an arbitrary or
            # `..`-escaping path is passed straight to
            # personal_docs_manager.remove_directory / rag.remove_directory.
            directory = _resolve_allowed_personal_dir(directory)

            logger.info(f"Removing directory from RAG: {directory}")

            # Always remove from personal_docs_manager tracking
            if hasattr(personal_docs_manager, 'remove_directory'):
                personal_docs_manager.remove_directory(directory)

            # Remove from RAG vector store (best-effort)
            rag = _rag()
            if rag:
                try:
                    rag.remove_directory(directory)
                except Exception as e:
                    logger.warning(f"RAG removal failed for directory {directory}: {e}")

            return {
                "success": True,
                "message": f"Successfully removed {directory} from RAG index",
                "directory": directory
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error removing directory from RAG: {e}")
            raise HTTPException(500, f"Failed to remove directory: {str(e)}")
    
    @router.post("/upload")
    async def upload_files_to_rag(request: Request, files: List[UploadFile] = File(...)):
        """Upload files directly into RAG. Supports text and PDF."""
        user = require_privilege(request, "can_use_documents")
        rag = _rag()
        if not rag:
            raise HTTPException(503, "RAG system is not available — is the embedding service running?")

        upload_dir = _personal_upload_dir_for_owner(user)

        total_indexed = 0
        total_failed = 0
        uploaded_files = []

        for upload in files:
            try:
                file_path, stored_name, safe_name = _unique_personal_upload_path(upload_dir, upload.filename)
                content_bytes = await upload.read(PERSONAL_UPLOAD_MAX_BYTES + 1)
                if len(content_bytes) > PERSONAL_UPLOAD_MAX_BYTES:
                    logger.warning(f"Rejected oversized personal upload: {upload.filename!r}")
                    total_failed += 1
                    continue
                with open(file_path, "wb") as f:
                    f.write(content_bytes)

                ext = os.path.splitext(safe_name)[1].lower()
                if ext == ".pdf":
                    from src.personal_docs import extract_pdf_text
                    text = extract_pdf_text(file_path)
                else:
                    text = content_bytes.decode("utf-8", errors="replace")

                if not text or not text.strip():
                    total_failed += 1
                    continue

                # Chunk and index
                chunks = rag._split_into_chunks(text, chunk_size=500)
                for i, chunk in enumerate(chunks):
                    metadata = {
                        "source": file_path,
                        "filename": safe_name,
                        "stored_filename": stored_name,
                        "directory": upload_dir,
                        "type": ext,
                        "chunk_id": i,
                    }
                    if user:
                        metadata["owner"] = user
                    if rag.add_document(chunk, metadata):
                        total_indexed += 1
                    else:
                        total_failed += 1

                uploaded_files.append(safe_name)
            except Exception as e:
                logger.error(f"Failed to upload/index {upload.filename}: {e}")
                total_failed += 1

        # Track uploads directory
        if uploaded_files and hasattr(personal_docs_manager, "add_directory"):
            personal_docs_manager.add_directory(upload_dir, index=False)

        return {
            "success": True,
            "uploaded": uploaded_files,
            "indexed_count": total_indexed,
            "failed_count": total_failed,
        }

    @router.delete("/file")
    async def delete_file_from_rag(filepath: str = Query(...), owner: str = Depends(require_user), _admin: None = Depends(require_admin)):
        """Delete a specific file from RAG index and optionally from disk."""
        try:
            # Remove chunks from RAG vector store (best-effort)
            removed = 0
            rag = _rag()
            if rag:
                try:
                    removed = rag.delete_by_source(filepath)
                except Exception as e:
                    logger.warning(f"RAG removal failed for {filepath}: {e}")

            # Delete file from disk if it's in the caller's own uploads dir.
            # Scope to the per-owner subdir, not the shared uploads root, so one
            # admin can't delete another user's personal files by path.
            deleted_from_disk = False
            try:
                abs_target = os.path.realpath(filepath)
                base_abs = os.path.realpath(_personal_upload_dir_for_owner(owner, create=False))
                in_uploads = (
                    abs_target == base_abs
                    or os.path.commonpath([abs_target, base_abs]) == base_abs
                )
            except ValueError:
                # commonpath raises on mixed drives / non-comparable paths
                in_uploads = False
            if in_uploads and abs_target != base_abs:
                try:
                    os.remove(abs_target)
                    deleted_from_disk = True
                except FileNotFoundError:
                    pass  # already gone — race with another request or cleanup

            # Exclude the file from the listing (persists across restarts)
            personal_docs_manager.exclude_file(filepath)

            return {
                "success": True,
                "removed_chunks": removed,
                "deleted_from_disk": deleted_from_disk,
            }
        except Exception as e:
            logger.error(f"Failed to delete file {filepath}: {e}")
            raise HTTPException(500, f"Failed to delete file: {str(e)}")

    return router
