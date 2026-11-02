import re
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from src.constants import GENERATED_IMAGES_DIR
from src.path_confinement import confine


GENERATED_IMAGE_DIR = Path(GENERATED_IMAGES_DIR)
GENERATED_IMAGE_RE = re.compile(
    r"^[a-f0-9]{8,64}\.(png|jpg|jpeg|webp|gif|mp4|mov|webm|mkv|m4v)$"
)
GENERATED_IMAGE_HEADERS = {
    "Cache-Control": "private, no-cache, must-revalidate",
    "X-Content-Type-Options": "nosniff",
}

_RECENT_GENERATED_IMAGES: dict[str, tuple[str, float]] = {}
_RECENT_IMAGES_LOCK = threading.Lock()
_RECENT_IMAGES_TTL = 3600  # 1 hour TTL


def register_generated_image(filename: str, owner: Optional[str]) -> None:
    """Record ownership of a freshly generated image.

    Ensures legitimate access to freshly generated images even before
    or in case Gallery database records are created/committed.
    """
    if not filename or not owner:
        return
    now = time.time()
    with _RECENT_IMAGES_LOCK:
        if len(_RECENT_GENERATED_IMAGES) > 200:
            expired = [k for k, (_, exp) in _RECENT_GENERATED_IMAGES.items() if exp < now]
            for k in expired:
                _RECENT_GENERATED_IMAGES.pop(k, None)
        _RECENT_GENERATED_IMAGES[filename] = (owner, now + _RECENT_IMAGES_TTL)


def get_registered_image_owner(filename: str) -> Optional[str]:
    """Return the owner of a freshly generated image if still valid in the registry."""
    if not filename:
        return None
    now = time.time()
    with _RECENT_IMAGES_LOCK:
        entry = _RECENT_GENERATED_IMAGES.get(filename)
        if not entry:
            return None
        owner, expiry = entry
        if now > expiry:
            _RECENT_GENERATED_IMAGES.pop(filename, None)
            return None
        return owner


def resolve_generated_image_path(filename: str) -> Path:
    if not isinstance(filename, str) or not GENERATED_IMAGE_RE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    try:
        path = Path(confine(GENERATED_IMAGE_DIR, filename, allow_root=False))
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return path

