"""Path helpers for per-project filesystems.

The on-disk layout is::

    DATA_DIR/projects/<owner_slug>/<project_id>/
        memory.json
        memory_vectors/
        uploads/
        rag_index.json
        memory_tidy_state.json

`<owner_slug>` is a sanitized form of the username so the path is always
filesystem-safe. The full original owner string is preserved on the
`DbProject` row for ownership checks — only the path component is slugged.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from src.constants import PROJECTS_DIR

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_SLUG_DASH_RE = re.compile(r"-{2,}")


def slugify_owner(name: str, *, fallback: Optional[str] = None) -> str:
    """Sanitize `name` for use as a filesystem path component.

    Lowercases, replaces anything that isn't ``[a-z0-9_-]`` with ``_``,
    collapses runs of ``-``. If the result is empty (e.g. an owner named
    ``"!!!"``) returns the `fallback` if given, else the literal string
    ``"owner"`` so the path is never an empty segment.
    """
    if not name:
        return fallback or "owner"
    slug = _SLUG_RE.sub("_", name.strip().lower()).strip("_")
    slug = _SLUG_DASH_RE.sub("-", slug)
    if not slug:
        return fallback or "owner"
    return slug


def project_data_dir(owner: str, project_id: str) -> str:
    """Return the absolute path to the project's data directory.

    `owner` may already be a slug, in which case it's used as-is. This is
    the only place that knows the on-disk layout — callers should not
    re-derive the path.
    """
    return os.path.join(PROJECTS_DIR, slugify_owner(owner), project_id)
