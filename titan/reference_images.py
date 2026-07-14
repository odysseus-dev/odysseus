"""Resolve reference_images for Titan scheduler identity backends."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.constants import GENERATED_IMAGES_DIR


def _host_data_dir() -> str:
    return os.environ.get("TITAN_HOST_DATA_DIR", "").rstrip("/")


def host_path_for_scheduler(path: str) -> str:
    """Map container /app/data paths to host paths for the scheduler."""
    p = str(path or "").strip()
    if not p:
        return p
    host_data = _host_data_dir()
    if p.startswith("/app/data/") and host_data:
        return f"{host_data}{p[len('/app/data'):]}"
    return p


def aggregate_ip_weight(
    reference_images: List[Dict[str, Any]],
    explicit: Optional[float] = None,
) -> Optional[float]:
    if explicit is not None:
        return max(0.0, min(1.0, float(explicit)))
    if not reference_images:
        return None
    weights: list[float] = []
    for ref in reference_images:
        w = ref.get("weight")
        if w is None:
            weights.append(0.7)
        else:
            try:
                weights.append(max(0.0, min(1.0, float(w))))
            except (TypeError, ValueError):
                weights.append(0.7)
    if not weights:
        return 0.7
    return max(0.0, min(1.0, sum(weights) / len(weights)))


def _strip_data_url(raw: str) -> str:
    s = str(raw or "").strip()
    if s.startswith("data:"):
        comma = s.find(",")
        if comma >= 0:
            return s[comma + 1 :]
    return s


def _gallery_file_path(gallery_id: str, owner: Optional[str]) -> Optional[Path]:
    if not gallery_id:
        return None
    try:
        from core.database import GalleryImage, SessionLocal
        from src.auth_helpers import owner_filter

        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(
                GalleryImage.id == gallery_id,
                GalleryImage.is_active == True,  # noqa: E712
            )
            if owner:
                q = owner_filter(q, GalleryImage, owner)
            row = q.first()
            if not row or not row.filename:
                return None
            return Path(GENERATED_IMAGES_DIR) / row.filename
        finally:
            db.close()
    except Exception:
        return None


def resolve_one_reference(entry: Dict[str, Any], *, owner: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resolve a single reference spec to scheduler payload ({path|b64, role, weight})."""
    if not isinstance(entry, dict):
        return None

    role = str(entry.get("role") or "identity").strip().lower()
    if role not in ("identity", "style", "composition"):
        role = "identity"

    weight_raw = entry.get("weight")
    weight: Optional[float]
    if weight_raw is None:
        weight = None
    else:
        try:
            weight = max(0.0, min(1.0, float(weight_raw)))
        except (TypeError, ValueError):
            weight = 0.7

    b64 = entry.get("b64") or entry.get("data") or entry.get("image_b64")
    if b64 and str(b64).strip():
        out: Dict[str, Any] = {"b64": _strip_data_url(str(b64)), "role": role}
        if weight is not None:
            out["weight"] = weight
        gid = str(entry.get("gallery_id") or "").strip()
        if gid:
            out["gallery_id"] = gid
        return out

    gallery_id = str(entry.get("gallery_id") or "").strip()
    if gallery_id:
        fp = _gallery_file_path(gallery_id, owner)
        if fp and fp.is_file():
            out = {
                "path": host_path_for_scheduler(str(fp)),
                "role": role,
                "gallery_id": gallery_id,
            }
            if weight is not None:
                out["weight"] = weight
            return out
        return None

    path = str(entry.get("path") or entry.get("image") or entry.get("url") or "").strip()
    if not path:
        return None

    if "/api/generated-image/" in path:
        fname = path.rsplit("/", 1)[-1].split("?", 1)[0]
        fp = Path(GENERATED_IMAGES_DIR) / fname
        if fp.is_file():
            path = str(fp)

    if not path.startswith("/") and "://" not in path:
        fp = Path(GENERATED_IMAGES_DIR) / path
        if fp.is_file():
            path = str(fp)

    host_path = host_path_for_scheduler(path)
    p = Path(host_path)
    if not p.is_file():
        alt = Path(GENERATED_IMAGES_DIR) / p.name
        if alt.is_file():
            p = alt
            host_path = str(alt)
        else:
            return None

    out = {"path": host_path, "role": role}
    if weight is not None:
        out["weight"] = weight
    return out


def resolve_reference_images_for_scheduler(
    reference_images: Any,
    *,
    owner: Optional[str] = None,
    prefer_b64: bool = False,
) -> List[Dict[str, Any]]:
    """Resolve UI/API reference specs to scheduler-ready entries."""
    if not reference_images or not isinstance(reference_images, list):
        return []
    resolved: List[Dict[str, Any]] = []
    for entry in reference_images:
        one = resolve_one_reference(entry, owner=owner)
        if not one:
            continue
        if prefer_b64 and one.get("path"):
            fp = Path(str(one["path"]))
            if fp.is_file():
                one = dict(one)
                one.pop("path", None)
                one["b64"] = base64.b64encode(fp.read_bytes()).decode("ascii")
        resolved.append(one)
    return resolved
