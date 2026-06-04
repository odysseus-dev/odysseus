# routes/project_routes.py
"""Routes for Projects folder validation, creation, and file browsing."""
import os
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)

# Maximum allowed folder path length to prevent abuse
_MAX_PATH_LEN = 512

# Directories to exclude from the file tree
_IGNORED_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "dist", "build", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    "target", "vendor", ".cache", ".tox", "coverage", ".next", ".nuxt",
    ".svelte-kit", "out", ".output", ".gradle", ".settings",
})

# File extensions treated as plain text (readable via the API)
_TEXT_EXTS = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".conf", ".env", ".env.example",
    ".md", ".mdx", ".rst", ".txt", ".csv", ".tsv",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".sql", ".go", ".rs", ".c", ".cpp", ".cc", ".cxx",
    ".h", ".hpp", ".hxx", ".java", ".kt", ".kts", ".swift",
    ".php", ".rb", ".pl", ".r", ".m", ".lua",
    ".ex", ".exs", ".erl", ".hs", ".clj", ".cljs",
    ".xml", ".svg", ".vue", ".svelte",
    ".graphql", ".gql", ".diff", ".patch",
    ".gitignore", ".gitattributes", ".editorconfig",
    ".prettierrc", ".eslintrc", ".babelrc", ".nvmrc", ".npmrc",
})

# File names (without extension) treated as plain text
_TEXT_NAMES = frozenset({
    "Makefile", "makefile", "Dockerfile", "dockerfile",
    "Jenkinsfile", "Procfile", "Vagrantfile", "Gemfile",
    "Rakefile", "Guardfile", "Capfile", ".env", ".gitignore",
    ".gitattributes", ".editorconfig", ".nvmrc", ".npmrc",
    "requirements.txt", "setup.cfg", "pyproject.toml",
    "package.json", "tsconfig.json", "jsconfig.json",
    "README", "LICENSE", "CHANGELOG", "AUTHORS",
})

_MAX_FILES_PER_DIR = 100
_MAX_TREE_DEPTH = 4
_MAX_FILE_READ_BYTES = 150_000  # 150 KB


def _is_readable(name: str) -> bool:
    """Return True if the file can be read as plain text."""
    ext = os.path.splitext(name)[1].lower()
    return ext in _TEXT_EXTS or name in _TEXT_NAMES


class FolderRequest(BaseModel):
    path: str


def _validate_path_arg(path: str) -> str:
    """Sanitize and return an absolute path; raise HTTPException on bad input."""
    if not path or not path.strip():
        raise HTTPException(400, "Path is required")
    path = path.strip()
    if len(path) > _MAX_PATH_LEN:
        raise HTTPException(400, "Path is too long")
    # Resolve to absolute path (handles . and .. components)
    abs_path = os.path.realpath(os.path.abspath(path))
    # Reject null bytes and shell metacharacters
    for ch in ("\x00",):
        if ch in abs_path:
            raise HTTPException(400, "Invalid character in path")
    return abs_path


def setup_project_routes():
    router = APIRouter(prefix="/api/projects")

    @router.post("/validate-folder")
    async def validate_folder(body: FolderRequest, request: Request):
        """Check whether the given folder path exists on the server."""
        get_current_user(request)  # ensure authenticated
        abs_path = _validate_path_arg(body.path)
        exists = os.path.isdir(abs_path)
        return {"exists": exists, "path": abs_path}

    @router.post("/create-folder")
    async def create_folder(body: FolderRequest, request: Request):
        """Create the folder at the given path (mkdir -p).

        Returns the resolved absolute path on success.
        """
        get_current_user(request)  # ensure authenticated
        abs_path = _validate_path_arg(body.path)
        if os.path.isdir(abs_path):
            return {"created": False, "path": abs_path}
        if os.path.exists(abs_path):
            raise HTTPException(400, "Path exists but is not a directory")
        try:
            os.makedirs(abs_path, exist_ok=True)
        except OSError as exc:
            logger.warning("Failed to create project folder %r: %s", abs_path, exc)
            raise HTTPException(500, f"Could not create folder: {exc.strerror}") from exc
        return {"created": True, "path": abs_path}

    @router.get("/files")
    async def list_project_files(
        request: Request,
        path: str = Query(...),
        rel: str = Query(""),
        depth: int = Query(2, ge=1, le=_MAX_TREE_DEPTH),
    ):
        """List files and directories inside a project folder.

        ``path`` is the project root (validated absolute path).
        ``rel``  is an optional sub-directory relative to the root.
        ``depth`` controls how many levels deep to recurse (1–4).
        """
        get_current_user(request)
        root = _validate_path_arg(path)
        if not os.path.isdir(root):
            raise HTTPException(404, "Project folder not found")

        # Validate optional subdir stays inside root
        if rel:
            target = os.path.realpath(os.path.join(root, rel.lstrip("/")))
            try:
                in_root = os.path.commonpath([target, root]) == root
            except ValueError:
                in_root = False
            if not in_root:
                raise HTTPException(403, "Path is outside project folder")
            if not os.path.isdir(target):
                raise HTTPException(404, "Subdirectory not found")
            scan_base = target
        else:
            scan_base = root

        def _scan(dirpath: str, base_rel: str, cur_depth: int) -> list:
            items = []
            try:
                entries = sorted(
                    os.scandir(dirpath),
                    key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()),
                )
            except OSError:
                return items

            count = 0
            for entry in entries:
                if count >= _MAX_FILES_PER_DIR:
                    items.append({"name": "…", "type": "truncated", "rel": ""})
                    break
                name = entry.name
                # Skip hidden files except common config files
                if name.startswith(".") and name not in {
                    ".env", ".gitignore", ".gitattributes", ".editorconfig",
                    ".nvmrc", ".npmrc", ".prettierrc", ".eslintrc", ".babelrc",
                    ".env.example",
                }:
                    continue
                entry_rel = (base_rel + "/" + name) if base_rel else name
                if entry.is_dir(follow_symlinks=False):
                    if name in _IGNORED_DIRS:
                        continue
                    item: dict = {"name": name, "type": "dir", "rel": entry_rel}
                    if cur_depth < depth:
                        item["children"] = _scan(entry.path, entry_rel, cur_depth + 1)
                    else:
                        item["collapsed"] = True  # has children but not yet loaded
                    items.append(item)
                else:
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    items.append({
                        "name": name,
                        "type": "file",
                        "rel": entry_rel,
                        "size": size,
                        "readable": _is_readable(name),
                    })
                count += 1
            return items

        base_rel = rel.strip("/") if rel else ""
        tree = _scan(scan_base, base_rel, 1)
        return {"tree": tree, "path": root, "rel": base_rel}

    @router.get("/file-content")
    async def read_project_file(
        request: Request,
        path: str = Query(...),
        rel: str = Query(...),
    ):
        """Return the text content of a single file inside a project folder.

        ``path`` is the project root.  ``rel`` is the file path relative to root.
        Returns HTTP 413 if the file exceeds the size limit.
        Returns HTTP 415 if the file is binary or an unsupported type.
        """
        get_current_user(request)
        root = _validate_path_arg(path)
        if not os.path.isdir(root):
            raise HTTPException(404, "Project folder not found")

        file_path = os.path.realpath(os.path.join(root, rel.lstrip("/")))
        try:
            in_root = os.path.commonpath([file_path, root]) == root
        except ValueError:
            in_root = False
        if not in_root:
            raise HTTPException(403, "Path is outside project folder")
        if not os.path.isfile(file_path):
            raise HTTPException(404, "File not found")

        name = os.path.basename(file_path)
        if not _is_readable(name):
            raise HTTPException(415, "Binary or unsupported file type")

        size = os.path.getsize(file_path)
        if size > _MAX_FILE_READ_BYTES:
            raise HTTPException(
                413,
                f"File too large ({size:,} bytes; limit {_MAX_FILE_READ_BYTES:,})",
            )

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as exc:
            raise HTTPException(500, f"Could not read file: {exc.strerror}") from exc

        truncated = len(content) > _MAX_FILE_READ_BYTES
        if truncated:
            content = content[:_MAX_FILE_READ_BYTES]

        return {
            "content": content,
            "truncated": truncated,
            "size": size,
            "rel": rel.strip("/"),
            "name": name,
        }

    return router
