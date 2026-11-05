"""Who wrote an image description: a vision model or a person.

Stored on GalleryImage.caption_source as "vision:<model>" or "user:<name>",
and next to the chat upload's cached vision text (``<cache>.source``) so a
later sync from that cache keeps the right author.
"""

from __future__ import annotations

from typing import Optional


def vision_source(model: Optional[str]) -> str:
    model = str(model or "").strip()
    return f"vision:{model}" if model else ""


def user_source(username: Optional[str]) -> str:
    username = str(username or "").strip()
    return f"user:{username}" if username else "user:"


def write_cache_source(cache_path: str, source: str) -> None:
    try:
        with open(cache_path + ".source", "w", encoding="utf-8") as f:
            f.write(source or "")
    except OSError:
        pass


def read_cache_source(cache_path: str) -> str:
    try:
        with open(cache_path + ".source", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""
