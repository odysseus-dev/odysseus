"""File Browser API — browse, read, edit, upload, download server files."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.auth_helpers import get_current_user
from src.tool_security import owner_is_admin_or_single_user
from src.tool_execution import _is_sensitive_path

logger = logging.getLogger(__name__)

MAX_READ_BYTES = 500 * 1024       # 500 KB
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_SEARCH_RESULTS = 200

# Paths/entries that should never be visible through the file browser, even to
# admins.  Mirrors the spirit of _SENSITIVE_BASENAMES from tool_execution but
# broadened for directory-level hiding (e.g. __pycache__).
_DENY_BASENAMES: frozenset[str] = frozenset({
    ".env", ".env.*",
    ".ssh", ".gnupg", ".gitconfig",
    ".bashrc", ".bash_profile", ".bash_logout",
    ".zshrc", ".zprofile", ".zshenv",
    ".profile", ".tcshrc", ".cshrc",
    ".netrc", ".git",
    "__pycache__", ".DS_Store", "Thumbs.db",
})


def _is_denied(name: str) -> bool:
    """Return True if a basename should be hidden from the file browser."""
    lower = name.casefold()
    for pat in _DENY_BASENAMES:
        if pat.endswith("*"):
            if lower.startswith(pat[:-1].casefold()):
                return True
        elif lower == pat.casefold():
            return True
    return False


def _require_admin(request: Request):
    """Reject non-admin callers.  Copied from shell_routes pattern."""
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if not auth_manager:
        return
    user = getattr(request.state, "current_user", None)
    if not user or user == "api":
        raise HTTPException(403, "Admin only")
    if not auth_manager.is_admin(user):
        raise HTTPException(403, "Admin only")


def _get_user_root(request: Request) -> str:
    """Return the filesystem root the caller may browse.

    Admins get / (full container filesystem).  Non-admins are confined to their
    personal workspace directory.
    """
    owner = get_current_user(request)
    if owner_is_admin_or_single_user(owner):
        return "/"
    safe = owner.replace("/", "_").replace("\\", "_") if owner else "default"
    return os.path.realpath(os.path.join("data", "workspaces", safe))


def _get_write_root(request: Request) -> str:
    """Return the filesystem root the caller may write to.

    Admins get /app (project + data).  Non-admins get their workspace.
    """
    owner = get_current_user(request)
    if owner_is_admin_or_single_user(owner):
        return "/app"
    safe = owner.replace("/", "_").replace("\\", "_") if owner else "default"
    return os.path.realpath(os.path.join("data", "workspaces", safe))


def _resolve_path(request: Request, raw_path: str) -> str:
    """Resolve, validate, and confine *raw_path* under the caller's root.

    Checks:
      1. Symlinks are resolved via realpath.
      2. commonpath ensures containment within the root.
      3. Sensitive-file deny-list blocks .ssh, .env, id_rsa, etc.
    """
    root = _get_user_root(request)
    if not raw_path or not raw_path.strip():
        return root

    expanded = os.path.expanduser(raw_path.strip())
    if not os.path.isabs(expanded):
        expanded = os.path.join(root, expanded)
    resolved = os.path.realpath(expanded)

    # Containment — must stay under root.
    try:
        if os.path.commonpath([resolved, root]) != root:
            raise HTTPException(403, "Path is outside the allowed root")
    except ValueError:
        raise HTTPException(403, "Path is outside the allowed root")

    # Sensitive-file gate (reuses tool_execution logic).
    if _is_sensitive_path(resolved):
        raise HTTPException(403, "Access to sensitive path denied")

    return resolved


def _resolve_write_path(request: Request, raw_path: str) -> str:
    """Resolve and confine *raw_path* under the caller's write root.

    Same as _resolve_path but uses _get_write_root (which is /app for admin,
    not /).  This prevents writes outside the project directory.
    """
    root = _get_write_root(request)
    if not raw_path or not raw_path.strip():
        raise HTTPException(400, "Path is required for write operation")

    expanded = os.path.expanduser(raw_path.strip())
    if not os.path.isabs(expanded):
        expanded = os.path.join(root, expanded)
    resolved = os.path.realpath(expanded)

    try:
        if os.path.commonpath([resolved, root]) != root:
            raise HTTPException(403, "Write access is restricted to the project directory")
    except ValueError:
        raise HTTPException(403, "Write access is restricted to the project directory")

    if _is_sensitive_path(resolved):
        raise HTTPException(403, "Cannot write to sensitive path")

    return resolved


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class WriteBody(BaseModel):
    path: str
    content: str


class MkdirBody(BaseModel):
    path: str


class DeleteBody(BaseModel):
    path: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def setup_filebrowser_routes() -> APIRouter:
    router = APIRouter(prefix="/api/files", tags=["files"])

    # ── GET /browse ──────────────────────────────────────────────────────────
    @router.get("/browse")
    def browse(request: Request, path: str = Query(default="")):
        """List files and directories under *path*.  Admin gets the full
        filesystem (with deny-list filtering); non-admin is confined to their
        workspace folder."""
        _require_admin(request)
        target = _resolve_path(request, path)

        if not os.path.isdir(target):
            raise HTTPException(404, "Directory not found")

        items: list[dict] = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    if _is_denied(entry.name):
                        continue
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        kind = "dir" if entry.is_dir(follow_symlinks=False) else "file"
                        items.append({
                            "name": entry.name,
                            "type": kind,
                            "size": stat.st_size if kind == "file" else None,
                            "mtime": stat.st_mtime,
                        })
                    except OSError:
                        continue
        except (PermissionError, OSError):
            items = []

        # Dirs first, then alphabetical.
        items.sort(key=lambda x: (0 if x["type"] == "dir" else 1, x["name"].lower()))

        parent = os.path.dirname(target)
        return {
            "path": target,
            "parent": parent if parent and parent != target else None,
            "items": items,
        }

    # ── GET /read ────────────────────────────────────────────────────────────
    @router.get("/read")
    def read_file(request: Request, path: str = Query(...)):
        """Return the text content of a file.  Rejects binary files (null
        byte detection) and files over 500 KB."""
        _require_admin(request)
        resolved = _resolve_path(request, path)

        if not os.path.isfile(resolved):
            raise HTTPException(404, "File not found")

        size = os.path.getsize(resolved)
        if size > MAX_READ_BYTES:
            raise HTTPException(413, f"File too large ({size} bytes; limit {MAX_READ_BYTES})")

        try:
            raw = Path(resolved).read_bytes()
        except (OSError, PermissionError) as exc:
            raise HTTPException(403, str(exc))

        if b"\x00" in raw:
            raise HTTPException(415, "Binary file — cannot display as text")

        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("latin-1")

        mime, _ = mimetypes.guess_type(resolved)
        return {"path": resolved, "content": content, "mime": mime, "size": size}

    # ── PUT /write ───────────────────────────────────────────────────────────
    @router.put("/write")
    def write_file(request: Request, body: WriteBody):
        """Create or overwrite a file."""
        _require_admin(request)
        resolved = _resolve_write_path(request, body.path)

        try:
            resolved_obj = Path(resolved)
            resolved_obj.parent.mkdir(parents=True, exist_ok=True)
            resolved_obj.write_text(body.content, encoding="utf-8")
        except (OSError, PermissionError) as exc:
            raise HTTPException(403, str(exc))

        return {"ok": True, "path": resolved, "size": len(body.content.encode("utf-8"))}

    # ── POST /mkdir ──────────────────────────────────────────────────────────
    @router.post("/mkdir")
    def mkdir(request: Request, body: MkdirBody):
        """Create a directory (including parents)."""
        _require_admin(request)
        resolved = _resolve_write_path(request, body.path)

        try:
            os.makedirs(resolved, exist_ok=True)
        except (OSError, PermissionError) as exc:
            raise HTTPException(403, str(exc))

        return {"ok": True, "path": resolved}

    # ── DELETE /delete ───────────────────────────────────────────────────────
    @router.delete("/delete")
    def delete(request: Request, body: DeleteBody):
        """Delete a file or an *empty* directory."""
        _require_admin(request)
        resolved = _resolve_write_path(request, body.path)

        if not os.path.exists(resolved):
            raise HTTPException(404, "Path not found")

        try:
            if os.path.isdir(resolved):
                # Only allow removing empty directories.
                if os.listdir(resolved):
                    raise HTTPException(400, "Directory is not empty")
                os.rmdir(resolved)
            else:
                os.remove(resolved)
        except (OSError, PermissionError) as exc:
            raise HTTPException(403, str(exc))

        return {"ok": True, "path": resolved}

    # ── GET /download ────────────────────────────────────────────────────────
    @router.get("/download")
    async def download(request: Request, path: str = Query(...)):
        """Download a file."""
        _require_admin(request)
        resolved = _resolve_path(request, path)

        if not os.path.isfile(resolved):
            raise HTTPException(404, "File not found")

        mime, _ = mimetypes.guess_type(resolved)
        filename = os.path.basename(resolved)
        return FileResponse(
            resolved,
            media_type=mime or "application/octet-stream",
            filename=filename,
        )

    # ── POST /upload ─────────────────────────────────────────────────────────
    @router.post("/upload")
    async def upload(
        request: Request,
        path: str = Form(...),
        file: UploadFile = File(...),
    ):
        """Upload a file into the directory at *path*."""
        _require_admin(request)
        # Validate the target directory.
        resolved_dir = _resolve_write_path(request, path)

        # Read the upload in chunks to enforce the size limit.
        total = 0
        chunks: list[bytes] = []
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD_BYTES} byte limit")
            chunks.append(chunk)

        # Re-validate after joining the filename —防止 path traversal via
        # the uploaded filename itself.
        dest = os.path.join(resolved_dir, file.filename or "upload")
        dest = os.path.realpath(dest)
        root = _get_write_root(request)
        try:
            if os.path.commonpath([dest, root]) != root:
                raise HTTPException(403, "Upload destination outside allowed root")
        except ValueError:
            raise HTTPException(403, "Upload destination outside allowed root")

        if _is_sensitive_path(dest):
            raise HTTPException(403, "Cannot upload to sensitive path")

        try:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in chunks:
                    f.write(chunk)
        except (OSError, PermissionError) as exc:
            raise HTTPException(403, str(exc))

        return {"ok": True, "path": dest, "size": total}

    # ── GET /search ──────────────────────────────────────────────────────────
    @router.get("/search")
    def search(request: Request, path: str = Query(default=""), q: str = Query(...)):
        """Search for files whose name contains *q* (case-insensitive).
        Returns at most 200 results."""
        _require_admin(request)
        root = _resolve_path(request, path)

        if not os.path.isdir(root):
            raise HTTPException(404, "Directory not found")

        query = q.lower()
        results: list[dict] = []

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune denied directories in-place so os.walk skips them.
            dirnames[:] = [d for d in dirnames if not _is_denied(d)]

            for name in filenames:
                if _is_denied(name):
                    continue
                if query in name.lower():
                    full = os.path.join(dirpath, name)
                    try:
                        st = os.stat(full)
                        results.append({
                            "name": name,
                            "path": full,
                            "type": "file",
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                        })
                    except OSError:
                        continue
                    if len(results) >= _MAX_SEARCH_RESULTS:
                        return {"results": results, "truncated": True}

        return {"results": results, "truncated": False}

    # ── GET /stat ────────────────────────────────────────────────────────────
    @router.get("/stat")
    def stat(request: Request, path: str = Query(...)):
        """Return metadata for a single file or directory."""
        _require_admin(request)
        resolved = _resolve_path(request, path)

        if not os.path.exists(resolved):
            raise HTTPException(404, "Path not found")

        try:
            st = os.stat(resolved)
        except OSError as exc:
            raise HTTPException(403, str(exc))

        kind = "dir" if os.path.isdir(resolved) else "file"
        return {
            "path": resolved,
            "name": os.path.basename(resolved),
            "type": kind,
            "size": st.st_size if kind == "file" else None,
            "mtime": st.st_mtime,
        }

    return router
