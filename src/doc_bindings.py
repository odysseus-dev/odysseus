"""
Docker/env-driven document folder bindings for Odysseus.

This module is intentionally additive:
- It does not replace normal uploads or chat/document attachments.
- It indexes Docker-mounted folders for selected users by writing owner
  metadata into the existing RAG/vector layer.

Environment:
    ODYSSEUS_DOC_BINDINGS_JSON
    ODYSSEUS_DOC_BINDINGS_ROOT
    ODYSSEUS_DOC_BINDINGS_STRICT
    ODYSSEUS_DOC_BINDINGS_REINDEX_ON_STARTUP
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable

try:
    from src.rag_vector import DEFAULT_FILE_EXTENSIONS
except Exception:  # pragma: no cover - defensive fallback
    DEFAULT_FILE_EXTENSIONS = {".txt", ".md", ".json", ".pdf"}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocBinding:
    id: str
    name: str
    path: str
    readers: tuple[str, ...]
    writers: tuple[str, ...] = ()
    mode: str = "ro"
    recursive: bool = True
    allowed_extensions: tuple[str, ...] = ()
    source: str = "env"

    def can_read(self, username: str | None) -> bool:
        return _norm_user(username) in self.readers

    def can_write(self, username: str | None) -> bool:
        return self.mode == "rw" and _norm_user(username) in self.writers

    @property
    def extensions_for_index(self) -> set[str]:
        return set(self.allowed_extensions or tuple(DEFAULT_FILE_EXTENSIONS))


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _norm_user(value: str | None) -> str:
    return str(value or "").strip().lower()


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        if value.startswith("["):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [_norm_user(v) for v in parsed if _norm_user(v)]
            except Exception:
                pass
        return [_norm_user(v) for v in value.split(",") if _norm_user(v)]
    if isinstance(value, Iterable):
        return [_norm_user(v) for v in value if _norm_user(v)]
    return []


def _known_users(auth_manager: Any) -> set[str]:
    users = getattr(auth_manager, "users", {}) or {}
    if isinstance(users, dict):
        return {_norm_user(u) for u in users.keys() if _norm_user(u)}
    return {_norm_user(u) for u in users if _norm_user(u)}


def _under_root(path: str, root: str) -> bool:
    if not root:
        return True
    try:
        path_real = os.path.realpath(path)
        root_real = os.path.realpath(root)
        return os.path.commonpath([path_real, root_real]) == root_real
    except ValueError:
        return False


def load_doc_bindings(auth_manager: Any = None) -> list[DocBinding]:
    raw = os.getenv("ODYSSEUS_DOC_BINDINGS_JSON", "").strip()
    if not raw:
        return []

    strict = _truthy(os.getenv("ODYSSEUS_DOC_BINDINGS_STRICT"), default=False)
    root = os.getenv("ODYSSEUS_DOC_BINDINGS_ROOT", "").strip()
    known = _known_users(auth_manager) if auth_manager is not None else set()

    try:
        payload = json.loads(raw)
    except Exception as exc:
        msg = f"ODYSSEUS_DOC_BINDINGS_JSON is not valid JSON: {exc}"
        if strict:
            raise RuntimeError(msg) from exc
        logger.warning(msg)
        return []

    if not isinstance(payload, list):
        msg = "ODYSSEUS_DOC_BINDINGS_JSON must be a JSON list"
        if strict:
            raise RuntimeError(msg)
        logger.warning(msg)
        return []

    bindings: list[DocBinding] = []

    for idx, item in enumerate(payload):
        try:
            if not isinstance(item, dict):
                raise ValueError("binding entry must be an object")

            path = os.path.abspath(str(item.get("path") or "").strip())
            if not path:
                raise ValueError("binding path is required")

            binding_id = str(item.get("id") or os.path.basename(path) or f"binding-{idx}").strip()
            name = str(item.get("name") or binding_id).strip()

            # Backward-friendly: owners means readers.
            readers = _as_string_list(item.get("readers", item.get("owners")))
            writers = _as_string_list(item.get("writers"))
            mode = str(item.get("mode") or ("rw" if writers else "ro")).strip().lower()
            recursive = bool(item.get("recursive", True))

            allowed_extensions = tuple(
                ext if str(ext).startswith(".") else f".{ext}"
                for ext in _as_string_list(item.get("allowed_extensions"))
            )

            if mode not in {"ro", "rw"}:
                raise ValueError("mode must be either ro or rw")
            if not readers:
                raise ValueError("readers/owners must include at least one user")
            if not set(writers).issubset(set(readers)):
                raise ValueError("writers must be a subset of readers")
            if known:
                unknown_readers = sorted(set(readers) - known)
                unknown_writers = sorted(set(writers) - known)
                if unknown_readers or unknown_writers:
                    raise ValueError(
                        f"unknown user(s): readers={unknown_readers}, writers={unknown_writers}"
                    )
            if not os.path.exists(path):
                raise ValueError(f"path does not exist: {path}")
            if not os.path.isdir(path):
                raise ValueError(f"path is not a directory: {path}")
            if root and not _under_root(path, root):
                raise ValueError(f"path is outside ODYSSEUS_DOC_BINDINGS_ROOT: {path}")
            if mode == "rw" and not os.access(path, os.W_OK):
                msg = f"binding {binding_id!r} is configured rw but filesystem/Docker mount is not writable"
                if strict:
                    raise ValueError(msg)
                logger.warning(msg)

            bindings.append(
                DocBinding(
                    id=binding_id,
                    name=name,
                    path=path,
                    readers=tuple(readers),
                    writers=tuple(writers),
                    mode=mode,
                    recursive=recursive,
                    allowed_extensions=allowed_extensions,
                )
            )

        except Exception as exc:
            msg = f"Skipping document binding #{idx}: {exc}"
            if strict:
                raise RuntimeError(msg) from exc
            logger.warning(msg)

    return bindings


def sync_doc_bindings(auth_manager: Any, rag_manager: Any, personal_docs_manager: Any = None) -> list[DocBinding]:
    """Load env bindings and index each folder once per allowed reader/user."""
    bindings = load_doc_bindings(auth_manager)

    if personal_docs_manager is not None:
        # Store bindings for future route/UI filtering without adding these
        # folders to the legacy global Personal Docs listing.
        setattr(personal_docs_manager, "doc_bindings", bindings)

    if not bindings:
        logger.info("No Docker/env document bindings configured")
        return []

    if rag_manager is None:
        logger.warning("Document bindings configured but RAG manager is not available")
        return bindings

    reindex = _truthy(os.getenv("ODYSSEUS_DOC_BINDINGS_REINDEX_ON_STARTUP"), default=True)

    for binding in bindings:
        for owner in binding.readers:
            try:
                if reindex and hasattr(rag_manager, "remove_directory"):
                    rag_manager.remove_directory(binding.path, owner=owner)

                result = rag_manager.index_personal_documents(
                    binding.path,
                    file_extensions=binding.extensions_for_index,
                    owner=owner,
                )
                logger.info(
                    "Indexed binding %s for owner=%s: %s",
                    binding.id,
                    owner,
                    result.get("message", result),
                )
            except Exception as exc:
                logger.exception(
                    "Failed indexing binding %s for owner=%s: %s",
                    binding.id,
                    owner,
                    exc,
                )

    return bindings


def get_readable_bindings(username: str | None, bindings: Iterable[DocBinding]) -> list[DocBinding]:
    return [binding for binding in bindings if binding.can_read(username)]


def get_writable_bindings(username: str | None, bindings: Iterable[DocBinding]) -> list[DocBinding]:
    return [binding for binding in bindings if binding.can_write(username)]


def resolve_bound_path(
    username: str | None,
    candidate_path: str,
    bindings: Iterable[DocBinding],
    *,
    require_write: bool = False,
) -> tuple[DocBinding, str]:
    """Resolve a path inside a binding and enforce reader/writer access."""
    resolved = os.path.realpath(candidate_path)
    user = _norm_user(username)

    for binding in bindings:
        root = os.path.realpath(binding.path)
        try:
            inside = os.path.commonpath([resolved, root]) == root
        except ValueError:
            inside = False
        if not inside:
            continue
        if require_write and not binding.can_write(user):
            raise PermissionError("User does not have write access to this bound vault")
        if not require_write and not binding.can_read(user):
            raise PermissionError("User does not have read access to this bound vault")
        return binding, resolved

    raise FileNotFoundError("Path is not inside an accessible bound vault")
