"""document_folders.py — shared service for nested, owner-scoped document folders.

Single source of truth for the folder-name normalization + find-or-create
rule and the small in-Python tree helpers (depth / descendants / subtree
height), so the HTTP routes (routes/document_routes.py) and the agent tool
(src/agent_tools/document_tools.py) behave identically. Imports only the ORM
model from core.database; never imports from routes/ (would be circular).
"""

import re
import uuid
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from core.database import DocumentFolder
from src.constants import DOCUMENT_FOLDER_MAX_DEPTH

logger = logging.getLogger(__name__)


# Folder names are short labels; cap so a runaway agent-supplied name can't
# bloat the row / sidebar. Generous enough for a long descriptive name.
_MAX_FOLDER_NAME = 100


def normalize_folder_name(name: str) -> str:
    """Trim and collapse internal whitespace; preserve case and accents.

    Display-faithful: "  Acme   Corp " -> "Acme Corp". Case/diacritics are
    kept for display; case-insensitive matching is handled by the column's
    NOCASE collation, not by lowercasing here. The result is capped at
    _MAX_FOLDER_NAME characters (re-stripped so the cap can't leave a trailing
    space).
    """
    collapsed = re.sub(r"\s+", " ", (name or "").strip())
    return collapsed[:_MAX_FOLDER_NAME].strip()


def _same_slot_filter(q, owner: Optional[str], norm: str, parent_id: Optional[str]):
    """Scope a DocumentFolder query to one (owner, name, parent) slot.

    parent_id=None targets ROOT folders (parent_id IS NULL); a non-null
    parent_id targets that parent's direct children.
    """
    q = q.filter(DocumentFolder.owner == owner, DocumentFolder.name == norm)
    if parent_id is None:
        return q.filter(DocumentFolder.parent_id.is_(None))
    return q.filter(DocumentFolder.parent_id == parent_id)


def find_or_create_folder(
    db, owner: Optional[str], name: str, parent_id: Optional[str] = None
) -> DocumentFolder:
    """Return the owner's folder named `name` under `parent_id`, creating it if
    missing. parent_id=None => a ROOT (top-level) folder.

    Case-insensitive match via the document_folders.name NOCASE collation, so
    "Acme" and "acme" resolve to the same row within the same parent slot.
    Root creates are guarded by the PARTIAL unique (owner, name) WHERE parent_id
    IS NULL index: on a concurrent-create race it raises IntegrityError, we roll
    back and re-select the winner's row (retry-once). Nested slots have no DB
    unique index (duplicate names between parents are allowed), so the
    SELECT-first below is what dedupes them. The caller validated `owner`.

    Caveat: the unique index only dedupes for a non-NULL owner (SQLite treats
    NULLs as distinct in unique indexes), so in single-user / dev mode
    (AUTH_ENABLED=false, owner NULL) it is the SELECT-first — not the index —
    that prevents duplicates. Production runs auth-on (non-NULL owner).
    """
    norm = normalize_folder_name(name)
    existing = _same_slot_filter(
        db.query(DocumentFolder), owner, norm, parent_id
    ).first()
    if existing:
        return existing
    folder = DocumentFolder(id=str(uuid.uuid4()), name=norm, owner=owner, parent_id=parent_id)
    db.add(folder)
    try:
        db.commit()
    except IntegrityError:
        # Lost a create race against a concurrent writer — the root partial
        # unique index rejected our insert. Reuse the winner's row.
        db.rollback()
        folder = _same_slot_filter(
            db.query(DocumentFolder), owner, norm, parent_id
        ).first()
        if folder is None:
            # Index violation but no row found — re-raise rather than silently
            # returning None (would be a harder bug to trace downstream).
            raise
        return folder
    db.refresh(folder)
    return folder


def _folder_depth(folder_id: str, parent_by_id: Dict[str, Optional[str]]) -> int:
    """1-based depth of a folder — a root folder is depth 1.

    `parent_by_id` maps folder id -> its parent_id (None for roots). Iteration
    is capped so a corrupt parent cycle can't spin forever.
    """
    depth = 1
    cur = parent_by_id.get(folder_id)
    steps = 0
    while cur is not None and steps <= DOCUMENT_FOLDER_MAX_DEPTH + 1:
        depth += 1
        cur = parent_by_id.get(cur)
        steps += 1
    return depth


def _descendant_ids(folder_id: str, children_by_parent: Dict[str, List[str]]) -> List[str]:
    """Ids of every folder below `folder_id` (excludes itself).

    `children_by_parent` maps folder id -> list of direct child ids. The visited
    set keeps it cycle-safe even on a corrupt tree.
    """
    out: List[str] = []
    seen = set()
    stack = list(children_by_parent.get(folder_id, []))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        stack.extend(children_by_parent.get(cur, []))
    return out


def _subtree_height(folder_id: str, children_by_parent: Dict[str, List[str]]) -> int:
    """Number of levels in the subtree rooted at `folder_id`, INCLUSIVE.

    A leaf folder has height 1. Iterative BFS with a visited guard so a corrupt
    cycle can't loop forever. Used for the move depth check:
    depth(new_parent) + subtree_height(moved) must be <= DOCUMENT_FOLDER_MAX_DEPTH.
    """
    seen = {folder_id}
    frontier = [folder_id]
    height = 0
    while frontier and height <= DOCUMENT_FOLDER_MAX_DEPTH + 1:
        height += 1
        nxt: List[str] = []
        for node in frontier:
            for child in children_by_parent.get(node, []):
                if child not in seen:
                    seen.add(child)
                    nxt.append(child)
        frontier = nxt
    return height


def folder_to_dict(
    folder: DocumentFolder,
    count: Optional[int] = None,
    depth: Optional[int] = None,
) -> Dict[str, Any]:
    """Serialize a folder for API responses.

    Always includes id/name/parent_id + created_at/updated_at; `count` (direct
    docs) and `depth` (1-based) are added when the caller has computed them.
    """
    out: Dict[str, Any] = {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
    }
    if depth is not None:
        out["depth"] = depth
    if count is not None:
        out["count"] = count
    out["created_at"] = (folder.created_at.isoformat() + "Z") if folder.created_at else None
    out["updated_at"] = (folder.updated_at.isoformat() + "Z") if folder.updated_at else None
    return out
