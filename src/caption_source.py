"""Who wrote an image description: a vision model or a person.

GalleryImage.caption_source holds "vision:<model>" or "user:<name>". Next to
an upload's cached vision text only the kind ("vision" or "user") is kept,
so no name is written to disk. The gallery row records the actual author
(the model, or whichever user wrote or edited the text) when the text is
written; a later sync from the cache keeps it.
"""

from __future__ import annotations

from typing import Optional

VISION = "vision"
USER = "user"


def vision_source(model: Optional[str]) -> str:
    model = str(model or "").strip()
    return f"{VISION}:{model}" if model else ""


def user_source(username: Optional[str]) -> str:
    username = str(username or "").strip()
    return f"{USER}:{username}" if username else f"{USER}:"


def write_cache_kind(cache_path: str, kind: str) -> None:
    """Record whether cached vision text came from a model or a person."""
    kind = kind if kind in (VISION, USER) else ""
    try:
        with open(cache_path + ".source", "w", encoding="utf-8") as f:
            f.write(kind)
    except OSError:
        pass


def read_cache_kind(cache_path: str) -> str:
    try:
        with open(cache_path + ".source", encoding="utf-8") as f:
            kind = f.read().strip()
    except OSError:
        return ""
    return kind if kind in (VISION, USER) else ""


def cache_fallback(kind: str, owner: Optional[str]) -> str:
    """Source for a gallery row that has none yet, when syncing a cache.

    A row that already has a source keeps it: it names the model, or the
    user who wrote or edited the text, which may not be the file's owner.
    """
    return user_source(owner) if kind == USER else ""
