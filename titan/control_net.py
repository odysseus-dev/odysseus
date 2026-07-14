"""ControlNet contract helpers — shared by ImageProposal and Fugassa."""

from __future__ import annotations

import base64
import io
import os
import re
from typing import Any

_DATA_URL_RE = re.compile(r"^data:image/[^;]+;base64,", re.I)
_HOST_DATA_DIR = os.environ.get("TITAN_HOST_DATA_DIR", "").rstrip("/")


def _strip_data_url(b64: str) -> str:
    s = str(b64 or "").strip()
    if not s:
        return ""
    return _DATA_URL_RE.sub("", s)


def _resolve_path(raw: str) -> str:
    path = str(raw or "").strip()
    if not path:
        return ""
    if path.startswith("/app/data/") and _HOST_DATA_DIR:
        return f"{_HOST_DATA_DIR}{path[len('/app/data'):]}"
    return path


def normalize_control(raw: Any) -> dict[str, Any] | None:
    if not raw or not isinstance(raw, dict):
        return None
    ctype = str(raw.get("type") or "canny").strip().lower()
    if ctype not in {"canny", "depth", "pose", "openpose", "raw"}:
        ctype = "canny"
    try:
        weight = float(raw.get("weight", raw.get("control_strength", 0.65)))
    except (TypeError, ValueError):
        weight = 0.65
    weight = max(0.0, min(1.0, weight))
    preprocess = raw.get("preprocess")
    if preprocess is None:
        preprocess = ctype == "canny"
    else:
        preprocess = bool(preprocess)
    out: dict[str, Any] = {
        "type": ctype,
        "weight": weight,
        "preprocess": preprocess,
    }
    for key in ("path", "b64", "image", "gallery_id"):
        val = raw.get(key)
        if val and str(val).strip():
            out[key] = str(val).strip()
    if not any(out.get(k) for k in ("path", "b64", "image", "gallery_id")):
        return None
    return out


def _preprocess_canny(image_bytes: bytes) -> bytes:
    """cv2.Canny — kept for unit tests; production preprocess runs in titan-scheduler."""
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    low = int(os.environ.get("TITAN_CANNY_LOW", "100"))
    high = int(os.environ.get("TITAN_CANNY_HIGH", "200"))
    try:
        import cv2
        import numpy as np

        arr = np.array(img)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, low, high)
        rgb = np.stack([edges, edges, edges], axis=-1)
        out = Image.fromarray(rgb)
    except ImportError:
        from PIL import ImageFilter

        gray = img.convert("L")
        edges = gray.filter(ImageFilter.FIND_EDGES)
        edges = edges.point(lambda p: 255 if p > 32 else 0)
        out = Image.merge("RGB", (edges, edges, edges))
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def resolve_control_for_scheduler(
    control: Any,
    *,
    owner: str | None = None,
) -> dict[str, Any] | None:
    """Resolve gallery/path/b64 control to scheduler-ready dict with embedded b64."""
    normalized = normalize_control(control)
    if not normalized:
        return None

    image_bytes: bytes | None = None
    b64 = normalized.get("b64") or normalized.get("image")
    if b64:
        try:
            image_bytes = base64.b64decode(_strip_data_url(str(b64)))
        except Exception:
            image_bytes = None
    elif normalized.get("path"):
        path = _resolve_path(str(normalized["path"]))
        if path and os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    image_bytes = f.read()
            except OSError:
                image_bytes = None
    elif normalized.get("gallery_id"):
        try:
            from titan.reference_images import resolve_one_reference

            ref = resolve_one_reference({"gallery_id": normalized["gallery_id"]}, owner=owner)
            if ref and ref.get("b64"):
                image_bytes = base64.b64decode(_strip_data_url(str(ref["b64"])))
            elif ref and ref.get("path") and os.path.isfile(ref["path"]):
                with open(ref["path"], "rb") as f:
                    image_bytes = f.read()
        except Exception:
            image_bytes = None

    if not image_bytes:
        return None

    # Scheduler applies cv2.Canny when preprocess=True — send raw RGB here.
    out = dict(normalized)
    out.pop("gallery_id", None)
    out.pop("path", None)
    out["b64"] = base64.b64encode(image_bytes).decode("ascii")
    return out
