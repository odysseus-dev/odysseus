"""Workspace API - browse, create, and persist an agent workspace folder."""
import os
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.auth_helpers import get_current_user
from src.constants import AGENT_WORKSPACE_DIR
from src.tool_security import owner_is_admin_or_single_user

# Cap entries returned per directory (mirrors filesystem_tools._CODENAV_MAX_HITS).
# A huge directory shouldn't dump thousands of rows into the picker; the user can
# type/paste a path to jump straight in instead.
_MAX_BROWSE_DIRS = 500
_WORKSPACE_PREF_KEY = "agent_workspace"


class WorkspaceSelection(BaseModel):
    path: str
    create: bool = False


class WorkspaceFolderCreate(BaseModel):
    parent: str
    name: str


def ensure_default_workspace() -> str:
    """Create and return the canonical managed workspace root."""
    try:
        Path(AGENT_WORKSPACE_DIR).mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError("Could not create the default workspace.") from exc
    return os.path.realpath(AGENT_WORKSPACE_DIR)


def _require_workspace_admin(request: Request):
    owner = get_current_user(request)
    if not owner_is_admin_or_single_user(owner):
        raise HTTPException(status_code=403, detail="Workspace selection is admin-only")
    return owner


def _reject_cross_site(request: Request):
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-site request rejected")


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((os.path.normcase(path), os.path.normcase(root))) == os.path.normcase(root)
    except (TypeError, ValueError):
        return False


def _creation_target(raw_path: str) -> tuple[str, bool]:
    """Resolve a missing path and report whether it may be created safely."""
    try:
        expanded = os.path.expanduser((raw_path or "").strip())
    except (OSError, ValueError):
        return "", False
    if not expanded or not os.path.isabs(expanded):
        return "", False

    managed_root = ensure_default_workspace()
    candidate = os.path.abspath(expanded)
    if not _is_within(candidate, managed_root):
        return candidate, False

    # Resolve the deepest existing ancestor so a symlink below the managed
    # root cannot redirect creation outside it.
    ancestor = candidate
    # The admin-supplied candidate has already passed lexical containment under
    # the server-owned managed root; these probes locate its existing ancestor.
    while not os.path.lexists(ancestor):
        parent = os.path.dirname(ancestor)
        if parent == ancestor:
            return candidate, False
        ancestor = parent
    if not os.path.isdir(ancestor):
        return candidate, False
    projected = os.path.realpath(
        os.path.join(os.path.realpath(ancestor), os.path.relpath(candidate, ancestor))
    )
    from src.tool_execution import workspace_path_is_sensitive

    return projected, (
        _is_within(projected, managed_root)
        and not workspace_path_is_sensitive(projected)
    )


def _create_managed_path(target: str) -> str:
    """Create ``target`` below the managed root without following symlinks.

    The POSIX path walks from an already-open root directory and opens every
    child with ``O_NOFOLLOW``. This closes the validation-to-creation race in
    which a writable ancestor could otherwise be replaced with a symlink.
    Platforms without equivalent no-follow directory primitives fail closed;
    a post-creation containment check cannot undo an external side effect.
    """
    managed_root = ensure_default_workspace()
    candidate = os.path.abspath(target)
    if not _is_within(candidate, managed_root):
        raise ValueError("Folder is outside the default workspace.")
    relative = os.path.relpath(candidate, managed_root)
    if relative == os.curdir:
        return managed_root
    parts = Path(relative).parts
    if any(part in {"", os.curdir, os.pardir} for part in parts):
        raise ValueError("Folder path is invalid.")

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if os.name == "posix" and nofollow and directory:
        flags = os.O_RDONLY | directory | nofollow
        fd = os.open(managed_root, flags)
        try:
            for part in parts:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
        finally:
            os.close(fd)
    else:
        raise RuntimeError(
            "Safe managed-folder creation requires no-follow directory support."
        )

    resolved = os.path.realpath(candidate)
    if not _is_within(resolved, managed_root) or not os.path.isdir(resolved):
        raise ValueError("Folder is outside the default workspace.")
    return resolved


def _workspace_validation(path: str) -> tuple[str | None, str]:
    from src.tool_execution import validate_workspace

    resolved, reason = validate_workspace(path)
    if not resolved:
        return None, reason

    # Use process-sandbox policy too, so the picker does not claim a folder is
    # usable and then fail when the first shell command starts.
    try:
        from src.execution_sandbox import validate_sandbox_workspace_path

        resolved, reason = validate_sandbox_workspace_path(resolved)
    except (ImportError, OSError, RuntimeError):
        return None, "Workspace sandbox policy is unavailable."
    return resolved, reason


def _browse_payload(target: str) -> dict:
    resolved, reason = _workspace_validation(target)
    try:
        canonical = os.path.realpath(os.path.expanduser(target))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=reason or "Folder path is invalid.",
        ) from exc
    # Browsing arbitrary server-visible folders is an intentional admin-only
    # capability. Canonicalization and workspace policy run before these sinks.
    if not os.path.exists(canonical):
        raise HTTPException(status_code=404, detail="Folder does not exist.")
    if not os.path.isdir(canonical):
        raise HTTPException(status_code=400, detail="Path is not a folder.")

    dirs = []
    try:
        with os.scandir(canonical) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                        dirs.append({"name": entry.name, "path": os.path.join(canonical, entry.name)})
                except OSError:
                    continue
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Folder cannot be read.") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail="Could not open folder.") from exc

    dirs_sorted = sorted(dirs, key=lambda d: d["name"].lower())
    parent = os.path.dirname(canonical)
    managed_root = ensure_default_workspace()
    return {
        "path": canonical,
        "parent": parent if parent and parent != canonical else None,
        "dirs": dirs_sorted[:_MAX_BROWSE_DIRS],
        "truncated": len(dirs_sorted) > _MAX_BROWSE_DIRS,
        "selectable": resolved is not None,
        "selectable_reason": reason,
        "can_create_folder": _is_within(canonical, managed_root),
        "default_path": managed_root,
    }


def _load_selected_workspace(owner) -> tuple[str, str, bool]:
    from routes.prefs_routes import _load_for_user

    prefs = _load_for_user(owner) or {}
    configured = _WORKSPACE_PREF_KEY in prefs
    raw = str(prefs.get(_WORKSPACE_PREF_KEY) or "").strip()
    if not raw:
        return "", "", configured
    resolved, reason = _workspace_validation(raw)
    return (resolved or ""), reason, configured


def _save_selected_workspace(owner, path: str):
    from routes.prefs_routes import _load_for_user, _save_for_user

    prefs = _load_for_user(owner) or {}
    # Preserve an explicit empty value as a tombstone. Without it, another
    # browser's legacy localStorage cache could be mistaken for an unmigrated
    # selection and resurrect a workspace the user intentionally cleared.
    prefs[_WORKSPACE_PREF_KEY] = path
    _save_for_user(owner, prefs)


def setup_workspace_routes():
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    @router.get("/browse")
    def browse(request: Request, path: str = Query(default="")):
        """List subdirectories of ``path`` so the UI can navigate folders.

        An empty path starts at the managed default workspace. Directories only.

        ADMIN-ONLY: this enumerates the server filesystem, so it is gated the
        same way the file/shell tools are (read_file/write_file/bash are in
        NON_ADMIN_BLOCKED_TOOLS). A non-admin who can't use those tools must not
        be able to map the host's directory tree either.
        """
        _require_workspace_admin(request)
        try:
            default_path = ensure_default_workspace()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Default workspace is unavailable.") from exc
        return _browse_payload(path.strip() or default_path)

    @router.get("/selection")
    def get_selection(request: Request):
        owner = _require_workspace_admin(request)
        try:
            default_path = ensure_default_workspace()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Default workspace is unavailable.") from exc
        path, warning, configured = _load_selected_workspace(owner)
        return {
            "path": path,
            "default_path": default_path,
            "warning": warning,
            "migration_allowed": not configured,
        }

    @router.post("/selection")
    def select_workspace(request: Request, body: WorkspaceSelection):
        owner = _require_workspace_admin(request)
        _reject_cross_site(request)
        raw = (body.path or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="Enter a folder path.")

        resolved, reason = _workspace_validation(raw)
        if not resolved and reason == "Folder does not exist.":
            target, can_create = _creation_target(raw)
            if body.create and can_create:
                try:
                    target = _create_managed_path(target)
                except (OSError, RuntimeError, ValueError) as exc:
                    raise HTTPException(status_code=400, detail="Could not create folder.") from exc
                resolved, reason = _workspace_validation(target)
            elif not body.create:
                return JSONResponse(
                    status_code=404,
                    content={
                        "ok": False,
                        "error": "Folder does not exist.",
                        "code": "folder_missing",
                        "path": target or os.path.abspath(os.path.expanduser(raw)),
                        "can_create": can_create,
                    },
                )
            else:
                reason = "Folders can only be created inside the default workspace."

        if not resolved:
            raise HTTPException(
                status_code=400,
                detail=reason or "Folder cannot be used as a workspace.",
            )
        browse_payload = _browse_payload(resolved)
        _save_selected_workspace(owner, resolved)
        return {"ok": True, "path": resolved, "browse": browse_payload}

    @router.delete("/selection")
    def clear_selection(request: Request):
        owner = _require_workspace_admin(request)
        _reject_cross_site(request)
        # Prepare every fallible part of the response before writing the clear
        # tombstone. Otherwise a default-workspace failure can report an error
        # after the server has cleared the preference while the browser still
        # holds the old path and can submit it on the next action.
        default_path = ensure_default_workspace()
        _save_selected_workspace(owner, "")
        return {"ok": True, "path": "", "default_path": default_path}

    @router.post("/folders")
    def create_folder(request: Request, body: WorkspaceFolderCreate):
        _require_workspace_admin(request)
        _reject_cross_site(request)
        name = (body.name or "").strip()
        if (
            not name
            or name in {".", ".."}
            or name.startswith(".")
            or "/" in name
            or "\\" in name
            or "\x00" in name
        ):
            raise HTTPException(status_code=400, detail="Enter a simple visible folder name.")

        parent, reason = _workspace_validation(body.parent)
        managed_root = ensure_default_workspace()
        if not parent or not _is_within(parent, managed_root):
            raise HTTPException(
                status_code=400,
                detail=(
                    reason
                    or "New folders can only be created inside the default workspace."
                ),
            )
        target = os.path.join(parent, name)
        # ``parent`` passed canonical managed-root policy and ``name`` excludes
        # separators, dot components, hidden names, and NUL bytes.
        if os.path.exists(target):
            if not os.path.isdir(target):
                raise HTTPException(status_code=409, detail="A file already uses that name.")
        try:
            target = _create_managed_path(target)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Could not create folder.") from exc
        return {"ok": True, "path": target, "browse": _browse_payload(target)}

    @router.get("/vet")
    def vet(request: Request, path: str = Query(default="")):
        """Validate a workspace path without binding it.

        The UI calls this before persisting a manually typed path (/workspace
        set) so a typo, file path, deleted folder, sensitive dir, or filesystem
        root is rejected up front with the canonical path returned on success,
        instead of being stored client-side and silently dropped at chat time.
        Admin-gated like /browse: it confirms path existence on the host.
        """
        _require_workspace_admin(request)
        resolved, reason = _workspace_validation(path)
        return {"ok": resolved is not None, "path": resolved, "error": reason}

    return router
