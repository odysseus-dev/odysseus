# routes/personal_routes.py
"""Routes for personal documents management."""

import os
import logging
from typing import List
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Depends
from src.request_models import DirectoryRequest
from core.constants import BASE_DIR, PERSONAL_DIR
from src.rag_singleton import get_rag_manager
from src.auth_helpers import get_current_user, require_user
from core.middleware import require_admin
from src.upload_handler import secure_filename

UPLOADS_DIR = os.path.join(BASE_DIR, "data", "personal_uploads")

logger = logging.getLogger(__name__)


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

        base_abs = os.path.abspath(PERSONAL_DIR)
        candidate = (
            directory if os.path.isabs(directory) else os.path.join(base_abs, directory)
        )
        resolved = os.path.abspath(candidate)
        try:
            in_base = os.path.commonpath([resolved, base_abs]) == base_abs
        except ValueError:
            in_base = False
        if not in_base:
            raise HTTPException(403, "Directory must be inside personal documents")
        return resolved

    @router.get("")
    def api_personal_list(
        owner: str = Depends(require_user), _admin: None = Depends(require_admin)
    ):
        """Enhanced version that includes directories"""
        files = [
            {"name": f["name"], "size": f["size"], "path": f.get("path", "")}
            for f in personal_docs_manager.index
        ]
        directories = (
            personal_docs_manager.get_indexed_directories()
            if hasattr(personal_docs_manager, "get_indexed_directories")
            else []
        )
        return {"files": files, "directories": directories}

    @router.post("/reload")
    def api_personal_reload(
        owner: str = Depends(require_user), _admin: None = Depends(require_admin)
    ):
        personal_docs_manager.refresh_index()
        return {"ok": True, "count": len(personal_docs_manager.index)}

    @router.post("/add_directory")
    async def add_directory_to_rag(
        request: Request,
        directory_request: DirectoryRequest,
        owner: str = Depends(require_user),
        _admin: None = Depends(require_admin),
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
                        "directory": directory,
                    }
                else:
                    raise HTTPException(
                        500, result.get("message", "Failed to index directory")
                    )
            else:
                raise HTTPException(503, "RAG system is not available")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error adding directory to RAG: {e}")
            raise HTTPException(500, f"Failed to add directory: {str(e)}")

    @router.delete("/remove_directory")
    async def remove_directory_from_rag(
        directory: str = Query(...),
        owner: str = Depends(require_user),
        _admin: None = Depends(require_admin),
    ):
        """
        Remove a directory from the RAG index.

        Args:
            directory: Path to the directory to remove

        Returns:
            JSON response confirming removal
        """
        try:
            if not directory:
                raise HTTPException(400, "Directory path is required")

            logger.info(f"Removing directory from RAG: {directory}")

            # Always remove from personal_docs_manager tracking
            if hasattr(personal_docs_manager, "remove_directory"):
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
                "directory": directory,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error removing directory from RAG: {e}")
            raise HTTPException(500, f"Failed to remove directory: {str(e)}")

    @router.post("/upload")
    async def upload_files_to_rag(
        request: Request, files: List[UploadFile] = File(...)
    ):
        """Upload files directly into RAG. Supports text and PDF."""
        user = get_current_user(request)
        rag = _rag()
        if not rag:
            raise HTTPException(
                503, "RAG system is not available — is the embedding service running?"
            )

        os.makedirs(UPLOADS_DIR, exist_ok=True)

        total_indexed = 0
        total_failed = 0
        uploaded_files = []

        for upload in files:
            try:
                # Sanitize filename — strip directory components and unsafe chars
                safe_name = secure_filename(
                    os.path.basename(upload.filename or "upload")
                )
                if not safe_name or safe_name.startswith("."):
                    safe_name = f"upload_{total_indexed + total_failed}"
                file_path = os.path.join(UPLOADS_DIR, safe_name)
                # Defense-in-depth: ensure resolved path stays under UPLOADS_DIR
                base_abs = os.path.abspath(UPLOADS_DIR)
                if (
                    os.path.commonpath([os.path.abspath(file_path), base_abs])
                    != base_abs
                ):
                    logger.warning(f"Rejected unsafe upload path: {upload.filename!r}")
                    total_failed += 1
                    continue
                content_bytes = await upload.read()
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
                        "directory": UPLOADS_DIR,
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
            personal_docs_manager.add_directory(UPLOADS_DIR, index=False)

        return {
            "success": True,
            "uploaded": uploaded_files,
            "indexed_count": total_indexed,
            "failed_count": total_failed,
        }

    @router.delete("/file")
    async def delete_file_from_rag(
        filepath: str = Query(...),
        owner: str = Depends(require_user),
        _admin: None = Depends(require_admin),
    ):
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

            # Delete file from disk if it's in uploads dir
            deleted_from_disk = False
            try:
                abs_target = os.path.abspath(filepath)
                base_abs = os.path.abspath(UPLOADS_DIR)
                in_uploads = (
                    abs_target == base_abs
                    or os.path.commonpath([abs_target, base_abs]) == base_abs
                )
            except ValueError:
                # commonpath raises on mixed drives / non-comparable paths
                in_uploads = False
            if in_uploads and abs_target != base_abs and os.path.exists(abs_target):
                os.remove(abs_target)
                deleted_from_disk = True

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
