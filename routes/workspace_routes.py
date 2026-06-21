import os
import shutil
from typing import Any

from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel

from src.auth_helpers import get_current_user
from src.tool_security import owner_is_admin_or_single_user

# Cap entries returned per directory (mirrors filesystem_tools._CODENAV_MAX_HITS).
# A huge directory shouldn't dump thousands of rows into the picker; the user can
# type/paste a path to jump straight in instead.
_MAX_BROWSE_DIRS = 500
_DEFAULT_FILE_ENTRIES = 250
_MAX_FILE_ENTRIES = 500
_MAX_TEXT_FILE_BYTES = 1024 * 1024
_TEXT_EXTENSIONS = {
    ".bat", ".c", ".cfg", ".conf", ".cpp", ".cs", ".css", ".csv", ".env.example",
    ".go", ".h", ".hpp", ".htm", ".html", ".ini", ".java", ".js", ".json",
    ".jsx", ".kt", ".kts", ".log", ".lua", ".md", ".mjs", ".ps1", ".py",
    ".rb", ".rs", ".sh", ".sql", ".svelte", ".toml", ".ts", ".tsx", ".txt",
    ".vue", ".xml", ".yaml", ".yml",
}


class WorkspaceFileWrite(BaseModel):
    workspace: str
    path: str
    content: str = ""
    previous_mtime: float | None = None
    create_parents: bool = False


class WorkspacePathBody(BaseModel):
    workspace: str
    path: str


class WorkspaceRenameBody(BaseModel):
    workspace: str
    path: str
    new_path: str


def _existing_dir(path: str) -> str | None:
    if not path:
        return None
    try:
        resolved = os.path.realpath(os.path.expanduser(path))
    except OSError:
        return None
    return resolved if os.path.isdir(resolved) else None


def _suggested_workspace_roots() -> list[dict[str, str]]:
    """Return useful, existing directories for the workspace picker.

    These are convenience shortcuts only. The regular vetting layer still
    decides whether any selected directory can be bound as a workspace.
    """
    home = _existing_dir("~")
    candidates: list[tuple[str, str, str | None]] = []
    if home:
        candidates.extend([
            ("documents", "Documents", os.path.join(home, "Documents")),
            ("desktop", "Desktop", os.path.join(home, "Desktop")),
            ("downloads", "Downloads", os.path.join(home, "Downloads")),
            ("home", "Home", home),
        ])

    if os.name == "nt":
        for env_name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            one_drive = os.environ.get(env_name)
            if one_drive:
                candidates.append((f"{env_name.lower()}-documents", "OneDrive Documents", os.path.join(one_drive, "Documents")))

    seen: set[str] = set()
    roots: list[dict[str, str]] = []
    for key, label, raw_path in candidates:
        resolved = _existing_dir(raw_path or "")
        if not resolved:
            continue
        norm = os.path.normcase(resolved)
        if norm in seen:
            continue
        seen.add(norm)
        roots.append({"key": key, "label": label, "path": resolved})
    return roots


def _default_workspace_browse_root() -> str:
    roots = _suggested_workspace_roots()
    for root in roots:
        if root["key"].endswith("documents"):
            return root["path"]
    if roots:
        return roots[0]["path"]
    return os.path.realpath(os.path.expanduser("~"))


def _require_workspace_admin(request: Request, action: str) -> None:
    owner = get_current_user(request)
    if not owner_is_admin_or_single_user(owner):
        raise HTTPException(status_code=403, detail=f"Workspace {action} is admin-only")


def _workspace_root_or_400(raw_workspace: str) -> str:
    from src.tool_execution import vet_workspace

    root = vet_workspace(raw_workspace)
    if not root:
        raise HTTPException(status_code=400, detail="Workspace is not usable")
    return root


def _display_relpath(root: str, path: str) -> str:
    if os.path.normcase(os.path.realpath(root)) == os.path.normcase(os.path.realpath(path)):
        return ""
    rel = os.path.relpath(path, root)
    return "" if rel == "." else rel.replace(os.sep, "/")


def _resolve_workspace_path(root: str, raw_path: str, *, allow_root: bool = False) -> str:
    raw = (raw_path or "").strip()
    if not raw:
        if allow_root:
            return root
        raise HTTPException(status_code=400, detail="Path is required")
    from src.tool_execution import _resolve_tool_path_in_workspace

    try:
        return _resolve_tool_path_in_workspace(root, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _looks_textual_name(path: str) -> bool:
    name = os.path.basename(path).lower()
    if name in {"dockerfile", "makefile", "license", "readme", "requirements"}:
        return True
    suffixes = []
    stem = name
    while True:
        stem, ext = os.path.splitext(stem)
        if not ext:
            break
        suffixes.append(ext)
    return any(ext in _TEXT_EXTENSIONS for ext in suffixes) or os.path.splitext(name)[1] in _TEXT_EXTENSIONS


def _is_probably_binary(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    control = sum(1 for b in sample if b < 32 and b not in (9, 10, 12, 13))
    return control / max(1, len(sample)) > 0.08


def _editable_text_file(path: str, size: int | None = None) -> bool:
    try:
        stat_size = os.path.getsize(path) if size is None else size
        if stat_size > _MAX_TEXT_FILE_BYTES:
            return False
        with open(path, "rb") as handle:
            sample = handle.read(4096)
    except OSError:
        return False
    if _is_probably_binary(sample):
        return False
    if _looks_textual_name(path):
        return True
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _entry_info(root: str, target: str) -> dict[str, Any] | None:
    try:
        from src.tool_execution import _resolve_tool_path_in_workspace

        resolved = _resolve_tool_path_in_workspace(root, target)
        stat = os.stat(resolved)
    except (OSError, ValueError):
        return None

    is_dir = os.path.isdir(resolved)
    is_file = os.path.isfile(resolved)
    size = 0 if is_dir else int(stat.st_size)
    return {
        "name": os.path.basename(resolved),
        "path": _display_relpath(root, resolved),
        "type": "directory" if is_dir else "file" if is_file else "other",
        "size": size,
        "modified": float(stat.st_mtime),
        # Keep directory listing metadata-only. Opening/sampling every file in
        # large synced folders can make the app look frozen; /files/read still
        # performs the real text/binary validation before returning content.
        "editable": bool(is_file and size <= _MAX_TEXT_FILE_BYTES),
        "text_hint": bool(is_file and _looks_textual_name(resolved)),
    }


def _list_workspace_dir(root: str, raw_path: str, max_entries: int = _DEFAULT_FILE_ENTRIES) -> dict[str, Any]:
    target = _resolve_workspace_path(root, raw_path, allow_root=True)
    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail="Path is not a folder")

    entry_limit = max(1, min(int(max_entries or _DEFAULT_FILE_ENTRIES), _MAX_FILE_ENTRIES))
    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(target) as iterator:
            for entry in iterator:
                if len(entries) >= entry_limit:
                    truncated = True
                    break
                child = os.path.join(target, entry.name)
                info = _entry_info(root, child)
                if info:
                    entries.append(info)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    entries.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
    parent = ""
    if os.path.normcase(target) != os.path.normcase(root):
        parent_path = os.path.dirname(target)
        parent = _display_relpath(root, parent_path)
    return {
        "workspace": root,
        "path": _display_relpath(root, target),
        "parent": parent,
        "entries": entries,
        "truncated": truncated,
        "max_entries": entry_limit,
    }


def setup_workspace_routes():
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    @router.get("/roots")
    def roots(request: Request):
        """Return useful server-side directories for the workspace picker."""
        owner = get_current_user(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(status_code=403, detail="Workspace shortcuts are admin-only")
        from src.tool_execution import vet_workspace
        roots = []
        for root in _suggested_workspace_roots():
            roots.append({
                **root,
                "selectable": vet_workspace(root["path"]) is not None,
            })
        return {
            "default_path": _default_workspace_browse_root(),
            "roots": roots,
        }

    @router.get("/browse")
    def browse(request: Request, path: str = Query(default="")):
        """List subdirectories of `path` (default: home) so the UI can navigate
        the server filesystem and pick a workspace folder. Directories only.

        ADMIN-ONLY: this enumerates the server filesystem, so it is gated the
        same way the file/shell tools are (read_file/write_file/bash are in
        NON_ADMIN_BLOCKED_TOOLS). A non-admin who can't use those tools must not
        be able to map the host's directory tree either.
        """
        _require_workspace_admin(request, "browsing")

        # Resolve symlinks so the reported path is canonical and the UI navigates
        # real directories (defends against symlink games in displayed paths).
        target = os.path.realpath(os.path.expanduser(path.strip() or _default_workspace_browse_root()))
        if not os.path.isdir(target):
            target = _default_workspace_browse_root()

        dirs = []
        truncated = False
        try:
            with os.scandir(target) as it:
                for entry in it:
                    try:
                        # Don't follow symlinks when classifying - a symlinked
                        # dir is skipped rather than letting the browser wander
                        # off via a link. Hidden entries are omitted.
                        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                            # Build the child path server-side with os.path.join
                            # so it's correct on Windows (backslashes) and Linux.
                            dirs.append({"name": entry.name, "path": os.path.join(target, entry.name)})
                            if len(dirs) > _MAX_BROWSE_DIRS:
                                truncated = True
                                break
                    except OSError:
                        continue
        except (PermissionError, OSError):
            dirs = []

        dirs_sorted = sorted(dirs, key=lambda d: d["name"].lower())
        parent = os.path.dirname(target)
        from src.tool_execution import vet_workspace
        return {
            "path": target,
            "parent": parent if parent and parent != target else None,
            "dirs": dirs_sorted[:_MAX_BROWSE_DIRS],
            "truncated": truncated,
            # Whether this directory may be bound as a workspace (filesystem
            # roots and sensitive dirs may be browsed through but not chosen).
            "selectable": vet_workspace(target) is not None,
        }

    @router.get("/vet")
    def vet(request: Request, path: str = Query(default="")):
        """Validate a workspace path without binding it.

        The UI calls this before persisting a manually typed path (/workspace
        set) so a typo, file path, deleted folder, sensitive dir, or filesystem
        root is rejected up front with the canonical path returned on success,
        instead of being stored client-side and silently dropped at chat time.
        Admin-gated like /browse: it confirms path existence on the host.
        """
        _require_workspace_admin(request, "selection")
        from src.tool_execution import vet_workspace
        resolved = vet_workspace(path)
        return {"ok": resolved is not None, "path": resolved}

    @router.get("/files/list")
    def list_files(
        request: Request,
        workspace: str = Query(default=""),
        path: str = Query(default=""),
        limit: int = Query(default=_DEFAULT_FILE_ENTRIES, ge=1, le=_MAX_FILE_ENTRIES),
    ):
        """List files and folders inside the selected workspace."""
        _require_workspace_admin(request, "file browsing")
        root = _workspace_root_or_400(workspace)
        return _list_workspace_dir(root, path, limit)

    @router.get("/files/read")
    def read_file(
        request: Request,
        workspace: str = Query(default=""),
        path: str = Query(default=""),
    ):
        """Read a text file inside the selected workspace."""
        _require_workspace_admin(request, "file reading")
        root = _workspace_root_or_400(workspace)
        target = _resolve_workspace_path(root, path)
        if not os.path.isfile(target):
            raise HTTPException(status_code=400, detail="Path is not a file")
        try:
            stat = os.stat(target)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if stat.st_size > _MAX_TEXT_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"File is larger than {_MAX_TEXT_FILE_BYTES} bytes")
        if not _editable_text_file(target, int(stat.st_size)):
            raise HTTPException(status_code=415, detail="File is not an editable text file")
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read(_MAX_TEXT_FILE_BYTES + 1)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Permission denied") from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "workspace": root,
            "path": _display_relpath(root, target),
            "name": os.path.basename(target),
            "content": content[:_MAX_TEXT_FILE_BYTES],
            "size": int(stat.st_size),
            "modified": float(stat.st_mtime),
            "truncated": len(content) > _MAX_TEXT_FILE_BYTES,
        }

    @router.post("/files/write")
    def write_file(request: Request, body: WorkspaceFileWrite):
        """Create or overwrite a UTF-8 text file inside the workspace."""
        _require_workspace_admin(request, "file writing")
        root = _workspace_root_or_400(body.workspace)
        target = _resolve_workspace_path(root, body.path)
        if os.path.isdir(target):
            raise HTTPException(status_code=400, detail="Path is a folder")
        encoded = (body.content or "").encode("utf-8")
        if len(encoded) > _MAX_TEXT_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"Content is larger than {_MAX_TEXT_FILE_BYTES} bytes")
        parent = os.path.dirname(target)
        if not os.path.isdir(parent):
            if body.create_parents:
                try:
                    os.makedirs(parent, exist_ok=True)
                except OSError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            else:
                raise HTTPException(status_code=400, detail="Parent folder does not exist")
        if body.previous_mtime is not None and os.path.exists(target):
            try:
                current_mtime = float(os.path.getmtime(target))
            except OSError:
                current_mtime = None
            if current_mtime is not None and abs(current_mtime - float(body.previous_mtime)) > 0.0001:
                raise HTTPException(status_code=409, detail="File changed on disk; reload before saving")
        try:
            with open(target, "w", encoding="utf-8", newline="") as handle:
                handle.write(body.content or "")
            stat = os.stat(target)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Permission denied") from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "workspace": root,
            "path": _display_relpath(root, target),
            "name": os.path.basename(target),
            "size": int(stat.st_size),
            "modified": float(stat.st_mtime),
        }

    @router.post("/files/mkdir")
    def mkdir(request: Request, body: WorkspacePathBody):
        """Create a folder inside the workspace."""
        _require_workspace_admin(request, "folder creation")
        root = _workspace_root_or_400(body.workspace)
        target = _resolve_workspace_path(root, body.path)
        if os.path.exists(target) and not os.path.isdir(target):
            raise HTTPException(status_code=400, detail="A file already exists at that path")
        try:
            os.makedirs(target, exist_ok=True)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Permission denied") from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "workspace": root, "path": _display_relpath(root, target)}

    @router.post("/files/rename")
    def rename(request: Request, body: WorkspaceRenameBody):
        """Rename or move a file/folder within the workspace."""
        _require_workspace_admin(request, "file renaming")
        root = _workspace_root_or_400(body.workspace)
        source = _resolve_workspace_path(root, body.path)
        target = _resolve_workspace_path(root, body.new_path)
        if os.path.normcase(source) == os.path.normcase(root):
            raise HTTPException(status_code=400, detail="Cannot rename the workspace root")
        if os.path.exists(target):
            raise HTTPException(status_code=409, detail="Target already exists")
        if not os.path.exists(source):
            raise HTTPException(status_code=404, detail="Path not found")
        parent = os.path.dirname(target)
        if not os.path.isdir(parent):
            raise HTTPException(status_code=400, detail="Target parent folder does not exist")
        try:
            os.replace(source, target)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Permission denied") from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "workspace": root, "path": _display_relpath(root, target)}

    @router.delete("/files/delete")
    def delete_path(
        request: Request,
        workspace: str = Query(default=""),
        path: str = Query(default=""),
        recursive: bool = Query(default=False),
    ):
        """Delete a file or folder inside the workspace."""
        _require_workspace_admin(request, "file deletion")
        root = _workspace_root_or_400(workspace)
        target = _resolve_workspace_path(root, path)
        if os.path.normcase(target) == os.path.normcase(root):
            raise HTTPException(status_code=400, detail="Cannot delete the workspace root")
        if not os.path.exists(target):
            raise HTTPException(status_code=404, detail="Path not found")
        try:
            if os.path.isdir(target):
                if recursive:
                    shutil.rmtree(target)
                else:
                    os.rmdir(target)
            else:
                os.remove(target)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Permission denied") from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "workspace": root, "path": _display_relpath(root, target)}

    return router
