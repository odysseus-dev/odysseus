"""
atlas_routes.py

Atlas — an Obsidian-style markdown vault. Notes are plain ``.md`` files on disk
under ``ATLAS_DIR/<owner>/`` (the filesystem is the source of truth), so a vault
is portable: external sync tools and Obsidian itself can read/write the same
folder. This module exposes CRUD over those files plus the link-graph,
backlinks, search, attachment upload, and zip / Obsidian-folder import/export.

Owner scoping + path confinement are the security-critical surface here. Every
path a caller supplies is funneled through :func:`safe_atlas_path`, which keeps
it inside the requesting user's own vault directory (no ``..`` traversal, no
absolute paths, no symlink escape). The same helpers are imported by the
``manage_atlas`` agent tool so the agent can never reach outside the vault
either.
"""

import io
import os
import re
import logging
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Request, HTTPException, File, UploadFile
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel

from core.atomic_io import atomic_write_text
from src.auth_helpers import require_user
from src.constants import ATLAS_DIR
from src.atlas_links import (
    parse_links,
    resolve_link,
    build_graph,
    backlinks as _backlinks,
    note_title,
)

logger = logging.getLogger(__name__)

ATLAS_ROOT = Path(ATLAS_DIR)

# Markdown note extensions and the attachment types we accept on import/upload.
NOTE_EXTS = {".md", ".markdown"}
ATTACH_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico",
    ".pdf", ".txt", ".csv", ".json", ".canvas",
}
# Per-note size cap (generous for prose, blocks accidental huge uploads).
MAX_NOTE_BYTES = 5 * 1024 * 1024
ATTACH_DIRNAME = "_attachments"
# Directories never scanned for notes (Obsidian config, attachments, VCS, hidden).
SKIP_DIRS = {ATTACH_DIRNAME, ".obsidian", ".git", ".trash"}


class AtlasPathError(ValueError):
    """Raised when a caller-supplied path escapes the owner's vault root."""


# ---------------------------------------------------------------------------
# Filesystem helpers (shared with src/agent_tools/atlas_tools.py)
# ---------------------------------------------------------------------------

def owner_slug(owner: Optional[str]) -> str:
    """Filesystem-safe directory name for an owner.

    Single-user / anonymous mode resolves to ``"default"``. Matches the
    sanitising used elsewhere (e.g. note reminder caches) so the same user maps
    to the same folder everywhere.
    """
    raw = (owner or "default").strip() or "default"
    return "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in raw)


def owner_root(owner: Optional[str]) -> Path:
    """Absolute path to a user's vault root, created on first use."""
    root = ATLAS_ROOT / owner_slug(owner)
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_atlas_path(owner: Optional[str], relpath: str) -> Path:
    """Resolve ``relpath`` inside the owner's vault, rejecting any escape.

    ``os.path.realpath`` collapses ``..`` and resolves symlinks; we then assert
    the result is the root itself or strictly below it. This is the single
    confinement gate for every read/write the API and the agent tool perform.
    """
    if relpath is None:
        raise AtlasPathError("missing path")
    rel = str(relpath).strip().replace("\\", "/").lstrip("/")
    if not rel:
        raise AtlasPathError("empty path")
    # NUL bytes and Windows drive specs are never valid vault-relative paths.
    if "\x00" in rel or re.match(r"^[A-Za-z]:", rel):
        raise AtlasPathError("invalid path")

    root = owner_root(owner)
    root_real = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root_real, rel))
    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        raise AtlasPathError("path escapes vault root")
    return Path(candidate)


def _rel_str(owner: Optional[str], abs_path: Path) -> str:
    """Vault-relative, forward-slash string for an absolute path."""
    root = owner_root(owner)
    return str(Path(abs_path).relative_to(root)).replace(os.sep, "/")


def _walk_note_paths(root: Path) -> List[Path]:
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip/hidden dirs in place so os.walk doesn't descend into them.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in NOTE_EXTS:
                out.append(Path(dirpath) / fn)
    return out


# Small mtime-keyed cache: re-reading every note's bytes on each graph/search
# call is wasteful, but the directory listing (paths + mtimes) is cheap. When
# the signature is unchanged we reuse the parsed content.
_notes_cache: Dict[str, Tuple[frozenset, Dict[str, str]]] = {}


def read_all_notes(owner: Optional[str]) -> Dict[str, str]:
    """Map of ``relpath -> markdown`` for every note in the owner's vault."""
    root = owner_root(owner)
    paths = _walk_note_paths(root)
    sig_items = []
    for p in paths:
        try:
            sig_items.append((str(p.relative_to(root)), p.stat().st_mtime_ns))
        except OSError:
            continue
    sig = frozenset(sig_items)
    cached = _notes_cache.get(owner_slug(owner))
    if cached and cached[0] == sig:
        return cached[1]
    notes: Dict[str, str] = {}
    for p in paths:
        rel = str(p.relative_to(root)).replace(os.sep, "/")
        try:
            notes[rel] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            notes[rel] = ""
    _notes_cache[owner_slug(owner)] = (sig, notes)
    return notes


def invalidate_cache(owner: Optional[str]) -> None:
    _notes_cache.pop(owner_slug(owner), None)


def _ensure_note_ext(rel: str) -> str:
    if os.path.splitext(rel)[1].lower() not in NOTE_EXTS:
        rel = rel + ".md"
    return rel


def write_note(owner: Optional[str], relpath: str, content: str) -> str:
    """Create/overwrite a note. Returns its normalised relpath."""
    rel = _ensure_note_ext(str(relpath).strip().replace("\\", "/").lstrip("/"))
    if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
        raise AtlasPathError("note exceeds size limit")
    abs_path = safe_atlas_path(owner, rel)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(str(abs_path), content)
    invalidate_cache(owner)
    return _rel_str(owner, abs_path)


def _note_meta(owner: Optional[str], rel: str, md: str) -> dict:
    abs_path = owner_root(owner) / rel
    try:
        st = abs_path.stat()
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        mtime, size = 0, len(md.encode("utf-8"))
    parsed = parse_links(md)
    return {
        "path": rel,
        "title": note_title(rel, md),
        "tags": parsed["tags"],
        "size": size,
        "mtime": mtime,
    }


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class NoteWrite(BaseModel):
    path: str
    content: str = ""


class PathBody(BaseModel):
    path: str


class RenameBody(BaseModel):
    path: str
    new_path: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def setup_atlas_routes() -> APIRouter:
    router = APIRouter(prefix="/api/atlas", tags=["atlas"])

    @router.get("/notes")
    async def list_notes(request: Request):
        owner = require_user(request)
        notes = read_all_notes(owner)
        items = [_note_meta(owner, rel, md) for rel, md in notes.items()]
        items.sort(key=lambda n: n["mtime"], reverse=True)
        return {"notes": items}

    @router.get("/note")
    async def get_note(request: Request, path: str):
        owner = require_user(request)
        try:
            abs_path = safe_atlas_path(owner, path)
        except AtlasPathError as e:
            raise HTTPException(400, str(e))
        all_notes = read_all_notes(owner)
        if not abs_path.is_file():
            # Resolve a bare wikilink-style name ('B', 'sub/Note') to its file,
            # the same way an internal [[link]] resolves.
            resolved = resolve_link(path, list(all_notes.keys()))
            if not resolved:
                raise HTTPException(404, "Note not found")
            abs_path = safe_atlas_path(owner, resolved)
        md = abs_path.read_text(encoding="utf-8", errors="replace")
        rel = _rel_str(owner, abs_path)
        all_relpaths = list(all_notes.keys())
        parsed = parse_links(md)
        outlinks = []
        for name in parsed["wikilinks"] + parsed["embeds"]:
            outlinks.append({"target": name, "resolved": resolve_link(name, all_relpaths)})
        return {
            "path": rel,
            "title": note_title(rel, md),
            "content": md,
            "tags": parsed["tags"],
            "outlinks": outlinks,
            "backlinks": _backlinks(rel, all_notes),
        }

    @router.put("/note")
    async def put_note(req: NoteWrite, request: Request):
        owner = require_user(request)
        try:
            rel = write_note(owner, req.path, req.content)
        except AtlasPathError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "path": rel}

    @router.post("/rename")
    async def rename_note(req: RenameBody, request: Request):
        owner = require_user(request)
        try:
            src = safe_atlas_path(owner, req.path)
            dst = safe_atlas_path(owner, _ensure_note_ext(req.new_path))
        except AtlasPathError as e:
            raise HTTPException(400, str(e))
        if not src.exists():
            raise HTTPException(404, "Note not found")
        if dst.exists():
            raise HTTPException(409, "Target already exists")
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        invalidate_cache(owner)
        return {"ok": True, "path": _rel_str(owner, dst)}

    @router.post("/delete")
    async def delete_note(req: PathBody, request: Request):
        owner = require_user(request)
        try:
            abs_path = safe_atlas_path(owner, req.path)
        except AtlasPathError as e:
            raise HTTPException(400, str(e))
        if not abs_path.exists():
            raise HTTPException(404, "Note not found")
        if abs_path.is_dir():
            raise HTTPException(400, "Use a per-note path, not a folder")
        abs_path.unlink()
        invalidate_cache(owner)
        return {"ok": True}

    @router.post("/folder")
    async def make_folder(req: PathBody, request: Request):
        owner = require_user(request)
        try:
            abs_path = safe_atlas_path(owner, req.path)
        except AtlasPathError as e:
            raise HTTPException(400, str(e))
        abs_path.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": _rel_str(owner, abs_path)}

    @router.get("/graph")
    async def graph(request: Request):
        owner = require_user(request)
        return build_graph(read_all_notes(owner))

    @router.get("/backlinks")
    async def get_backlinks(request: Request, path: str):
        owner = require_user(request)
        try:
            abs_path = safe_atlas_path(owner, path)
        except AtlasPathError as e:
            raise HTTPException(400, str(e))
        rel = _rel_str(owner, abs_path)
        return {"path": rel, "backlinks": _backlinks(rel, read_all_notes(owner))}

    @router.get("/search")
    async def search(request: Request, q: str = ""):
        owner = require_user(request)
        q = (q or "").strip()
        notes = read_all_notes(owner)
        results = []
        if not q:
            return {"results": results}
        tag_query = q[1:].lower() if q.startswith("#") else None
        ql = q.lower()
        for rel, md in notes.items():
            parsed = parse_links(md)
            if tag_query is not None:
                if any(t.lower() == tag_query or t.lower().startswith(tag_query + "/")
                       for t in parsed["tags"]):
                    results.append(_note_meta(owner, rel, md))
                continue
            hay = (rel + "\n" + md).lower()
            if ql in hay:
                idx = md.lower().find(ql)
                snippet = ""
                if idx >= 0:
                    start = max(0, idx - 40)
                    snippet = md[start:idx + len(q) + 40].replace("\n", " ")
                meta = _note_meta(owner, rel, md)
                meta["snippet"] = snippet
                results.append(meta)
        return {"results": results}

    @router.get("/raw")
    async def raw_file(request: Request, path: str):
        """Serve a note/attachment's bytes (for ![[embeds]] in the preview)."""
        owner = require_user(request)
        try:
            abs_path = safe_atlas_path(owner, path)
        except AtlasPathError as e:
            raise HTTPException(400, str(e))
        if not abs_path.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(str(abs_path))

    @router.post("/attachment")
    async def upload_attachment(request: Request, file: UploadFile = File(...)):
        owner = require_user(request)
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in ATTACH_EXTS:
            raise HTTPException(400, f"Attachment type '{ext}' not allowed")
        safe_name = re.sub(r"[^\w\-. ]+", "_", os.path.basename(file.filename or "file"))
        rel = f"{ATTACH_DIRNAME}/{safe_name}"
        abs_path = safe_atlas_path(owner, rel)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        # Avoid clobbering an existing attachment with the same name.
        stem, suffix = os.path.splitext(abs_path.name)
        i = 1
        while abs_path.exists():
            abs_path = abs_path.with_name(f"{stem}-{i}{suffix}")
            i += 1
        data = await file.read()
        if len(data) > MAX_NOTE_BYTES * 4:
            raise HTTPException(400, "Attachment too large")
        abs_path.write_bytes(data)
        return {"ok": True, "path": _rel_str(owner, abs_path)}

    @router.get("/export")
    async def export_vault(request: Request):
        owner = require_user(request)
        root = owner_root(owner)
        buf = io.BytesIO()
        count = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in {".git", ".trash"}]
                for fn in filenames:
                    src = Path(dirpath) / fn
                    arc = str(src.relative_to(root))
                    zf.write(src, arcname=arc)
                    count += 1
        if count == 0:
            # Still return a (valid, empty) zip rather than 404 so the UI's
            # download flow is uniform.
            pass
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="atlas-vault.zip"'},
        )

    @router.post("/import")
    async def import_vault(request: Request, files: List[UploadFile] = File(...)):
        """Import notes/attachments.

        Accepts either a single ``.zip`` (e.g. a previous export) or a set of
        files uploaded from a folder picker (``webkitdirectory``), in which case
        each ``UploadFile.filename`` carries its relative path within the chosen
        folder. Every destination path is sanitised through ``safe_atlas_path``
        (zip-slip / traversal defense) and only markdown + known attachment
        types are kept.
        """
        owner = require_user(request)
        imported = 0
        skipped = 0

        def _accept(rel: str, data: bytes) -> bool:
            nonlocal imported, skipped
            ext = os.path.splitext(rel)[1].lower()
            if ext not in NOTE_EXTS and ext not in ATTACH_EXTS:
                skipped += 1
                return False
            try:
                abs_path = safe_atlas_path(owner, rel)
            except AtlasPathError:
                skipped += 1
                return False
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(data)
            imported += 1
            return True

        for up in files:
            raw = await up.read()
            name = up.filename or ""
            if name.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                        for info in zf.infolist():
                            if info.is_dir():
                                continue
                            # Strip a single leading vault-name folder so an
                            # Obsidian export ("MyVault/note.md") lands flat.
                            entry = info.filename.replace("\\", "/")
                            _accept(entry, zf.read(info))
                except zipfile.BadZipFile:
                    raise HTTPException(400, "Invalid zip file")
            else:
                # Folder upload: filename is the path relative to the picked dir.
                # Drop the top-level folder component so the vault root isn't
                # nested under the source folder's name.
                rel = name.replace("\\", "/")
                parts = rel.split("/", 1)
                if len(parts) == 2 and parts[1]:
                    rel = parts[1]
                _accept(rel, raw)

        invalidate_cache(owner)
        return {"ok": True, "imported": imported, "skipped": skipped}

    return router
