"""Gallery routes — browsable library for photos and AI-generated images."""

import os
import asyncio
import base64
import gc
import hashlib
import io
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request

from core.database import SessionLocal, GalleryImage, GalleryAlbum, ModelEndpoint
from core.database import Session as DbSession
from src.auth_helpers import get_current_user, owner_filter, require_privilege
from src.session_search import escape_like_pattern
from src.upload_limits import (
    read_upload_limited,
    GALLERY_UPLOAD_MAX_BYTES,
    GALLERY_TRANSFORM_UPLOAD_MAX_BYTES,
)
from src.constants import GENERATED_IMAGES_DIR
from src.path_confinement import confine
from src.optional_deps import patch_realesrgan_torchvision_compat

from routes.gallery.gallery_helpers import (
    GalleryPatch, _extract_exif, _image_to_dict, _owner_filter, _human_size,
)

logger = logging.getLogger(__name__)

_SAM_STATE: Dict[str, Any] = {}
_GROUNDING_STATE: Dict[str, Any] = {}


def _rss_mb() -> float:
    """Current process RSS in MB (from /proc; -1.0 when unavailable)."""
    try:
        with open("/proc/self/status", "r") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except Exception:
        pass
    return -1.0


def _image_op_log_rss(op: str, phase: str) -> None:
    logger.info("image-op %s %s rss_mb=%.1f", op, phase, _rss_mb())


def _local_image_models_enabled() -> bool:
    """Whether GB-scale local image models may be loaded IN-PROCESS.

    Default OFF: loading rembg/SAM/OWL-ViT/Real-ESRGAN in the web process
    routinely OOM-kills memory-constrained hosts. Enable with
    ``ODYSSEUS_ALLOW_LOCAL_IMAGE_MODELS=1`` or point ``ODYSSEUS_IMAGE_OPS_BASE``
    at a remote service that implements the same ``/api/image/*`` endpoints.
    """
    return os.getenv("ODYSSEUS_ALLOW_LOCAL_IMAGE_MODELS", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _remote_image_ops_base() -> str:
    return (os.getenv("ODYSSEUS_IMAGE_OPS_BASE", "") or "").strip().rstrip("/")


def _outpaint_timeout() -> float:
    """Provider timeout for the outpaint/extend proxy.

    Tall extensions can take up to ~330 s (the provider's own timeout is
    900 s), so the proxy waits 900 s by default — otherwise a long extend is
    cut off at the proxy. Override with ``ODYSSEUS_IMAGE_OUTPAINT_TIMEOUT``.
    """
    try:
        return float(os.getenv("ODYSSEUS_IMAGE_OUTPAINT_TIMEOUT", "900") or 900)
    except (TypeError, ValueError):
        return 900.0


def _unload_image_models() -> None:
    """Drop lazily-loaded torch models so their RAM returns to the OS."""
    if not _SAM_STATE and not _GROUNDING_STATE:
        return
    _SAM_STATE.clear()
    _GROUNDING_STATE.clear()
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    gc.collect()
    _image_op_log_rss("models", "unloaded")


async def _route_heavy_image_op(path: str, body: dict, *, label: str):
    """Proxy a heavy image op to the remote ops service when configured, else
    run it in-process only when explicitly enabled.

    Returns the remote JSON on success, or ``None`` to signal the caller should
    run it in-process; raises HTTPException(503) with a clear message otherwise
    so the app never loads a GB-scale model on a memory-constrained host.
    """
    base = _remote_image_ops_base()
    if base:
        _image_op_log_rss(label, f"proxy->{base}")
        try:
            import httpx

            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(base + path, json=body)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("image-op %s remote %s -> %s", label, path, resp.status_code)
            raise HTTPException(
                503,
                f"{label} is temporarily unavailable (remote service returned {resp.status_code}).",
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("image-op %s remote %s failed: %s", label, path, exc)
            raise HTTPException(
                503,
                f"{label} is temporarily unavailable (remote service unreachable).",
            ) from exc
    if _local_image_models_enabled():
        return None
    raise HTTPException(503, f"{label} is temporarily unavailable on this server.")


def _xywh_to_xyxy(bbox):
    """Convert a remote image-ops [x, y, w, h] bbox to this app's [x1,y1,x2,y2]."""
    try:
        x, y, w, h = (float(v) for v in bbox)
        return [int(x), int(y), int(x + w), int(y + h)]
    except Exception:
        return bbox


async def _remote_remove_background(body: dict):
    """Proxy background removal to the remote ops service (seneca contract).

    Endpoint: POST {base}/v1/images/remove-background (multipart: image,
    optional hint_mask, model, return_mask). Returns {"image": <b64 RGBA PNG>}
    to match this route's local response shape, or None when the caller should
    run in-process (local models explicitly enabled); raises 503 otherwise.
    """
    base = _remote_image_ops_base()
    if not base:
        if _local_image_models_enabled():
            return None
        raise HTTPException(503, "Background removal is temporarily unavailable on this server.")
    img_bytes = _b64_bytes(body.get("image"))
    hint_bytes = None
    if body.get("hint_mask"):
        hint_bytes = _b64_bytes(body.get("hint_mask"), label="hint_mask")
    _image_op_log_rss("remove-bg", f"proxy->{base}")
    try:
        import httpx

        files = {"image": ("image.png", img_bytes, "image/png")}
        if hint_bytes:
            files["hint_mask"] = ("hint.png", hint_bytes, "image/png")
        data = {"model": str(body.get("model") or ""), "return_mask": "true"}
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(base + "/v1/images/remove-background", files=files, data=data)
        if resp.status_code == 200:
            payload = resp.json()
            items = payload.get("data") or []
            if not items or not items[0].get("b64_json"):
                raise HTTPException(503, "Background removal is temporarily unavailable (remote returned no image).")
            out = {"image": items[0]["b64_json"]}
            if items[0].get("mask_b64"):
                out["mask"] = items[0]["mask_b64"]
            if items[0].get("bbox"):
                out["bbox"] = items[0]["bbox"]
            return out
        raise HTTPException(
            503, f"Background removal is temporarily unavailable (remote service returned {resp.status_code})."
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("image-op remove-bg remote failed: %s", exc)
        raise HTTPException(503, "Background removal is temporarily unavailable (remote service unreachable).") from exc


async def _remote_smart_mask(body: dict):
    """Proxy SAM/grounding masking to the remote ops service (seneca contract).

    Endpoint: POST {base}/v1/images/mask (multipart: image + one of prompt/box/
    points). Returns {"mask": <b64 L PNG>, "bbox": [...], "model", "device"} to
    match this route's local response shape, or None when the caller should run
    in-process; raises 503 otherwise.
    """
    base = _remote_image_ops_base()
    if not base:
        if _local_image_models_enabled():
            return None
        raise HTTPException(503, "Smart mask is temporarily unavailable on this server.")
    img_bytes = _b64_bytes(body.get("image"))
    points = body.get("points") or []
    box = body.get("box")
    text = (body.get("text") or body.get("query") or "").strip()
    if not points and not box and not text:
        raise HTTPException(400, "Provide at least one point, box, or object text")
    _image_op_log_rss("mask", f"proxy->{base}")
    try:
        import httpx

        data: Dict[str, Any] = {}
        if text:
            data["prompt"] = text
        if box:
            try:
                x1, y1, x2, y2 = (float(v) for v in box)
                data["box"] = ",".join(str(int(v)) for v in (x1, y1, x2 - x1, y2 - y1))
            except Exception as exc:
                raise HTTPException(400, "Box must be [x1, y1, x2, y2]") from exc
        if points:
            import json as _json

            data["points"] = _json.dumps(
                [[float(p["x"]), float(p["y"]), int(p.get("label", 1))] for p in points]
            )
        if body.get("multimask") is not None:
            data["multimask"] = str(bool(body.get("multimask"))).lower()
        if body.get("dilate_px") is not None:
            data["dilate_px"] = str(int(body.get("dilate_px")))
        files = {"image": ("image.png", img_bytes, "image/png")}
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(base + "/v1/images/mask", files=files, data=data)
        if resp.status_code == 200:
            payload = resp.json()
            masks = payload.get("masks") or []
            candidates = [m for m in masks if m.get("b64_json")]
            if not candidates:
                # Empty selection is a normal 200 outcome (never a 400): the
                # editor shows a "nothing selected" message and changes nothing.
                return {
                    "mask": None,
                    "bbox": None,
                    "masks": [],
                    "model": payload.get("model") or "sam+grounding",
                    "device": payload.get("device", ""),
                    "error": "Nothing selected for that selection.",
                }
            best = max(candidates, key=lambda m: float(m.get("score") or 0.0))
            return {
                "mask": best["b64_json"],
                "bbox": _xywh_to_xyxy(best.get("bbox")),
                "label": best.get("label"),
                "masks": masks,
                "model": payload.get("model") or "sam+grounding",
                "device": payload.get("device", ""),
            }
        raise HTTPException(
            503, f"Smart mask is temporarily unavailable (remote service returned {resp.status_code})."
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("image-op mask remote failed: %s", exc)
        raise HTTPException(503, "Smart mask is temporarily unavailable (remote service unreachable).") from exc


def _b64_to_pil_image(image_b64: str, *, mode: str = "RGBA"):
    if not image_b64:
        raise HTTPException(400, "Missing image")
    if "," in image_b64 and image_b64.split(",", 1)[0].startswith("data:"):
        image_b64 = image_b64.split(",", 1)[1]
    try:
        from PIL import Image

        raw = base64.b64decode(image_b64)
        return Image.open(io.BytesIO(raw)).convert(mode)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, "Invalid image") from exc


def _b64_bytes(value: str, *, label: str = "image") -> bytes:
    """Decode raw base64 OR a ``data:image/...;base64,`` URL to bytes.

    The image-edit tools post data URLs, while the browser posts raw base64,
    so every decode site must accept both.
    """
    if not value:
        raise HTTPException(400, f"Missing {label}")
    text = str(value)
    if "," in text and text.split(",", 1)[0].startswith("data:"):
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text)
    except Exception as exc:
        raise HTTPException(400, f"Invalid {label}") from exc


async def _json_object(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Invalid JSON body")
    if not isinstance(data, dict):
        raise HTTPException(400, "JSON object body required")
    return data


async def _image_ids(request: Request) -> list:
    ids = (await _json_object(request)).get("image_ids", [])
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise HTTPException(400, "image_ids must be a list of image ids")
    return ids


def _pil_image_to_b64(img, *, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _prepare_local_edit(src_b64, mask_b64, max_side):
    """Decode/normalize/cap an edit's inputs to PNG bytes at a model-friendly size.

    CPU-bound (a 4000x3000 decode+resize is ~0.2 s); run it via a thread off the
    event loop. Returns (source_png_bytes, mask_png_bytes, model_w, model_h, orig_size).
    """
    from PIL import Image

    img_bytes = _b64_bytes(src_b64)
    source_png = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    if mask_b64:
        mask_bytes = _b64_bytes(mask_b64, label="mask")
        mask_png = Image.open(io.BytesIO(mask_bytes)).convert("L")
    else:
        mask_png = Image.new("L", source_png.size, 255)
    orig_size = source_png.size
    if max(orig_size) > max_side:
        scale = float(max_side) / max(orig_size)
        model_w = max(16, int(round(orig_size[0] * scale / 16.0)) * 16)
        model_h = max(16, int(round(orig_size[1] * scale / 16.0)) * 16)
    else:
        model_w, model_h = orig_size
    if (model_w, model_h) != orig_size:
        source_work = source_png.resize((model_w, model_h), Image.LANCZOS)
        mask_work = mask_png.resize((model_w, model_h), Image.LANCZOS)
    else:
        source_work, mask_work = source_png, mask_png
    src_buf = io.BytesIO()
    source_work.save(src_buf, format="PNG")
    mask_buf = io.BytesIO()
    mask_work.save(mask_buf, format="PNG")
    return src_buf.getvalue(), mask_buf.getvalue(), model_w, model_h, orig_size


def _restore_local_b64(raw_b64, orig_size, model_size):
    """Resize a provider result b64 back to the original dimensions (CPU-bound)."""
    if tuple(model_size) == tuple(orig_size):
        return raw_b64
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(base64.b64decode(raw_b64))).convert("RGBA")
        im = im.resize(tuple(orig_size), Image.LANCZOS)
        ob = io.BytesIO()
        im.save(ob, format="PNG")
        return base64.b64encode(ob.getvalue()).decode("ascii")
    except Exception:
        return raw_b64


def _load_sam_backend():
    model_id = os.getenv("ODYSSEUS_SAM_MODEL", "facebook/sam-vit-base")
    cached = _SAM_STATE.get(model_id)
    if cached:
        return cached
    try:
        import torch
        from transformers import SamModel, SamProcessor
    except Exception as exc:
        raise HTTPException(
            501,
            "SAM mask tools are not installed. Install Cookbook Dependencies -> SAM mask tools.",
        ) from exc

    device = "cpu"
    try:
        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = "mps"
    except Exception:
        device = "cpu"

    try:
        processor = SamProcessor.from_pretrained(model_id)
        model = SamModel.from_pretrained(model_id)
        model.to(device)
        model.eval()
    except Exception as exc:
        raise HTTPException(500, f"Failed to load SAM model {model_id}: {exc}") from exc

    cached = {"torch": torch, "processor": processor, "model": model, "device": device, "model_id": model_id}
    _SAM_STATE[model_id] = cached
    return cached


def _load_grounding_backend():
    model_id = os.getenv("ODYSSEUS_GROUNDING_MODEL", "google/owlvit-base-patch32")
    cached = _GROUNDING_STATE.get(model_id)
    if cached:
        return cached
    try:
        import torch
        from transformers import OwlViTForObjectDetection, OwlViTProcessor
    except Exception as exc:
        raise HTTPException(
            501,
            "Object mask tools are not installed. Install Cookbook Dependencies -> SAM mask tools.",
        ) from exc

    device = "cpu"
    try:
        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = "mps"
    except Exception:
        device = "cpu"

    try:
        processor = OwlViTProcessor.from_pretrained(model_id)
        model = OwlViTForObjectDetection.from_pretrained(model_id)
        model.to(device)
        model.eval()
    except Exception as exc:
        raise HTTPException(500, f"Failed to load object mask model {model_id}: {exc}") from exc

    cached = {"torch": torch, "processor": processor, "model": model, "device": device, "model_id": model_id}
    _GROUNDING_STATE[model_id] = cached
    return cached


def _model_input_to_device(value, device: str, torch):
    if not hasattr(value, "to"):
        return value
    if (
        device == "mps"
        and hasattr(torch, "float64")
        and getattr(value, "dtype", None) == torch.float64
    ):
        return value.to(device=device, dtype=torch.float32)
    return value.to(device)


def _model_inputs_to_device(inputs, device: str, torch) -> Dict[str, Any]:
    return {
        key: _model_input_to_device(value, device, torch)
        for key, value in inputs.items()
    }


def _ground_text_to_box(image, text: str, *, threshold: float = 0.05):
    query = (text or "").strip()
    if not query:
        raise HTTPException(400, "Missing object text")
    backend = _load_grounding_backend()
    torch = backend["torch"]
    processor = backend["processor"]
    model = backend["model"]
    device = backend["device"]

    labels = [query]
    if not query.lower().startswith(("a ", "an ", "the ")):
        labels.append(f"a photo of {query}")
    try:
        inputs = processor(text=[labels], images=image, return_tensors="pt")
        model_inputs = _model_inputs_to_device(inputs, device, torch)
        with torch.no_grad():
            outputs = model(**model_inputs)
        target_sizes = torch.tensor([[image.height, image.width]])
        if hasattr(processor, "post_process_object_detection"):
            results = processor.post_process_object_detection(
                outputs=outputs,
                target_sizes=target_sizes,
                threshold=float(threshold),
            )
        elif hasattr(processor, "post_process_grounded_object_detection"):
            results = processor.post_process_grounded_object_detection(
                outputs=outputs,
                target_sizes=target_sizes,
                threshold=float(threshold),
                text_labels=[labels],
            )
        else:
            raise HTTPException(500, "Installed Transformers does not expose OWL-ViT object detection post-processing")
        boxes = results[0].get("boxes")
        scores = results[0].get("scores")
        labels_idx = results[0].get("labels")
        text_labels = results[0].get("text_labels") or results[0].get("labels_text")
        if boxes is None or scores is None or len(boxes) == 0:
            raise HTTPException(404, f"No visible object matched '{query}'")
        idx = int(torch.argmax(scores).item())
        box = [float(v) for v in boxes[idx].detach().cpu().tolist()]
        label_idx = int(labels_idx[idx].detach().cpu().item()) if labels_idx is not None and len(labels_idx) else 0
        label = labels[min(label_idx, len(labels) - 1)]
        if text_labels and len(text_labels) > idx:
            label = str(text_labels[idx])
        return {
            "box": box,
            "score": float(scores[idx].detach().cpu().item()),
            "label": label,
            "model": backend["model_id"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ground_text_to_box failed")
        raise HTTPException(500, f"Object mask failed: {exc}") from exc


def _current_user_is_admin(request: Request, user: str | None) -> bool:
    if not user:
        return False
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    is_admin = getattr(auth_mgr, "is_admin", None)
    if not callable(is_admin):
        return False
    try:
        return bool(is_admin(user))
    except Exception:
        return False


def _sanitize_gallery_filename(filename: str) -> str:
    """Return a local filename safe to join under generated_images."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(str(filename or "")).name)[:128]
    if not safe_name or safe_name in {".", ".."}:
        safe_name = uuid.uuid4().hex[:12]
    return safe_name


GALLERY_IMAGE_DIR = Path(GENERATED_IMAGES_DIR)


def _gallery_image_path(filename: str) -> Path:
    """Resolve a stored gallery filename without leaving generated_images."""
    if not isinstance(filename, str):
        raise HTTPException(400, "Unsafe gallery filename")
    safe_name = _sanitize_gallery_filename(filename)
    original = str(filename or "")
    try:
        path = Path(confine(GALLERY_IMAGE_DIR, safe_name, allow_root=False))
    except (ValueError, OSError):
        raise HTTPException(400, "Unsafe gallery filename")
    if safe_name != original:
        raise HTTPException(400, "Unsafe gallery filename")
    return path


def _normalize_image_endpoint_base(url: str) -> str:
    base = (url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def _is_openai_api_base(url: str) -> bool:
    """Return True only when url's hostname is exactly api.openai.com."""
    from urllib.parse import urlsplit
    try:
        candidate = url if "://" in url else f"https://{url}"
        return urlsplit(candidate).hostname == "api.openai.com"
    except Exception:
        return False


_GALLERY_ENDPOINT_PATHS = frozenset({
    "/images",
    "/images/edits",
    "/images/generations",
    "/images/harmonize",
    "/images/img2img",
    "/images/inpaint",
    "/images/upscale",
    "/images/variations",
    "/sdapi/v1/img2img",
})


def _join_checked_gallery_endpoint(base: str, path: str) -> str:
    """Append a known-constant gallery path suffix to a validated base URL.

    Rejects paths not in the pre-approved list so arbitrary strings can never
    be spliced into the URL passed to httpx.
    """
    if path not in _GALLERY_ENDPOINT_PATHS:
        raise ValueError(f"Unexpected gallery path: {path!r}")
    return base + path


def _visible_image_endpoint_query(db, owner: str | None):
    from src.auth_helpers import owner_filter
    q = db.query(ModelEndpoint).filter(
        ModelEndpoint.model_type == "image",
        ModelEndpoint.is_enabled == True,  # noqa: E712
    )
    return owner_filter(q, ModelEndpoint, owner)


def _first_visible_image_endpoint(db, owner: str | None):
    endpoints = _visible_image_endpoint_query(db, owner).all()
    if owner:
        for ep in endpoints:
            if getattr(ep, "owner", None) == owner:
                return ep
    if endpoints:
        return endpoints[0]
    # Fallback: the image model may live on a *mixed* endpoint (model_type !=
    # "image"), e.g. qwen-image-edit / gpt-image-1 on a shared OpenAI-compatible
    # gateway or a diffusion server registered as "chat". Chat image generation
    # already resolves its model by name across every enabled endpoint, so the
    # editor must find the same model here instead of reporting "no endpoint".
    return _first_mixed_image_endpoint(db, owner)


def _first_mixed_image_endpoint(db, owner: str | None):
    """Best-effort image endpoint when no image-only endpoint is registered.

    Returns an enabled, owner-visible endpoint whose configured model list
    includes an image-generation model, preferring the admin's configured
    ``image_model`` when it is one of them.
    """
    import json
    from src.auth_helpers import owner_filter
    from src.image_model_ids import looks_like_image_generation_model

    preferred = ""
    try:
        from src.settings import load_settings
        preferred = str((load_settings() or {}).get("image_model") or "").strip()
    except Exception:
        preferred = ""

    query = owner_filter(
        db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True),  # noqa: E712
        ModelEndpoint,
        owner,
    )
    fallback = None
    for ep in query.all():
        if getattr(ep, "model_type", None) == "image":
            continue
        models = set()
        for field in ("cached_models", "pinned_models"):
            try:
                values = getattr(ep, field, None) or []
                values = json.loads(values) if isinstance(values, str) else values
                models.update(v for v in values if isinstance(v, str))
            except (TypeError, ValueError):
                pass
        matches = {m for m in models if looks_like_image_generation_model(m)}
        if not matches:
            continue
        if preferred and preferred in matches:
            return ep
        if owner and getattr(ep, "owner", None) == owner:
            return ep
        if fallback is None:
            fallback = ep
    return fallback


def _visible_image_endpoint_for_base(db, base: str, owner: str | None, model: str = ""):
    import json
    target = _normalize_image_endpoint_base(base)
    if not target:
        return None
    fallback = None
    from src.auth_helpers import owner_filter
    from src.image_model_ids import looks_like_image_generation_model
    query = owner_filter(db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True), ModelEndpoint, owner)
    for ep in query.all():
        if getattr(ep, "model_type", None) != "image":
            # Mixed providers are eligible only for a configured image model.
            configured = set()
            for field in ("cached_models", "pinned_models"):
                try:
                    values = getattr(ep, field, None) or []
                    values = json.loads(values) if isinstance(values, str) else values
                    configured.update(v for v in values if isinstance(v, str))
                except (TypeError, ValueError):
                    pass
            if model:
                if model not in configured or not looks_like_image_generation_model(model):
                    continue
            else:
                if not any(looks_like_image_generation_model(v) for v in configured):
                    continue
        ep_id = str(getattr(ep, "id", "") or "")
        ep_base = _normalize_image_endpoint_base(getattr(ep, "base_url", ""))
        if (ep_id and (base == ep_id or target == ep_id)) or (ep_base and ep_base == target):
            if owner and getattr(ep, "owner", None) == owner:
                return ep
            if fallback is None:
                fallback = ep
    return fallback


async def _fetch_result_image_b64(url: str) -> Optional[str]:
    """Fetch an image URL returned in an upstream response body, base64-encoded
    (or None on a non-200).

    The URL comes from the diffusion/OpenAI server's response, not from our own
    config, so a malicious or compromised endpoint could otherwise steer this
    fetch at an internal or cloud-metadata address. Validate it the same way the
    client-supplied endpoint is validated before the first request.
    """
    import base64
    import httpx
    from src.url_safety import check_outbound_url

    ok, reason = check_outbound_url(
        url,
        block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
    )
    if not ok:
        raise HTTPException(502, f"Upstream returned an unsafe image URL: {reason}")
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as c2:
        ir = await c2.get(url)
        if ir.status_code == 200:
            return base64.b64encode(ir.content).decode()
    return None


def _resolve_image_op_endpoint(db, owner, requested_base: str, chosen_model: str = "") -> Tuple[str, Optional[str]]:
    """Resolve the provider base (with /v1) + api_key for a self-hosted image op.

    Mirrors inpaint_proxy: a client-supplied base must map to a registered,
    owner-visible image endpoint (no direct use of request-body URLs).
    """
    if requested_base:
        ep = _visible_image_endpoint_for_base(db, requested_base, owner, chosen_model)
        if not ep:
            raise HTTPException(403, "Choose a registered image endpoint")
    else:
        ep = _first_visible_image_endpoint(db, owner)
        if not ep:
            raise HTTPException(400, "No image generation endpoint configured. Serve a diffusion model via Cookbook first.")
    base = ep.base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base, ep.api_key


async def _forward_seneca_image_op(
    base: str,
    api_key: Optional[str],
    provider_path: str,
    image_bytes: bytes,
    fields: Dict[str, str],
    *,
    label: str,
    timeout: float = 300.0,
) -> Dict[str, Any]:
    """POST a single-image multipart op to the provider and return ``{image}``.

    The provider returns OpenAI-style ``{data:[{b64_json|url}]}`` or a raw
    ``{image}``. ``request_id`` in ``fields`` keys the provider's progress route.
    """
    import httpx

    url = base.rstrip("/") + provider_path
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    files = {"image": ("source.png", image_bytes, "image/png")}
    _t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, data=fields, files=files, headers=headers)
        _provider_ms = (time.perf_counter() - _t0) * 1000.0
    except httpx.TimeoutException:
        raise HTTPException(504, f"{label} timed out ({int(timeout)}s)")
    except Exception as exc:
        logger.warning("%s provider call failed: %s", label, exc)
        raise HTTPException(503, f"{label} is temporarily unavailable (remote service unreachable).")
    if r.status_code != 200:
        logger.warning("%s provider status %s", label, r.status_code)
        detail = f"{label} failed"
        try:
            j = r.json()
            if isinstance(j, dict) and (j.get("error") or j.get("detail")):
                detail = str(j.get("error") or j.get("detail"))[:200]
        except Exception:
            pass
        raise HTTPException(r.status_code if r.status_code in (400, 404, 422) else 502, detail)
    try:
        result = r.json()
    except ValueError as exc:
        raise HTTPException(502, "Image endpoint returned invalid JSON") from exc
    raw_b64: Optional[str] = None
    used_model = ""
    if isinstance(result, dict):
        used_model = str(result.get("model") or "")
        data = result.get("data")
        if isinstance(data, list) and data:
            item = data[0] if isinstance(data[0], dict) else {}
            raw_b64 = item.get("b64_json") or None
            if not raw_b64 and item.get("url"):
                raw_b64 = await _fetch_result_image_b64(item["url"])
        if not raw_b64 and result.get("image"):
            raw_b64 = result["image"]
    if not raw_b64:
        raise HTTPException(502, "Image endpoint returned no image")
    logger.info(
        "%s timing model_ms=%.1f total_ms=%.1f request_id=%s",
        label, _provider_ms,
        (time.perf_counter() - _t0) * 1000.0, fields.get("request_id") or "-",
    )
    payload: Dict[str, Any] = {"image": raw_b64}
    if used_model:
        payload["model"] = used_model
    return payload


def setup_gallery_routes() -> APIRouter:
    router = APIRouter(tags=["gallery"])

    # ---- POST /api/gallery/upload ----
    @router.post("/api/gallery/upload")
    async def gallery_upload(request: Request):
        """Upload an image file to the gallery with EXIF extraction and dedup."""
        import uuid
        from pathlib import Path

        form = await request.form()
        file = form.get("file")
        if not file or not hasattr(file, 'filename'):
            raise HTTPException(400, "No file provided")

        user = get_current_user(request)
        album_id = form.get("album_id") or None
        content = await read_upload_limited(file, GALLERY_UPLOAD_MAX_BYTES, "Gallery upload")

        # Duplicate detection via SHA-256
        file_hash = hashlib.sha256(content).hexdigest()
        db = SessionLocal()
        try:
            if album_id and user is not None:
                _get_or_404_album(db, album_id, user)

            # SECURITY: scope the dup-detect to THIS user — otherwise a
            # caller can probe whether someone else uploaded the same
            # file (the response leaks the existing row's id+filename).
            _dup_q = db.query(GalleryImage).filter(
                GalleryImage.file_hash == file_hash,
                GalleryImage.is_active == True,
            )
            if user:
                _dup_q = _dup_q.filter(GalleryImage.owner == user)
            existing = _dup_q.first()
            if existing:
                return {"ok": False, "duplicate": True, "filename": existing.filename,
                        "id": existing.id, "message": "Duplicate photo skipped"}

            img_dir = Path(GENERATED_IMAGES_DIR)
            img_dir.mkdir(parents=True, exist_ok=True)

            ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "png"
            VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "m4v"}
            IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
            if ext not in VIDEO_EXTS and ext not in IMAGE_EXTS:
                raise HTTPException(400, f"Unsupported file type: .{ext}")
            is_video = ext in VIDEO_EXTS
            filename = f"{uuid.uuid4().hex[:12]}.{ext}"
            img_path = img_dir / filename
            img_path.write_bytes(content)

            # Extract EXIF for images only — PIL can't parse video containers
            # and the failure path logs a noisy WARNING. We'll add ffprobe-based
            # video metadata extraction in a follow-up.
            exif = {} if is_video else _extract_exif(content)
            original_name = file.filename.rsplit(".", 1)[0] if "." in file.filename else file.filename

            img_id = str(uuid.uuid4())
            db.add(GalleryImage(
                id=img_id,
                filename=filename,
                prompt=original_name,
                model="imported",
                owner=user,
                file_hash=file_hash,
                file_size=len(content),
                width=exif.get("width"),
                height=exif.get("height"),
                taken_at=exif.get("taken_at"),
                camera_make=exif.get("camera_make"),
                camera_model=exif.get("camera_model"),
                gps_lat=exif.get("gps_lat"),
                gps_lng=exif.get("gps_lng"),
                album_id=album_id,
            ))
            db.commit()
            resp = {"ok": True, "filename": filename, "id": img_id}
            if exif.get("exif_error"):
                resp["exif_warning"] = exif["exif_error"]
            return resp
        finally:
            db.close()

    # ---- POST /api/gallery/{id}/replace ----
    @router.post("/api/gallery/{image_id}/replace")
    async def gallery_replace(request: Request, image_id: str):
        """Replace an existing gallery image file with a new one."""
        user = get_current_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not user or img.owner != user:
                raise HTTPException(403, "Not your image")

            form = await request.form()
            file = form.get("image")
            if not file or not hasattr(file, 'read'):
                raise HTTPException(400, "No image provided")

            content = await read_upload_limited(file, GALLERY_UPLOAD_MAX_BYTES, "Gallery replacement")
            GALLERY_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            img_path = _gallery_image_path(img.filename)
            img_path.write_bytes(content)

            # Refresh dimensions in case the editor resized the canvas.
            # updated_at auto-bumps via TimestampMixin's onupdate hook.
            try:
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(content)) as new_im:
                    img.width = new_im.width
                    img.height = new_im.height
            except Exception:
                pass
            try:
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("gallery_replace: DB commit failed")
                raise HTTPException(500, "Image update failed")
            return {"ok": True, "width": img.width, "height": img.height}
        finally:
            db.close()

    # ---- POST /api/gallery/{image_id}/rename ----
    @router.post("/api/gallery/{image_id}/rename")
    async def gallery_rename(request: Request, image_id: str):
        """Rename a gallery photo. Stores the new name in the `prompt`
        column (which serves as the user-facing label for uploaded
        photos that have no AI prompt)."""
        user = get_current_user(request)
        data = await request.json()
        new_name = (data.get("name") or "").strip()
        if not new_name:
            raise HTTPException(400, "Name cannot be empty")
        if len(new_name) > 500:
            raise HTTPException(400, "Name too long")
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not user or img.owner != user:
                raise HTTPException(403, "Not your image")
            img.prompt = new_name
            db.commit()
            return {"ok": True, "name": new_name}
        finally:
            db.close()

    # ---- POST /api/gallery/{image_id}/rotate ----
    @router.post("/api/gallery/{image_id}/rotate")
    async def gallery_rotate(request: Request, image_id: str):
        """Rotate an image by ±90° or 180°. Updates the file on disk and the
        width/height in the DB. Body: {angle: 90 | -90 | 180}."""
        from pathlib import Path
        from PIL import Image
        from io import BytesIO

        data = await request.json()
        try:
            angle = int(data.get("angle", 90))
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid angle")
        if angle not in (90, -90, 180, 270):
            raise HTTPException(400, "Angle must be 90, -90, 180, or 270")

        user = get_current_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not user or img.owner != user:
                raise HTTPException(403, "Not your image")

            img_path = _gallery_image_path(img.filename)
            if not img_path.exists():
                raise HTTPException(404, "Image file not found")

            # PIL rotates counter-clockwise; the API takes "clockwise"
            # convention so we negate to match user expectation.
            with Image.open(img_path) as pil:
                rotated = pil.rotate(-angle, expand=True)
                # Recompute hash so dedupe stays accurate.
                buf = BytesIO()
                ext = img.filename.rsplit(".", 1)[-1].lower()
                save_kwargs = {}
                if ext in ("jpg", "jpeg"):
                    save_kwargs["quality"] = 95
                    fmt = "JPEG"
                elif ext == "webp":
                    fmt = "WEBP"
                    save_kwargs["quality"] = 95
                else:
                    fmt = "PNG"
                rotated.save(buf, format=fmt, **save_kwargs)
                content = buf.getvalue()
                img_path.write_bytes(content)
                img.file_hash = hashlib.sha256(content).hexdigest()
                img.file_size = len(content)
                img.width, img.height = rotated.size
            db.commit()
            return {"ok": True, "width": img.width, "height": img.height}
        finally:
            db.close()

    # ---- POST /api/gallery/ai-upscale ----
    @router.post("/api/gallery/ai-upscale")
    async def gallery_ai_upscale(request: Request):
        """AI upscale using img2img with the diffusion server."""
        import base64, httpx

        user = require_privilege(request, "can_generate_images")
        form = await request.form()
        file = form.get("image")
        if not file: raise HTTPException(400, "No image")
        scale = int(form.get("scale", "2"))

        image_bytes = await read_upload_limited(file, GALLERY_TRANSFORM_UPLOAD_MAX_BYTES, "Image upload")
        b64 = base64.b64encode(image_bytes).decode()

        # Find image endpoint
        db = SessionLocal()
        try:
            ep = _first_visible_image_endpoint(db, user)
        finally:
            db.close()

        if not ep:
            raise HTTPException(400, "No image generation endpoint configured. Add one in Settings → Add Models.")

        base_url = ep.base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"

        # Use img2img endpoint if available, otherwise upscale via canvas on client
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(f"{base_url}/images/upscale", json={
                    "image": b64, "scale": scale,
                })
                if resp.status_code == 200:
                    data = resp.json()
                    return {"image": data.get("data", [{}])[0].get("b64_json", "")}
                # Fallback: no upscale endpoint — return error
                return {"error": f"Upscale endpoint not available ({resp.status_code})"}
        except Exception:
            logger.exception("ai_upscale: request failed")
            return {"error": "Upscale request failed"}

    # ---- POST /api/gallery/style-transfer ----
    @router.post("/api/gallery/style-transfer")
    async def gallery_style_transfer(request: Request):
        """Style transfer using img2img with the diffusion server."""
        import base64, httpx

        user = require_privilege(request, "can_generate_images")
        form = await request.form()
        file = form.get("image")
        prompt = form.get("prompt", "")
        strength = float(form.get("strength", "0.55"))
        if not file: raise HTTPException(400, "No image")

        image_bytes = await read_upload_limited(file, GALLERY_TRANSFORM_UPLOAD_MAX_BYTES, "Image upload")
        b64 = base64.b64encode(image_bytes).decode()

        db = SessionLocal()
        try:
            ep = _first_visible_image_endpoint(db, user)
        finally:
            db.close()

        if not ep:
            raise HTTPException(400, "No image generation endpoint configured.")

        base_url = ep.base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(f"{base_url}/images/generations", json={
                    "prompt": prompt,
                    "image": b64,
                    "strength": strength,
                    "response_format": "b64_json",
                })
                if resp.status_code == 200:
                    data = resp.json()
                    img_data = data.get("data", [{}])[0].get("b64_json", "")
                    if img_data:
                        return {"image": img_data}
                return {"error": f"Style transfer failed ({resp.status_code})"}
        except Exception:
            logger.exception("style_transfer: request failed")
            return {"error": "Style transfer failed"}

    # ---- GET /api/gallery/tags ----
    @router.get("/api/gallery/tags")
    async def gallery_tags(request: Request) -> Dict[str, Any]:
        """Return distinct tags across all active gallery images."""
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryImage.tags).filter(
                GalleryImage.is_active == True, GalleryImage.tags != None, GalleryImage.tags != ""
            )
            q = _owner_filter(q, user)
            rows = q.all()
            tag_set = set()
            for (raw,) in rows:
                for t in raw.split(","):
                    t = t.strip()
                    if t:
                        tag_set.add(t)
            return {"tags": sorted(tag_set)}
        finally:
            db.close()

    # ---- GET /api/gallery/library ----
    @router.get("/api/gallery/library")
    async def gallery_library(
        request: Request,
        search: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        model: Optional[str] = Query(None),
        album: Optional[str] = Query(None),
        favorites: bool = Query(False),
        sort: str = Query("recent"),
        seed: Optional[int] = Query(None),
        offset: int = Query(0, ge=0),
        limit: int = Query(24, ge=1, le=100),
    ) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            # Distinct tags for filter UI
            tag_q = db.query(GalleryImage.tags).filter(
                GalleryImage.is_active == True, GalleryImage.tags != None, GalleryImage.tags != ""
            )
            tag_q = _owner_filter(tag_q, user)
            tag_rows = tag_q.all()
            all_tags = set()
            for (raw,) in tag_rows:
                for t in raw.split(","):
                    t = t.strip()
                    if t:
                        all_tags.add(t)

            # Distinct models for filter UI
            model_q = db.query(GalleryImage.model).filter(
                GalleryImage.is_active == True, GalleryImage.model != None
            )
            model_q = _owner_filter(model_q, user)
            model_rows = model_q.distinct().all()
            all_models = sorted([m for (m,) in model_rows if m])

            # Base query with left join to sessions for session_name
            q = (
                db.query(GalleryImage, DbSession.name)
                .outerjoin(DbSession, GalleryImage.session_id == DbSession.id)
                .filter(GalleryImage.is_active == True)
            )
            q = _owner_filter(q, user)

            # Per-term AND search: a single `%foo bar%` LIKE only matched the
            # exact phrase, so "red car" missed "a car that is red".
            # LIKE wildcards are escaped per token.
            if search:
                from sqlalchemy import or_
                for tok in search.split():
                    term = f"%{escape_like_pattern(tok)}%"
                    q = q.filter(or_(
                        GalleryImage.prompt.ilike(term, escape="\\"),
                        GalleryImage.tags.ilike(term, escape="\\"),
                        GalleryImage.ai_tags.ilike(term, escape="\\"),
                    ))

            # Tag filter. The UI stacks multiple tag pills by passing them
            # comma-separated — each tag adds a separate AND-filter so the
            # result set narrows as the user piles tags on. A single tag
            # (no commas) is the original behaviour.
            if tag:
                from sqlalchemy import or_ as _or
                for one in (t.strip() for t in tag.split(",")):
                    if not one:
                        continue
                    safe_tag = f"%{escape_like_pattern(one)}%"
                    q = q.filter(_or(
                        GalleryImage.tags.ilike(safe_tag, escape="\\"),
                        GalleryImage.ai_tags.ilike(safe_tag, escape="\\"),
                    ))

            # Model filter
            if model:
                q = q.filter(GalleryImage.model == model)

            # Album filter
            if album:
                q = q.filter(GalleryImage.album_id == album)

            # Favorites filter
            if favorites:
                q = q.filter(GalleryImage.favorite == True)

            # Total before pagination
            total = q.count()
            # How many of those have AI tags — surfaced as "X/Y photos tagged"
            # in the AI-tagging settings header.
            total_tagged = q.filter(
                GalleryImage.ai_tags.isnot(None), GalleryImage.ai_tags != ""
            ).count()

            # Sorting
            if sort == "shuffle":
                # Seeded shuffle: fetch all matching IDs, shuffle them
                # deterministically with `seed`, then re-query for just the
                # page we want. Stable across pagination as long as the
                # client keeps the same seed.
                import random as _random
                id_rows = q.with_entities(GalleryImage.id).all()
                all_ids = [r[0] for r in id_rows]
                rng = _random.Random(seed if seed is not None else 0)
                rng.shuffle(all_ids)
                page_ids = all_ids[offset:offset + limit]
                if page_ids:
                    page_rows = (
                        db.query(GalleryImage, DbSession.name)
                        .outerjoin(DbSession, GalleryImage.session_id == DbSession.id)
                        .filter(GalleryImage.id.in_(page_ids))
                        .all()
                    )
                    # Restore the shuffled order
                    by_id = {img.id: (img, session_name) for img, session_name in page_rows}
                    rows = [by_id[i] for i in page_ids if i in by_id]
                else:
                    rows = []
            else:
                if sort == "oldest":
                    q = q.order_by(GalleryImage.created_at.asc())
                else:  # recent
                    q = q.order_by(GalleryImage.created_at.desc())
                rows = q.offset(offset).limit(limit).all()

            items = []
            for img, session_name in rows:
                items.append(_image_to_dict(img, session_name))

            return {
                "items": items,
                "total": total,
                "total_tagged": total_tagged,
                "tags": sorted(all_tags),
                "models": all_models,
            }
        except Exception:
            logger.exception("Failed to fetch gallery library")
            raise HTTPException(500, "Failed to fetch gallery library")
        finally:
            db.close()

    # ---- Album CRUD (must be before {image_id} catch-all) ----

    @router.get("/api/gallery/albums")
    async def list_albums(request: Request):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryAlbum)
            q = _owner_filter(q, user, GalleryAlbum)
            albums = q.order_by(GalleryAlbum.created_at.desc()).all()
            result = []
            for a in albums:
                _count_q = db.query(GalleryImage).filter(
                    GalleryImage.album_id == a.id, GalleryImage.is_active == True
                )
                _count_q = _owner_filter(_count_q, user)
                count = _count_q.count()
                cover_url = None
                if a.cover_id:
                    cover_q = db.query(GalleryImage).filter(GalleryImage.id == a.cover_id)
                    cover = _owner_filter(cover_q, user).first()
                    if cover:
                        cover_url = f"/api/generated-image/{cover.filename}"
                elif count > 0:
                    _cover_q = db.query(GalleryImage).filter(
                        GalleryImage.album_id == a.id, GalleryImage.is_active == True
                    )
                    _cover_q = _owner_filter(_cover_q, user)
                    first = _cover_q.order_by(GalleryImage.created_at.desc()).first()
                    if first:
                        cover_url = f"/api/generated-image/{first.filename}"
                result.append({
                    "id": a.id, "name": a.name, "description": a.description or "",
                    "cover_url": cover_url, "count": count,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                })
            return {"albums": result}
        finally:
            db.close()

    @router.post("/api/gallery/albums")
    async def create_album(request: Request):
        import uuid
        user = get_current_user(request)
        data = await request.json()
        name = (data.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Album name required")
        db = SessionLocal()
        try:
            a = GalleryAlbum(
                id=str(uuid.uuid4()), name=name,
                description=data.get("description", ""),
                owner=user,
            )
            db.add(a)
            db.commit()
            return {"ok": True, "id": a.id, "name": a.name}
        finally:
            db.close()

    @router.get("/api/gallery/stats")
    async def gallery_stats(request: Request):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            from sqlalchemy import func
            base = db.query(GalleryImage).filter(GalleryImage.is_active == True)
            size_q = db.query(func.sum(GalleryImage.file_size)).filter(GalleryImage.is_active == True)
            album_q = db.query(GalleryAlbum)
            base = _owner_filter(base, user)
            size_q = _owner_filter(size_q, user)
            album_q = _owner_filter(album_q, user, GalleryAlbum)
            total = base.count()
            total_size = size_q.scalar() or 0
            fav_count = base.filter(GalleryImage.favorite == True).count()
            album_count = album_q.count()
            return {
                "total_photos": total,
                "total_size": total_size,
                "total_size_human": _human_size(total_size),
                "favorites": fav_count,
                "albums": album_count,
            }
        finally:
            db.close()

    @router.post("/api/gallery/ai-tag-batch")
    async def ai_tag_batch(
        request: Request,
        album_id: Optional[str] = Query(None),
        limit: int = Query(200),
    ):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(
                GalleryImage.is_active == True,
                (GalleryImage.ai_tags == None) | (GalleryImage.ai_tags == ""),
            )
            q = _owner_filter(q, user)
            if album_id:
                q = q.filter(GalleryImage.album_id == album_id)
            untagged = q.count()
            ids = [img.id for img in q.limit(max(1, min(limit, 500))).all()]
            return {"ok": True, "queued": len(ids), "total_untagged": untagged, "image_ids": ids}
        finally:
            db.close()

    # ---- GET /api/gallery/{image_id} ----
    @router.get("/api/gallery/{image_id}")
    async def get_gallery_image(request: Request, image_id: str) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            row = (
                db.query(GalleryImage, DbSession.name)
                .outerjoin(DbSession, GalleryImage.session_id == DbSession.id)
                .filter(GalleryImage.id == image_id)
                .first()
            )
            if not row:
                raise HTTPException(404, "Image not found")
            img, session_name = row
            if not user or img.owner != user:
                raise HTTPException(404, "Image not found")
            return _image_to_dict(img, session_name)
        finally:
            db.close()

    # ---- PATCH /api/gallery/{image_id} ----
    @router.patch("/api/gallery/{image_id}")
    async def patch_gallery_image(request: Request, image_id: str, req: GalleryPatch) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not user or img.owner != user:
                raise HTTPException(404, "Image not found")
            if req.tags is not None:
                # Drop any tag from the user-tags field that already lives in
                # ai_tags — earlier flows wrote AI suggestions to both fields
                # and the UI showed every photo with the same chips twice.
                ai_set = {t.strip().lower() for t in (img.ai_tags or '').split(',') if t.strip()}
                cleaned = []
                seen = set()
                for raw in (req.tags or '').split(','):
                    t = raw.strip()
                    k = t.lower()
                    if not t or k in seen or k in ai_set:
                        continue
                    seen.add(k)
                    cleaned.append(t)
                img.tags = ', '.join(cleaned)
            if req.favorite is not None:
                img.favorite = req.favorite
            if req.album_id is not None:
                if req.album_id:
                    # Validate the target album belongs to the caller before
                    # moving the image into it — mirrors add_to_album, so you
                    # cannot file your image into another user's album.
                    _get_or_404_album(db, req.album_id, user)
                    img.album_id = req.album_id
                else:
                    img.album_id = None
            db.commit()
            db.refresh(img)
            return _image_to_dict(img)
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            logger.exception("patch_gallery_image: update failed")
            raise HTTPException(500, "Image update failed")
        finally:
            db.close()

    # ---- POST /api/gallery/download-zip ----
    # Bundle the given image ids into a single .zip for download. Used by the
    # gallery's bulk "Download" when many photos are selected (one file instead
    # of a flood of individual downloads).
    @router.post("/api/gallery/download-zip")
    async def gallery_download_zip(request: Request):
        user = get_current_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        try:
            data = await request.json()
        except Exception:
            data = {}
        ids = data.get("ids") or []
        if not ids:
            raise HTTPException(400, "No images specified")
        db = SessionLocal()
        try:
            imgs = db.query(GalleryImage).filter(
                GalleryImage.id.in_(ids),
                GalleryImage.owner == user,
            ).all()
            if not imgs:
                raise HTTPException(404, "No images found")
            import io
            import re
            import zipfile
            buf = io.BytesIO()
            used = set()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for img in imgs:
                    src = _gallery_image_path(img.filename)
                    if not src.exists():
                        continue
                    ext = src.suffix or ".png"
                    base = (img.prompt or "").strip() or src.stem
                    base = re.sub(r"[^\w\-. ]+", "", base)[:60].strip() or img.id
                    name = f"{base}{ext}"
                    i = 1
                    while name in used:
                        name = f"{base}-{i}{ext}"
                        i += 1
                    used.add(name)
                    zf.write(src, arcname=name)
            if not used:
                raise HTTPException(404, "No image files found on disk")
            from fastapi import Response
            return Response(
                content=buf.getvalue(),
                media_type="application/zip",
                headers={"Content-Disposition": 'attachment; filename="gallery-photos.zip"'},
            )
        finally:
            db.close()

    # ---- POST /api/gallery/clear-user-tags ----
    # Wipe the `tags` field on every image owned by the current user.
    # Leaves `ai_tags` intact. Use after a bug populated user-tags with
    # AI-suggested values you never added.
    @router.post("/api/gallery/clear-user-tags")
    async def clear_gallery_user_tags(request: Request) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(GalleryImage.is_active == True)
            q = _owner_filter(q, user)
            cleared = 0
            for img in q.all():
                if img.tags:
                    img.tags = ''
                    cleared += 1
            db.commit()
            return {"ok": True, "cleared": cleared}
        except Exception:
            db.rollback()
            logger.exception("clear_gallery_user_tags: failed")
            raise HTTPException(500, "Tag update failed")
        finally:
            db.close()

    # ---- POST /api/gallery/clear-ai-tags ----
    # Wipe the `ai_tags` field on every image owned by the current user.
    # Leaves user `tags` intact. Use when AI-suggested tags like "dog" /
    # "woman" have leaked into the gallery and you want them gone.
    @router.post("/api/gallery/clear-ai-tags")
    async def clear_gallery_ai_tags(request: Request, image_id: Optional[str] = Query(None)) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(GalleryImage.is_active == True)
            q = _owner_filter(q, user)
            if image_id:  # clear just one photo's AI tags
                q = q.filter(GalleryImage.id == image_id)
            cleared = 0
            for img in q.all():
                if img.ai_tags:
                    img.ai_tags = ''
                    cleared += 1
            db.commit()
            return {"ok": True, "cleared": cleared}
        except Exception:
            db.rollback()
            logger.exception("clear_gallery_ai_tags: failed")
            raise HTTPException(500, "Tag update failed")
        finally:
            db.close()

    # ---- POST /api/gallery/dedupe-tags ----
    # One-shot cleanup: for every image owned by the current user, drop any
    # tag from `tags` that also appears in `ai_tags` (case-insensitive).
    # Returns how many rows were touched + how many tags removed.
    @router.post("/api/gallery/dedupe-tags")
    async def dedupe_gallery_tags(request: Request) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(GalleryImage.is_active == True)
            q = _owner_filter(q, user)
            rows_touched = 0
            tags_removed = 0
            for img in q.all():
                ai_set = {t.strip().lower() for t in (img.ai_tags or '').split(',') if t.strip()}
                if not ai_set:
                    continue
                original = [t.strip() for t in (img.tags or '').split(',') if t.strip()]
                cleaned = []
                seen = set()
                for t in original:
                    k = t.lower()
                    if k in ai_set or k in seen:
                        continue
                    seen.add(k)
                    cleaned.append(t)
                if len(cleaned) != len(original):
                    rows_touched += 1
                    tags_removed += len(original) - len(cleaned)
                    img.tags = ', '.join(cleaned)
            db.commit()
            return {"ok": True, "rows_touched": rows_touched, "tags_removed": tags_removed}
        except Exception:
            db.rollback()
            logger.exception("dedupe_gallery_tags: failed")
            raise HTTPException(500, "Tag deduplication failed")
        finally:
            db.close()

    # ---- DELETE /api/gallery/{image_id} ----
    @router.delete("/api/gallery/{image_id}")
    async def delete_gallery_image(request: Request, image_id: str) -> Dict[str, str]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img:
                raise HTTPException(404, "Image not found")
            if not user or img.owner != user:
                raise HTTPException(404, "Image not found")

            img_filename = img.filename
            # Soft-delete the record first; the DB is the source of truth.
            img.is_active = False
            db.commit()

            # Only after the soft-delete commit succeeds do we remove the file.
            # If the file were deleted first and the commit then failed/rolled
            # back, the still-active record would point at a missing file.
            # Best-effort so a missing or locked file can't 500 a delete that
            # already succeeded logically. Uses the path-confined resolver so a
            # malformed stored filename can't escape generated_images.
            try:
                img_path = _gallery_image_path(img_filename)
                if img_path.exists():
                    img_path.unlink()
            except Exception as e:
                logger.warning(f"Could not remove gallery image file for {img_filename}: {e}")

            # Strip stale chat-history references so the image bubble
            # (and its prompt caption) doesn't come back after a server
            # reboot replays the session. We remove the matching tool
            # event entirely; if that leaves the message with no other
            # tool events AND a "Generated image for: …" body, drop the
            # whole row so there's no remnant.
            try:
                from core.database import ChatMessage as _ChatMessage
                from sqlalchemy import or_ as _or
                import json as _json
                # Match by image_id OR by filename — older messages
                # (saved before we threaded image_id through the SSE)
                # only carry image_url containing the filename.
                msgs = db.query(_ChatMessage).filter(
                    _ChatMessage.meta_data.isnot(None),
                    _or(
                        _ChatMessage.meta_data.like(f"%{image_id}%"),
                        _ChatMessage.meta_data.like(f"%{img_filename}%"),
                    ),
                ).all()
                rows_to_delete = []
                for m in msgs:
                    if not m.meta_data:
                        continue
                    try:
                        meta = _json.loads(m.meta_data)
                    except Exception:
                        continue
                    events = meta.get("tool_events") or []
                    new_events = []
                    removed_any = False
                    for ev in events:
                        if not isinstance(ev, dict):
                            new_events.append(ev)
                            continue
                        is_match = ev.get("image_id") == image_id or (
                            ev.get("image_url") and img_filename in ev["image_url"]
                        )
                        if is_match:
                            removed_any = True
                            continue
                        new_events.append(ev)
                    if not removed_any:
                        continue
                    # If the message has no other tool events left, drop
                    # it AND the immediately preceding user prompt that
                    # asked for the image, so no remnant of the exchange
                    # survives.
                    if not new_events:
                        rows_to_delete.append(m)
                        prev = (
                            db.query(_ChatMessage)
                            .filter(
                                _ChatMessage.session_id == m.session_id,
                                _ChatMessage.timestamp < m.timestamp,
                            )
                            .order_by(_ChatMessage.timestamp.desc())
                            .first()
                        )
                        if prev and prev.role == "user":
                            prev_meta = {}
                            try:
                                prev_meta = _json.loads(prev.meta_data) if prev.meta_data else {}
                            except Exception:
                                prev_meta = {}
                            # Only purge the prompt if it has no tool
                            # events of its own (i.e. it's a pure user
                            # message, not an agent step).
                            if not (prev_meta.get("tool_events") or []):
                                rows_to_delete.append(prev)
                    else:
                        meta["tool_events"] = new_events
                        m.meta_data = _json.dumps(meta)
                for m in rows_to_delete:
                    db.delete(m)
                if msgs:
                    db.commit()
            except Exception as _e:
                # Cleanup is best-effort — never block the delete itself.
                logger.warning(f"chat-history cleanup after image delete failed: {_e}")

            return {"status": "deleted", "id": image_id}
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            logger.exception("delete_gallery_image: failed")
            raise HTTPException(500, "Image deletion failed")
        finally:
            db.close()

    # ---- POST /api/image/inpaint — proxy to diffusion server OR OpenAI ----
    @router.post("/api/image/inpaint")
    async def inpaint_proxy(request: Request):
        """Forward inpaint request. If the selected endpoint is OpenAI, re-shape
        the request for /v1/images/edits (multipart, inverted mask). Otherwise
        proxy through to a self-hosted diffusion server's /v1/images/inpaint."""
        import httpx
        user = require_privilege(request, "can_generate_images")
        body = await request.json()
        # Use endpoint from request body (editor dropdown) or fall back to DB lookup.
        # Store as requested_base to avoid carrying user input into the outbound request.
        requested_base = (body.pop("_endpoint", "") or "").rstrip("/")
        # SSRF hardening: validate a client-supplied endpoint before any
        # outbound request (mirrors routes/embedding_routes.py).
        if requested_base and "://" in requested_base:
            from src.url_safety import check_outbound_url
            ok, reason = check_outbound_url(
                requested_base,
                block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
            )
            if not ok:
                raise HTTPException(400, f"Rejected endpoint URL: {reason}")
        chosen_model = (body.pop("_model", "") or "").strip()
        # Optional client-supplied id so GET /api/image/edit-progress can poll the
        # provider's step progress for this op. Ignored by providers that do not
        # implement the progress contract.
        progress_request_id = (body.get("request_id") or "").strip()
        api_key = None
        if not requested_base:
            db = SessionLocal()
            try:
                ep = _first_visible_image_endpoint(db, user)
                if not ep:
                    raise HTTPException(400, "No image generation endpoint configured. Serve a diffusion model via Cookbook first.")
                base = ep.base_url.rstrip("/")
                api_key = ep.api_key
            finally:
                db.close()
        else:
            # Resolve the client-supplied base to a registered visible endpoint.
            # Admins are not exempted — gallery proxy routes must use a DB row
            # so the outbound URL never depends directly on request-body input.
            db = SessionLocal()
            try:
                ep = _visible_image_endpoint_for_base(db, requested_base, user, chosen_model)
                if not ep:
                    raise HTTPException(403, "Choose a registered image endpoint")
                base = ep.base_url.rstrip("/")
                api_key = ep.api_key
            finally:
                db.close()

        if not base.endswith("/v1"):
            base += "/v1"

        is_openai = _is_openai_api_base(base)
        from src.model_capability_readers.base import detect_vendor
        is_openrouter = detect_vendor(base) == "openrouter"

        if is_openai or is_openrouter:
            # OpenAI path: /v1/images/edits with gpt-image-1.
            # Mask convention differs from Stable Diffusion:
            #   SD:     white pixels = regenerate, black = keep
            #   OpenAI: transparent alpha = regenerate, opaque = keep
            # So we convert the incoming PNG mask into an alpha-channel PNG.
            if not api_key:
                raise HTTPException(400, "OpenAI endpoint has no api_key stored — edit it in Endpoints settings.")
            import base64, io
            try:
                from PIL import Image
            except ImportError:
                raise HTTPException(500, "Pillow not installed on server")

            try:
                img_bytes = _b64_bytes(body.get("image"))
                if not body.get("mask"):
                    raise HTTPException(400, "A mask is required for OpenAI-compatible image edits")
                mask_bytes = _b64_bytes(body.get("mask"), label="mask")
                source_png = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                mask_png = Image.open(io.BytesIO(mask_bytes)).convert("L")  # luminance
                # Build OpenAI mask: RGBA where alpha=255 means keep, 0 means regenerate.
                # SD mask: white (255) = regenerate → alpha 0.  Black (0) = keep → alpha 255.
                # RGB must be white for keep areas; start from fully-white opaque and
                # overwrite alpha so visual contents match the expected semantic.
                alpha = mask_png.point(lambda p: 255 - p)
                oa_mask = Image.new("RGBA", source_png.size, (255, 255, 255, 255))
                oa_mask.putalpha(alpha)

                src_buf = io.BytesIO()
                source_png.save(src_buf, format="PNG")
                src_buf.seek(0)
                mask_buf = io.BytesIO()
                oa_mask.save(mask_buf, format="PNG")
                mask_buf.seek(0)
            except HTTPException:
                raise
            except Exception:
                logger.exception("inpaint_proxy: failed to prepare OpenAI request")
                raise HTTPException(400, "Failed to prepare inpaint request")

            width = int(body.get("width") or 1024)
            height = int(body.get("height") or 1024)
            # gpt-image-1 only accepts 1024x1024, 1024x1536, 1536x1024 (no 'auto'
            # for edits). Pick the closest to preserve aspect, default square.
            if width > height * 1.15:
                size = "1536x1024"
            elif height > width * 1.15:
                size = "1024x1536"
            else:
                size = "1024x1024"

            files = {
                "image": ("source.png", src_buf.getvalue(), "image/png"),
                "mask": ("mask.png", mask_buf.getvalue(), "image/png"),
            }
            # Honor explicit model selection from the editor; fall back to gpt-image-1.
            # dall-e-3 has no edit endpoint — refuse it loudly so the user picks again.
            oa_model = chosen_model or "gpt-image-1"
            if "dall-e-3" in oa_model:
                raise HTTPException(400, "dall-e-3 doesn't support image edits — pick gpt-image-1 or dall-e-2")
            data = {
                "model": oa_model,
                "prompt": body.get("prompt", ""),
                "size": size,
                "n": "1",
            }
            headers = {"Authorization": f"Bearer {api_key}"}
            try:
                logger.debug(
                    "inpaint_proxy provider edit -> model=%r size=%r base=%s",
                    oa_model, size, base,
                )
                async with httpx.AsyncClient(timeout=300) as client:
                    if is_openrouter:
                        # Reference-based editing, then composite locally so
                        # pixels outside the user's mask remain unchanged.
                        reference_mask = io.BytesIO()
                        mask_png.save(reference_mask, format="PNG")
                        references = [src_buf.getvalue(), reference_mask.getvalue()]
                        payload = {
                            "model": oa_model, "size": size, "n": 1,
                            "output_format": "png",
                            "prompt": "Edit the first image. The second image is a mask: white marks the region to edit, black marks the region to preserve. Keep composition and framing unchanged. Requested edit: " + str(body.get("prompt", "")),
                            "input_references": [{"type": "image_url", "image_url": {
                                "url": "data:image/png;base64," + base64.b64encode(value).decode(),
                            }} for value in references],
                        }
                        r = await client.post(_join_checked_gallery_endpoint(base, "/images"), headers=headers, json=payload)
                    else:
                        r = await client.post(_join_checked_gallery_endpoint(base, "/images/edits"), headers=headers, data=data, files=files)
                    if r.status_code != 200:
                        logger.error("inpaint_proxy OpenAI edit: status %s", r.status_code)
                        _detail = "OpenAI edit failed"
                        try:
                            _j = r.json()
                            if isinstance(_j, dict):
                                _detail = _j.get("error") or _j.get("detail") or _detail
                                if isinstance(_detail, dict):
                                    _detail = _detail.get("message") or _detail
                        except Exception:
                            pass
                        raise HTTPException(r.status_code, f"{_detail} (provider HTTP {r.status_code})")
                    result = r.json()
                    raw_b64 = None
                    if result.get("data"):
                        item = result["data"][0]
                        # gpt-image-1 returns b64_json by default; dall-e-2 may return url
                        if item.get("b64_json"):
                            raw_b64 = item["b64_json"]
                        elif item.get("url"):
                            raw_b64 = await _fetch_result_image_b64(item["url"])
                    if not raw_b64:
                        raise HTTPException(502, "OpenAI returned no image")

                    # OpenAI's edits API doesn't truly preserve unmasked
                    # pixels — gpt-image-1 regenerates the whole image,
                    # so even areas the user didn't mask come back
                    # slightly different. Composite the model output onto
                    # the ORIGINAL source using the user's mask, so only
                    # the masked region actually changes.
                    try:
                        generated = Image.open(io.BytesIO(base64.b64decode(raw_b64))).convert("RGBA")
                        # Match the generated image to the source dims.
                        if generated.size != source_png.size:
                            generated = generated.resize(source_png.size, Image.LANCZOS)
                        # mask_png: white = regenerate (use generated),
                        #           black = keep (use source).
                        # Composite: result = source * (1 - mask_norm) + generated * mask_norm
                        # Image.composite does exactly that with `mask`.
                        blended = Image.composite(generated, source_png, mask_png)
                        out_buf = io.BytesIO()
                        blended.save(out_buf, format="PNG")
                        return {"image": base64.b64encode(out_buf.getvalue()).decode(), "model": oa_model}
                    except Exception as comp_err:
                        # Never return a full-image edit when masking fails.
                        logger.warning(f"Inpaint compose failed: {comp_err}")
                        raise HTTPException(502, "Could not apply the edit within the selected region")
            except httpx.TimeoutException:
                raise HTTPException(504, "OpenAI inpaint timed out (300s)")
            except httpx.TransportError as exc:
                logger.warning("inpaint_proxy: OpenAI endpoint unreachable: %s", exc)
                raise HTTPException(502, f"Image server unreachable: {type(exc).__name__}")

        # Self-hosted diffusion server path. Newer Odysseus image
        # wrappers expose the OpenAI-compatible /v1/images/edits
        # multipart route even when they are local/self-hosted. Older
        # diffusion_server.py exposes /v1/images/inpaint as JSON. Try the
        # OpenAI-compatible local route first, then fall back.
        try:
            # Forward chosen_model so the diffusion server can route if it ever
            # supports multiple models per process. Harmless if ignored.
            if chosen_model:
                body["model"] = chosen_model
            async with httpx.AsyncClient(timeout=300) as client:
                _t0 = time.perf_counter()
                _restore_ms = [0.0]
                try:
                    import base64, io
                    from PIL import Image

                    # Decode/resize/encode off the event loop: a 4000x3000 input
                    # costs ~0.2 s of CPU that would otherwise block every other
                    # request (and stack under concurrent edits).
                    _max_side = int(os.getenv("ODYSSEUS_IMAGE_EDIT_MAX_SIDE", "768") or 768)
                    _src_png, _mask_png, _model_w, _model_h, _orig_size = await asyncio.to_thread(
                        _prepare_local_edit, body.get("image"), body.get("mask"), _max_side
                    )
                    files = {
                        "image": ("source.png", _src_png, "image/png"),
                        "mask": ("mask.png", _mask_png, "image/png"),
                    }
                    data = {
                        "model": chosen_model or body.get("model") or "",
                        "prompt": body.get("prompt", ""),
                        "size": f"{_model_w}x{_model_h}",
                        "n": "1",
                    }
                    if progress_request_id:
                        data["request_id"] = progress_request_id
                    # Let the caller's step budget through up to a cap (quality
                    # edits such as removal/background-replacement/expand pass 8
                    # on purpose); only fall back to a moderate default when no
                    # count is requested. The size cap above is what keeps the
                    # op responsive.
                    _max_steps = int(os.getenv("ODYSSEUS_IMAGE_EDIT_MAX_STEPS", "8") or 8)
                    if body.get("steps"):
                        try:
                            data["steps"] = str(min(max(1, int(body["steps"])), _max_steps))
                        except (TypeError, ValueError):
                            data["steps"] = "4"
                    else:
                        data["steps"] = "4"

                    async def _restore_b64(_raw: str) -> str:
                        """Resize a provider PNG b64 back to the original size (off-loop)."""
                        if (_model_w, _model_h) == _orig_size:
                            return _raw
                        _rs = time.perf_counter()
                        try:
                            return await asyncio.to_thread(
                                _restore_local_b64, _raw, _orig_size, (_model_w, _model_h)
                            )
                        finally:
                            _restore_ms[0] += (time.perf_counter() - _rs) * 1000.0
                    logger.debug(
                        "inpaint_proxy self-hosted /images/edits -> model=%r "
                        "size=%r steps=%r endpoint=%s",
                        data.get("model"), data.get("size"),
                        data.get("steps"), base,
                    )
                    _prep_ms = (time.perf_counter() - _t0) * 1000.0

                    def _finish(_payload: Dict[str, Any]) -> Dict[str, Any]:
                        # model_ms is the provider round-trip (its own queue +
                        # diffusion time); prep_ms is our decode/resize; restore_ms
                        # is resizing the result back. total_ms is the whole op.
                        logger.info(
                            "inpaint-proxy timing prep_ms=%.1f model_ms=%.1f restore_ms=%.1f "
                            "total_ms=%.1f size=%s steps=%s model=%s request_id=%s",
                            _prep_ms, _provider_ms[0], _restore_ms[0],
                            (time.perf_counter() - _t0) * 1000.0,
                            data.get("size"), data.get("steps"), data.get("model"),
                            progress_request_id or "-",
                        )
                        return _payload

                    _t_post = time.perf_counter()
                    r = await client.post(_join_checked_gallery_endpoint(base, "/images/edits"), data=data, files=files)
                    _provider_ms = [(time.perf_counter() - _t_post) * 1000.0]
                    if r.status_code == 200:
                        result = r.json()
                        _used_model = result.get("model") if isinstance(result, dict) else None
                        _used_model = _used_model or data.get("model") or ""
                        if isinstance(result, dict) and result.get("data"):
                            item = result["data"][0]
                            if item.get("b64_json"):
                                return _finish({"image": await _restore_b64(item["b64_json"]), "model": _used_model})
                            if item.get("url"):
                                raw_b64 = await _fetch_result_image_b64(item["url"])
                                if raw_b64:
                                    return _finish({"image": await _restore_b64(raw_b64), "model": _used_model})
                        if isinstance(result, dict) and result.get("image"):
                            return _finish({"image": await _restore_b64(result["image"]), "model": _used_model})
                        raise HTTPException(502, "Image edit endpoint returned no image")
                    if r.status_code not in (404, 405):
                        logger.warning("inpaint_proxy self-hosted edits: status %s", r.status_code)
                        detail = "Image edit request failed"
                        try:
                            err = r.json()
                            detail = err.get("detail") or err.get("error") or detail
                        except Exception:
                            pass
                        # A plain SD/SDXL checkpoint often exposes
                        # generation only at /images/edits.
                        # That does not mean the endpoint cannot inpaint:
                        # Odysseus diffusion_server.py has a dedicated
                        # /images/inpaint route that can derive/fallback to
                        # inpaint, img2img crop+composite, or txt2img
                        # crop+composite. Fall through to that route instead
                        # of surfacing "does not support image edits".
                        if r.status_code == 400 and "does not support image edits" in str(detail).lower():
                            logger.info("inpaint_proxy self-hosted edits unsupported; falling back to /images/inpaint")
                        else:
                            raise HTTPException(r.status_code, detail)
                except HTTPException:
                    raise
                except Exception:
                    logger.exception("inpaint_proxy: failed to prepare self-hosted edit request")
                    raise HTTPException(400, "Failed to prepare inpaint request")

                r = await client.post(_join_checked_gallery_endpoint(base, "/images/inpaint"), json=body)
                if r.status_code != 200:
                    logger.error("inpaint_proxy diffusion: status %s", r.status_code)
                    detail = "Inpaint request failed"
                    try:
                        j = r.json()
                        if isinstance(j, dict):
                            detail = j.get("detail") or j.get("error") or detail
                    except Exception:
                        pass
                    raise HTTPException(r.status_code, f"{detail} (provider HTTP {r.status_code})")
                return r.json()
        except httpx.TimeoutException:
            raise HTTPException(504, "Inpaint request timed out (240s)")
        except httpx.TransportError as exc:
            logger.warning("inpaint_proxy: image endpoint unreachable: %s", exc)
            raise HTTPException(502, f"Image server unreachable: {type(exc).__name__}")
        except HTTPException:
            raise
        except Exception:
            logger.exception("inpaint_proxy: request failed")
            raise HTTPException(502, "Inpaint request failed")

    async def _resolve_and_decode(body: Dict[str, Any], user):
        """Shared prep for the seneca single-image ops: validate endpoint + decode.

        Returns (base, api_key, image_bytes, chosen_model). The base64 decode
        runs off the event loop (large images).
        """
        requested_base = (body.pop("_endpoint", "") or "").rstrip("/")
        if requested_base and "://" in requested_base:
            from src.url_safety import check_outbound_url
            ok, reason = check_outbound_url(
                requested_base,
                block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
            )
            if not ok:
                raise HTTPException(400, f"Rejected endpoint URL: {reason}")
        chosen_model = (body.pop("_model", "") or "").strip()
        db = SessionLocal()
        try:
            base, api_key = _resolve_image_op_endpoint(db, user, requested_base, chosen_model)
        finally:
            db.close()
        image_bytes = await asyncio.to_thread(_b64_bytes, body.get("image"))
        return base, api_key, image_bytes, chosen_model

    @router.post("/api/image/replace-background")
    async def replace_background(request: Request):
        """Proxy seneca's subject-mask background replacement (~/v1/images/replace-background)."""
        user = require_privilege(request, "can_generate_images")
        body = await request.json()
        base, api_key, image_bytes, chosen_model = await _resolve_and_decode(body, user)
        fields: Dict[str, str] = {
            "prompt": str(body.get("prompt") or ""),
            "steps": str(body.get("steps") or 4),
        }
        if body.get("request_id"):
            fields["request_id"] = str(body["request_id"]).strip()
        if body.get("harmonise") is not None:
            fields["harmonise"] = "true" if body.get("harmonise") else "false"
        if body.get("shadow") is not None:
            fields["shadow"] = "true" if body.get("shadow") else "false"
        if chosen_model:
            fields["model"] = chosen_model
        return await _forward_seneca_image_op(
            base, api_key, "/images/replace-background", image_bytes, fields,
            label="replace-background",
        )

    @router.post("/api/image/outpaint")
    async def outpaint(request: Request):
        """Proxy seneca's canvas outpaint/extend (~/v1/images/outpaint, alias /extend)."""
        user = require_privilege(request, "can_generate_images")
        body = await request.json()
        base, api_key, image_bytes, chosen_model = await _resolve_and_decode(body, user)
        fields: Dict[str, str] = {
            "prompt": str(body.get("prompt") or ""),
            "steps": str(body.get("steps") or 4),
        }
        for key in ("ratio", "size", "context"):
            if body.get(key) is not None and str(body.get(key)) != "":
                fields[key] = str(body[key])
        if body.get("request_id"):
            fields["request_id"] = str(body["request_id"]).strip()
        if chosen_model:
            fields["model"] = chosen_model
        return await _forward_seneca_image_op(
            base, api_key, "/images/outpaint", image_bytes, fields,
            label="outpaint", timeout=_outpaint_timeout(),
        )

    @router.post("/api/image/extend")
    async def outpaint_extend(request: Request):
        """Alias of /api/image/outpaint (the provider exposes /outpaint as /extend too)."""
        return await outpaint(request)

    @router.get("/api/image/edit-progress")
    async def edit_progress(request: Request):
        """Poll a running image edit's provider-side step progress.

        Contract v1 (image-op progress): the provider exposes
        ``GET /v1/images/progress/{request_id}`` -> ``{stage, step, steps,
        eta_s, progress}``. This proxy resolves the configured image endpoint
        (owner-scoped) and returns that JSON, or ``{"available": false}`` when
        the provider has no progress route yet, so the client can keep an
        indeterminate bar without breaking.
        """
        import httpx
        user = require_privilege(request, "can_generate_images")
        request_id = (request.query_params.get("request_id") or "").strip()
        if not request_id:
            raise HTTPException(400, "Missing request_id")
        # request_id is spliced into the outbound URL, so it must be a safe token
        # (no path separators/host chars). The base itself is a DB-validated row.
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", request_id):
            raise HTTPException(400, "Invalid request_id")
        requested_base = (request.query_params.get("base") or "").strip().rstrip("/")
        endpoint_id = (request.query_params.get("endpoint_id") or "").strip()
        db = SessionLocal()
        try:
            ep = None
            if endpoint_id:
                ep = _visible_image_endpoint_for_base(db, endpoint_id, user)
                if ep is None:
                    return {"available": False, "reason": "endpoint not found or disabled"}
            elif requested_base:
                ep = _visible_image_endpoint_for_base(db, requested_base, user)
                if ep is None:
                    return {"available": False, "reason": "endpoint not found or disabled"}
            else:
                ep = _first_visible_image_endpoint(db, user)
            if ep is None:
                return {"available": False, "reason": "no image endpoint configured"}
            base = ep.base_url.rstrip("/")
        finally:
            db.close()

        from src.url_safety import check_outbound_url
        block_private = os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true"
        ok, reason = check_outbound_url(base, block_private=block_private)
        if not ok:
            # CodeQL #1203: Never expose internal addresses, resolver errors or raw
            # exception details to clients or logs. Use a stable diagnostic message
            # without interpolating untrusted exception content or destination strings.
            logger.warning("edit-progress endpoint rejected: outbound URL policy check failed")
            return {"available": False, "reason": "endpoint URL rejected"}

        if not base.endswith("/v1"):
            base += "/v1"
        # Dynamic path segment (validated token above), so not routed through
        # _join_checked_gallery_endpoint's exact-match whitelist.
        url = f"{base}/images/progress/{request_id}"
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    data.setdefault("available", True)
                    return data
                return {"available": True, "progress": data}
            return {"available": False, "status": r.status_code}
        except Exception as exc:
            logger.debug("edit-progress poll failed: %s", exc)
            return {"available": False, "reason": "provider unreachable"}

    # ---- POST /api/image/harmonize — proper img2img call ----
    # Earlier version routed through inpaint with a full-white mask, but
    # most backends interpret "100% mask coverage" as "regenerate from
    # scratch using the prompt", ignoring the source. Real img2img sends
    # the image alongside a `strength` (denoising strength) and the model
    # mixes that fraction of new noise into the existing pixels.
    @router.post("/api/image/harmonize")
    async def harmonize_image(request: Request):
        """Harmonize = img2img. The model preserves (1 - strength) of the
        original and regenerates `strength` fraction. With strength ~0.4
        you get edge blending + lighting unification while keeping the
        composition recognisable."""
        import httpx
        user = require_privilege(request, "can_generate_images")
        body = await request.json()

        image_b64 = body.get("image")
        if not image_b64:
            raise HTTPException(400, "No image provided")

        requested_base = (body.get("_endpoint") or "").rstrip("/")
        # SSRF hardening: a client-supplied endpoint is fetched server-side
        # below, so validate it first (mirrors routes/embedding_routes.py).
        # Local-first means loopback/LAN is allowed by default; the cloud
        # metadata range and non-HTTP(S) schemes are always rejected.
        if requested_base and "://" in requested_base:
            from src.url_safety import check_outbound_url
            ok, reason = check_outbound_url(
                requested_base,
                block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
            )
            if not ok:
                raise HTTPException(400, f"Rejected endpoint URL: {reason}")
        model = (body.get("_model") or "").strip()

        api_key = None
        if not requested_base:
            db = SessionLocal()
            try:
                ep = _first_visible_image_endpoint(db, user)
                if not ep:
                    raise HTTPException(400, "No image generation endpoint configured.")
                base = ep.base_url.rstrip("/")
                api_key = ep.api_key
            finally:
                db.close()
        else:
            # Resolve the client-supplied base to a registered visible endpoint.
            # Admins are not exempted — gallery proxy routes must use a DB row
            # so the outbound URL never depends directly on request-body input.
            db = SessionLocal()
            try:
                ep = _visible_image_endpoint_for_base(db, requested_base, user, model)
                if not ep:
                    raise HTTPException(403, "Choose a registered image endpoint")
                base = ep.base_url.rstrip("/")
                api_key = ep.api_key
            finally:
                db.close()

        if not base.endswith("/v1"):
            base += "/v1"

        prompt = body.get("prompt") or "natural lighting, harmonious color, seamless blend"
        # Legacy single-strength control (old clients) → maps to color_match
        strength = body.get("strength", 0.45)
        try:
            strength = float(strength)
        except Exception:
            strength = 0.45
        strength = max(0.05, min(0.95, strength))
        # New two-stage controls. Clients may send either color_match/seam_fix
        # explicitly, or fall back to strength→color_match for legacy.
        try:
            color_match = float(body.get("color_match", strength))
        except Exception:
            color_match = strength
        try:
            seam_fix = float(body.get("seam_fix", 0.0))
        except Exception:
            seam_fix = 0.0
        color_match = max(0.0, min(1.0, color_match))
        seam_fix = max(0.0, min(1.0, seam_fix))
        body_mask_b64 = body.get("body_mask") or body.get("mask")
        seam_mask_b64 = body.get("seam_mask")

        # OpenAI's image API has no img2img mode — its edits endpoint
        # regenerates pixels from the prompt rather than preserving the
        # source. Earlier hack (alpha-blend the regen back at `strength`)
        # produced visibly broken results, so we refuse and tell the
        # user to spin up a real diffusion endpoint instead.
        if _is_openai_api_base(base):
            raise HTTPException(400,
                "Harmonize needs a diffusion server that supports img2img "
                "(SD WebUI / Forge / Comfy). OpenAI's API doesn't expose "
                "one. Cookbook → Models can serve an SD-compatible model "
                "locally in a few clicks.")

        # Try img2img-shaped routes in order. Most self-hosted servers
        # expose at least one of these. Whatever returns 200 wins.
        # /images/harmonize is our own diffusion_server.py's native endpoint —
        # try it first since it's purpose-built for this and tolerates models
        # that only ship an inpaint pipeline.
        harmonize_payload = {
            "image": image_b64,
            "prompt": prompt,
            "color_match": color_match,
            "seam_fix": seam_fix,
            # Legacy field names so an un-restarted older diffusion server
            # still recognises the body mask. The new server prefers
            # `body_mask` over `mask`, so sending both is safe.
            "strength": color_match,
        }
        if body_mask_b64:
            harmonize_payload["body_mask"] = body_mask_b64
            harmonize_payload["mask"] = body_mask_b64
        if seam_mask_b64:
            harmonize_payload["seam_mask"] = seam_mask_b64

        candidates = [
            ("/images/harmonize", "json", harmonize_payload),
            ("/images/img2img", "json", {
                "image": image_b64,
                "prompt": prompt,
                "strength": strength,
                **({"model": model} if model else {}),
            }),
            ("/images/variations", "json", {
                "image": image_b64,
                "prompt": prompt,
                "strength": strength,
                **({"model": model} if model else {}),
            }),
            # Last-resort fallback: AUTOMATIC1111-style sdapi route.
            ("/sdapi/v1/img2img", "json_a1111", {
                "init_images": [f"data:image/png;base64,{image_b64}"],
                "prompt": prompt,
                "denoising_strength": strength,
                "steps": 30,
                **({"override_settings": {"sd_model_checkpoint": model}} if model else {}),
            }),
        ]

        # Strip the /v1 for the AUTOMATIC1111 path which uses /sdapi/v1/...
        base_root = base[:-3] if base.endswith("/v1") else base

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        last_err = None
        # Cold-start image servers can take 90-110s for a full pass (and
        # 60-90s more on first request while weights load to GPU). 300s gives
        # headroom for both on slower setups.
        async with httpx.AsyncClient(timeout=300) as client:
            for path, kind, payload in candidates:
                _effective_base = base_root if path.startswith("/sdapi") else base
                target = _join_checked_gallery_endpoint(_effective_base, path)
                try:
                    r = await client.post(target, json=payload, headers=headers)
                    if r.status_code == 404:
                        last_err = f"{path}: 404"
                        continue  # try next variant
                    if r.status_code != 200:
                        logger.warning("harmonize: %s returned %s", path, r.status_code)
                        last_err = f"{path}: {r.status_code}"
                        continue
                    data = r.json()
                    # Normalise return shape.
                    if isinstance(data, dict):
                        # Server returned 200 with an explicit error field —
                        # surface it now instead of trying the other routes
                        # (otherwise the real error gets buried under 404s).
                        if data.get("error") and not data.get("image"):
                            logger.warning("harmonize: server error at %s: %s", path, data.get("error"))
                            raise HTTPException(502, f"Diffusion server error at {path}")
                        if data.get("image"):
                            return {"image": data["image"]}
                        if data.get("images") and isinstance(data["images"], list):
                            img0 = data["images"][0]
                            if isinstance(img0, str):
                                # A1111 sometimes returns "data:image/png;base64,..." prefix
                                if img0.startswith("data:"):
                                    img0 = img0.split(",", 1)[1]
                                return {"image": img0}
                        # OpenAI-style {"data":[{"b64_json": ...}]}
                        if data.get("data"):
                            item = data["data"][0]
                            if item.get("b64_json"):
                                return {"image": item["b64_json"]}
                            if item.get("url"):
                                img_b64 = await _fetch_result_image_b64(item["url"])
                                if img_b64:
                                    return {"image": img_b64}
                    last_err = f"{path}: server returned no image"
                except httpx.ConnectError:
                    logger.warning("harmonize: can't reach diffusion server at %s", base)
                    raise HTTPException(502, "Can't reach diffusion server")
                except httpx.TimeoutException:
                    raise HTTPException(504, "Harmonize timed out (240s) — restart the diffusion server or lower Color match / disable Seam fix")
        raise HTTPException(502,
            "No supported img2img route responded. "
            "Your diffusion server needs to expose one of: "
            "/v1/images/harmonize, /v1/images/img2img, /v1/images/variations, /sdapi/v1/img2img.")

    # ---- POST /api/image/sharpen ----
    @router.post("/api/image/sharpen")
    async def sharpen_image(request: Request):
        """Apply unsharp-mask sharpening to an image."""
        require_privilege(request, "can_generate_images")
        body = await request.json()
        image_b64 = body.get("image")
        amount = body.get("amount", 50) / 100.0

        from PIL import ImageFilter
        import base64, io

        img = _b64_to_pil_image(image_b64, mode="RGB")

        # Unsharp mask: radius=2, percent=amount*200, threshold=3
        sharpened = img.filter(ImageFilter.UnsharpMask(radius=2, percent=int(amount * 200), threshold=3))

        buf = io.BytesIO()
        sharpened.save(buf, format="PNG")
        return {"image": base64.b64encode(buf.getvalue()).decode()}

    # ---- POST /api/image/denoise ----
    # AI denoise via Real-ESRGAN with the realesr-general-x4v3 weights at
    # outscale=1 + denoise_strength. Falls back to a "package missing"
    # error so the client can prompt the user to install via Cookbook.
    @router.post("/api/image/denoise")
    async def denoise_image(request: Request):
        require_privilege(request, "can_generate_images")
        body = await request.json()
        image_b64 = body.get("image")
        if not image_b64:
            raise HTTPException(400, "No image provided")
        _proxied = await _route_heavy_image_op("/api/image/denoise", body, label="AI denoise")
        if _proxied is not None:
            return _proxied
        try:
            strength = float(body.get("strength", 0.5))
        except Exception:
            strength = 0.5
        strength = max(0.0, min(1.0, strength))
        try:
            import base64, io
            from PIL import Image
            import numpy as np
        except ImportError:
            raise HTTPException(500, "Server missing a required dependency")
        # Decode source image (RGB; Real-ESRGAN doesn't preserve alpha).
        src = _b64_to_pil_image(image_b64, mode="RGB")
        try:
            patch_realesrgan_torchvision_compat()
            from realesrgan import RealESRGANer
        except ImportError:
            return {"error": "realesrgan not installed. Install it from Cookbook → Dependencies (search 'realesrgan')."}
        try:
            # General-purpose lightweight model with denoise control.
            from realesrgan.archs.srvgg_arch import SRVGGNetCompact
            model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64,
                                    num_conv=32, upscale=4, act_type='prelu')
            upsampler = RealESRGANer(
                scale=4,
                model_path='https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth',
                dni_weight=[strength, 1.0 - strength],
                model=model,
                tile=400, tile_pad=10, pre_pad=0, half=False,
            )
            arr = np.array(src)
            output, _ = upsampler.enhance(arr, outscale=1)
            out_img = Image.fromarray(output)
            buf = io.BytesIO()
            out_img.save(buf, format="PNG")
            return {"image": base64.b64encode(buf.getvalue()).decode()}
        except Exception:
            logger.warning("Denoise failed", exc_info=True)
            return {"error": "Denoise failed"}

    # ---- POST /api/image/upscale-local ----
    # Local Real-ESRGAN upscale (2× or 4×). Self-contained — no diffusion
    # server required. Used by the editor's AI Upscale button.
    @router.post("/api/image/upscale-local")
    async def upscale_image_local(request: Request):
        require_privilege(request, "can_generate_images")
        body = await request.json()
        image_b64 = body.get("image")
        if not image_b64:
            raise HTTPException(400, "No image provided")
        _proxied = await _route_heavy_image_op("/api/image/upscale-local", body, label="AI upscale")
        if _proxied is not None:
            return _proxied
        try:
            scale = int(body.get("scale", 2))
        except Exception:
            scale = 2
        scale = 2 if scale not in (2, 4) else scale
        try:
            import base64, io
            from PIL import Image
            import numpy as np
        except ImportError:
            raise HTTPException(500, "Server missing a required dependency")
        src = _b64_to_pil_image(image_b64, mode="RGB")
        try:
            patch_realesrgan_torchvision_compat()
            from basicsr.archs.rrdbnet_arch import RRDBNet
            from realesrgan import RealESRGANer
        except ImportError:
            return {"error": "realesrgan not installed. Install it from Cookbook → Dependencies (search 'realesrgan')."}
        try:
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                            num_block=23, num_grow_ch=32, scale=4)
            upsampler = RealESRGANer(
                scale=4,
                model_path='https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth',
                model=model,
                tile=400, tile_pad=10, pre_pad=0, half=False,
            )
            arr = np.array(src)
            output, _ = upsampler.enhance(arr, outscale=scale)
            out_img = Image.fromarray(output)
            buf = io.BytesIO()
            out_img.save(buf, format="PNG")
            return {"image": base64.b64encode(buf.getvalue()).decode()}
        except Exception:
            logger.warning("AI upscale failed", exc_info=True)
            return {"error": "AI upscale failed"}

    # ---- POST /api/image/generate ----
    @router.post("/api/image/generate")
    async def generate_image(request: Request):
        """Text-to-image generation for the editor's Quick Edit 'Image' mode.

        Generates a brand-new image from a prompt using the admin-configured
        image model, returned as base64 so the client can drop it in as a layer.
        """
        import json as _json
        user = require_privilege(request, "can_generate_images")
        try:
            body = await request.json()
        except Exception:
            body = {}
        prompt = str((body or {}).get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "A prompt is required")
        try:
            width = int((body or {}).get("width") or 0)
            height = int((body or {}).get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        if height > width:
            size = "1024x1536"
        elif width > height:
            size = "1536x1024"
        else:
            size = "1024x1024"
        try:
            from src.ai_interaction import do_generate_image
        except Exception:
            raise HTTPException(502, "Image generation is unavailable")
        # The editor's model picker may choose a specific (cloud) image model;
        # forward it so settings defaults only apply when none is selected.
        model_choice = str((body or {}).get("model") or "").strip()
        payload = {"prompt": prompt, "size": size}
        if model_choice:
            payload["model"] = model_choice
        content = _json.dumps(payload)
        result = await do_generate_image(content, owner=user, incognito=True)
        if not isinstance(result, dict) or result.get("error"):
            msg = (result.get("error") if isinstance(result, dict) else None) or "Image generation failed"
            low = str(msg).lower()
            # Plain-language status codes the editor maps to fixed messages:
            #   503 -> generation is switched off on the server
            #   502 -> the image server could not be reached (or failed)
            if "disabled" in low or "switched off" in low or "not enabled" in low:
                raise HTTPException(503, "Image generation is switched off on the server at the moment.")
            if "timed out" in low or "timeout" in low:
                raise HTTPException(504, f"Image server timed out: {msg}")
            if "connection" in low or "connect" in low or "unreachable" in low:
                raise HTTPException(502, f"Image server unreachable: {msg}")
            # A configured image model that cannot be resolved means its endpoint
            # did not answer, i.e. the image server is down, not a bad prompt.
            if "no endpoint found with image model" in low:
                raise HTTPException(502, f"Image server unreachable: {msg}")
            raise HTTPException(502, msg)
        image_url = result.get("image_url") or ""
        b64 = image_url.split(",", 1)[1] if image_url.startswith("data:") else image_url
        if not b64:
            raise HTTPException(502, "Image generation returned no image")
        return {
            "image": b64,
            "prompt": prompt,
            "model": result.get("image_model"),
            "size": result.get("image_size") or size,
        }

    # ---- POST /api/image/remove-bg ----
    @router.post("/api/image/mask")
    async def smart_mask(request: Request):
        """Create a neutral segmentation mask from user-provided points or a box.

        This endpoint intentionally does not inspect edit prompts. It only
        turns explicit visual selection hints into a binary mask that the
        editor can reuse for wand/layer-mask/inpaint workflows.
        """
        require_privilege(request, "can_generate_images")
        body = await request.json()
        _proxied = await _remote_smart_mask(body)
        if _proxied is not None:
            return _proxied
        image = _b64_to_pil_image(body.get("image") or "", mode="RGB")
        points = body.get("points") or []
        box = body.get("box")
        text = (body.get("text") or body.get("query") or "").strip()
        grounded = None

        if not points and not box and text:
            grounded = _ground_text_to_box(image, text)
            box = grounded["box"]

        if not points and not box:
            raise HTTPException(400, "Provide at least one point, box, or object text")

        _image_op_log_rss("mask", "start")
        backend = _load_sam_backend()
        torch = backend["torch"]
        processor = backend["processor"]
        model = backend["model"]
        device = backend["device"]

        kwargs: Dict[str, Any] = {"return_tensors": "pt"}
        input_points = []
        if points:
            input_labels = []
            for p in points:
                try:
                    input_points.append([float(p["x"]), float(p["y"])])
                    input_labels.append(int(p.get("label", 1)))
                except Exception as exc:
                    raise HTTPException(400, "Invalid point format") from exc
            kwargs["input_points"] = [input_points]
            kwargs["input_labels"] = [input_labels]
        if box:
            if not isinstance(box, list) or len(box) != 4:
                raise HTTPException(400, "Box must be [x1, y1, x2, y2]")
            try:
                kwargs["input_boxes"] = [[[float(v) for v in box]]]
            except Exception as exc:
                raise HTTPException(400, "Invalid box format") from exc

        try:
            inputs = processor(image, **kwargs)
            model_inputs = _model_inputs_to_device(inputs, device, torch)
            with torch.no_grad():
                outputs = model(**model_inputs)
            masks = processor.image_processor.post_process_masks(
                outputs.pred_masks.detach().cpu(),
                inputs["original_sizes"].detach().cpu(),
                inputs["reshaped_input_sizes"].detach().cpu(),
            )
            mask_tensor = masks[0]
            while getattr(mask_tensor, "ndim", 0) > 3:
                mask_tensor = mask_tensor[0]
            if getattr(mask_tensor, "ndim", 0) == 3:
                scores = outputs.iou_scores.detach().cpu()[0]
                while getattr(scores, "ndim", 0) > 1:
                    scores = scores[0]
                # SAM commonly returns multiple candidates for a click. The
                # highest-IoU candidate can be the entire image, which is
                # useless as an editor selection. Prefer a candidate that
                # contains the clicked point while keeping area reasonable.
                point_xy = None
                if input_points:
                    try:
                        point_xy = (
                            int(round(float(input_points[0][0]))),
                            int(round(float(input_points[0][1]))),
                        )
                    except Exception:
                        point_xy = None
                best_idx = 0
                best_rank = None
                total_px = max(1, int(mask_tensor.shape[-1]) * int(mask_tensor.shape[-2]))
                for i in range(int(mask_tensor.shape[0])):
                    candidate = mask_tensor[i]
                    area_ratio = float(candidate.sum().item()) / float(total_px)
                    if area_ratio >= 0.985:
                        continue
                    contains_click = True
                    if point_xy:
                        px = max(0, min(int(candidate.shape[-1]) - 1, point_xy[0]))
                        py = max(0, min(int(candidate.shape[-2]) - 1, point_xy[1]))
                        contains_click = bool(candidate[py, px].item())
                    if not contains_click:
                        continue
                    score = float(scores[min(i, len(scores) - 1)].item()) if len(scores) else 0.0
                    # Strongly penalize broad masks; a click-selection should
                    # usually be local unless the user gives a box.
                    rank = score - (area_ratio * 0.35)
                    if best_rank is None or rank > best_rank:
                        best_rank = rank
                        best_idx = i
                if best_rank is None and len(scores):
                    best_idx = int(torch.argmax(scores).item())
                mask_tensor = mask_tensor[min(best_idx, mask_tensor.shape[0] - 1)]
            mask_array = (mask_tensor.numpy() > 0).astype("uint8") * 255
            from PIL import Image

            mask_img = Image.fromarray(mask_array, mode="L")
            if mask_img.size != image.size:
                mask_img = mask_img.resize(image.size, Image.NEAREST)
            bbox = mask_img.getbbox()
            result = {
                "mask": _pil_image_to_b64(mask_img),
                "bbox": list(bbox) if bbox else None,
                "model": backend["model_id"],
                "device": device,
            }
            if grounded:
                result["grounding"] = grounded
            _image_op_log_rss("mask", "end")
            _unload_image_models()
            return result
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("smart_mask failed")
            raise HTTPException(500, f"SAM mask failed: {exc}") from exc

    @router.post("/api/image/remove-bg")
    async def remove_background(request: Request):
        """Remove background from an image. If the client passes a `hint_mask`
        (white-where-the-user-wants-the-subject PNG, same dims as the
        image), we constrain the output:

          1. Crop the image to the mask's bounding box (with padding) so
             the model only sees the region the user cares about.
          2. Run rembg on that crop.
          3. Paste the result back at the original offset.
          4. Multiply the final alpha by the user's mask, so anything
             outside the hint becomes transparent regardless of what the
             model thought was foreground.
        """
        require_privilege(request, "can_generate_images")
        body = await request.json()
        image_b64 = body.get("image")
        hint_b64 = body.get("hint_mask")
        _proxied = await _remote_remove_background(body)
        if _proxied is not None:
            return _proxied

        from PIL import Image
        import base64, io

        img = _b64_to_pil_image(image_b64)
        W, H = img.size

        hint = None
        bbox = None
        if hint_b64:
            try:
                hint_bytes = _b64_bytes(hint_b64, label="hint_mask")
                hint = Image.open(io.BytesIO(hint_bytes)).convert("L")
                # Resize the hint to match if dimensions disagree
                if hint.size != img.size:
                    hint = hint.resize(img.size, Image.NEAREST)
                # Bounding box of any non-zero pixel (with 8 px padding)
                bbox = hint.getbbox()
                if bbox:
                    pad = 8
                    bbox = (
                        max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                        min(W, bbox[2] + pad), min(H, bbox[3] + pad),
                    )
            except Exception:
                hint = None
                bbox = None

        # Crop to the bbox if a hint was supplied so rembg sees just the
        # user's region of interest. Otherwise process the whole image.
        if bbox:
            crop = img.crop(bbox)
        else:
            crop = img

        _image_op_log_rss("remove-bg", "start")
        try:
            from rembg import remove
            from starlette.concurrency import run_in_threadpool
            cut = await run_in_threadpool(remove, crop)
        except ImportError:
            try:
                from transformers import pipeline
                from starlette.concurrency import run_in_threadpool
                pipe = await run_in_threadpool(pipeline, "image-segmentation", model="briaai/RMBG-1.4", trust_remote_code=True)
                mask_img = (await run_in_threadpool(pipe, crop, return_mask=True)).convert("L")
                tmp = crop.copy()
                tmp.putalpha(mask_img)
                cut = tmp
            except Exception:
                return {"error": "No background removal model available. Install rembg: pip install rembg"}
        finally:
            _image_op_log_rss("remove-bg", "end")
            _unload_image_models()

        # Compose the cropped result back into a full-size transparent canvas.
        if bbox:
            result = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            result.paste(cut, (bbox[0], bbox[1]), cut)
        else:
            result = cut.convert("RGBA")

        # Final alpha = result.alpha * hint (normalised). Anything outside
        # the user's hint is forced transparent.
        if hint is not None:
            r, g, b, a = result.split()
            # Multiply alphas — use ImageChops to stay in PIL-pure code.
            from PIL import ImageChops
            a = ImageChops.multiply(a, hint)
            result = Image.merge("RGBA", (r, g, b, a))

        # Edge cleanup (feather / grow) moved to the client so the user
        # can re-tune live without re-running the model. Server returns
        # the pristine cutout.

        buf = io.BytesIO()
        result.save(buf, format="PNG")
        return {"image": base64.b64encode(buf.getvalue()).decode()}

    # ---- POST /api/image/enhance-face ----
    @router.post("/api/image/enhance-face")
    async def enhance_face(request: Request):
        """Face/portrait enhancement. Uses GFPGAN if available, falls back to PIL."""
        require_privilege(request, "can_generate_images")
        body = await request.json()
        image_b64 = body.get("image")
        if not image_b64:
            raise HTTPException(400, "No image provided")

        import base64, io, tempfile, os
        from PIL import Image, ImageFilter, ImageEnhance
        import numpy as np

        img = _b64_to_pil_image(image_b64, mode="RGB")

        # Try GFPGAN first (AI face restoration)
        try:
            if not _local_image_models_enabled():
                raise ImportError("local image models disabled")
            from gfpgan import GFPGANer
            import cv2

            model_path = os.path.join(tempfile.gettempdir(), "gfpgan_models")
            os.makedirs(model_path, exist_ok=True)

            restorer = GFPGANer(
                model_path="https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth",
                upscale=1,
                arch="clean",
                channel_multiplier=2,
                bg_upsampler=None,
                model_rootpath=model_path,
            )

            img_bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            _, _, output = restorer.enhance(
                img_bgr,
                has_aligned=False,
                only_center_face=False,
                paste_back=True,
            )

            # Convert back to RGB
            result_rgb = cv2.cvtColor(output, cv2.COLOR_BGR2RGB)
            result_img = Image.fromarray(result_rgb)

            buf = io.BytesIO()
            result_img.save(buf, format="PNG")
            return {"image": base64.b64encode(buf.getvalue()).decode()}

        except ImportError:
            # GFPGAN not available — use PIL-based enhancement (no AI, but works everywhere)
            logger.info("GFPGAN not available — using PIL enhancement fallback")
            # Multi-step enhancement: denoise → sharpen → contrast → color boost
            enhanced = img.filter(ImageFilter.MedianFilter(size=3))  # light denoise
            enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))  # sharpen
            enhanced = ImageEnhance.Contrast(enhanced).enhance(1.15)  # slight contrast boost
            enhanced = ImageEnhance.Color(enhanced).enhance(1.1)  # subtle color boost
            enhanced = ImageEnhance.Brightness(enhanced).enhance(1.05)  # slight brightness lift

            buf = io.BytesIO()
            enhanced.save(buf, format="PNG")
            return {"image": base64.b64encode(buf.getvalue()).decode(), "method": "pil"}
        except Exception:
            logger.exception("enhance_face: failed")
            raise HTTPException(500, "Face enhancement failed")

    # ---- Album management (path-param routes) ----

    def _get_or_404_album(db, album_id: str, user):
        album = db.query(GalleryAlbum).filter(GalleryAlbum.id == album_id).first()
        if not album:
            raise HTTPException(404, "Album not found")
        if not user or album.owner != user:
            raise HTTPException(404, "Album not found")
        return album

    def _get_or_404_image(db, image_id: str, user):
        img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
        if not img:
            raise HTTPException(404, "Image not found")
        if not user or img.owner != user:
            raise HTTPException(404, "Image not found")
        return img

    @router.put("/api/gallery/albums/{album_id}")
    async def update_album(request: Request, album_id: str):
        user = get_current_user(request)
        data = await _json_object(request)
        name = data.get("name")
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise HTTPException(400, "Album name required")
            name = name.strip()
        description = data.get("description")
        if description is not None and not isinstance(description, str):
            raise HTTPException(400, "Album description must be a string")
        db = SessionLocal()
        try:
            album = _get_or_404_album(db, album_id, user)
            if name is not None:
                album.name = name
            if description is not None:
                album.description = description
            if data.get("cover_id") is not None:
                cover_id = data["cover_id"] or None
                if cover_id:
                    _get_or_404_image(db, cover_id, user)
                album.cover_id = cover_id
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.delete("/api/gallery/albums/{album_id}")
    async def delete_album(request: Request, album_id: str):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            album = _get_or_404_album(db, album_id, user)
            q = db.query(GalleryImage).filter(GalleryImage.album_id == album_id)
            if user is not None:
                q = q.filter(GalleryImage.owner == user)
            q.update({"album_id": None}, synchronize_session=False)
            db.delete(album)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/api/gallery/albums/{album_id}/add")
    async def add_to_album(request: Request, album_id: str):
        user = get_current_user(request)
        ids = await _image_ids(request)
        db = SessionLocal()
        try:
            _get_or_404_album(db, album_id, user)
            # Only move images the caller owns
            q = db.query(GalleryImage).filter(GalleryImage.id.in_(ids))
            if user:
                q = q.filter(GalleryImage.owner == user)
            q.update({"album_id": album_id}, synchronize_session=False)
            db.commit()
            return {"ok": True, "count": len(ids)}
        finally:
            db.close()

    @router.post("/api/gallery/albums/{album_id}/remove")
    async def remove_from_album(request: Request, album_id: str):
        user = get_current_user(request)
        ids = await _image_ids(request)
        db = SessionLocal()
        try:
            _get_or_404_album(db, album_id, user)
            q = db.query(GalleryImage).filter(
                GalleryImage.id.in_(ids), GalleryImage.album_id == album_id
            )
            if user:
                q = q.filter(GalleryImage.owner == user)
            q.update({"album_id": None}, synchronize_session=False)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ---- Favorite toggle ----

    @router.post("/api/gallery/{image_id}/favorite")
    async def toggle_favorite(request: Request, image_id: str):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            img = _get_or_404_image(db, image_id, user)
            img.favorite = not img.favorite
            db.commit()
            return {"ok": True, "favorite": img.favorite}
        finally:
            db.close()

    # ---- AI auto-tag ----

    @router.post("/api/gallery/{image_id}/ai-tag")
    async def ai_tag_image(request: Request, image_id: str):
        """Send image to vision model for auto-tagging."""
        import base64, httpx
        from pathlib import Path

        user = get_current_user(request)
        db = SessionLocal()
        try:
            img = _get_or_404_image(db, image_id, user)

            img_path = _gallery_image_path(img.filename)
            if not img_path.exists():
                raise HTTPException(404, "Image file not found")

            # Read and encode
            img_bytes = img_path.read_bytes()
            b64 = base64.b64encode(img_bytes).decode()
            ext = img.filename.rsplit(".", 1)[-1].lower()
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")

            # Resolve vision model via admin Vision setting (same resolver used for docs)
            from src.document_processor import _load_vl_settings, _resolve_vl_model
            vl_settings = _load_vl_settings()
            if not vl_settings.get("vision_enabled", True):
                return {"error": "Vision is disabled — enable it in Settings → Vision"}
            configured = vl_settings.get("vision_model", "")
            try:
                chat_url, model_name, headers = _resolve_vl_model(configured, owner=user)
            except ValueError:
                return {"error": "No vision model configured — set one in Settings → Vision"}
            if not chat_url:
                return {"error": "No vision-capable endpoint configured"}

            # Call vision model — format differs between Anthropic and OpenAI
            from src.llm_core import _detect_provider, _restricts_temperature, _uses_max_completion_tokens
            provider = _detect_provider(chat_url)
            tag_prompt = (
                "Analyze this photo. Return ONLY a comma-separated list of tags. "
                "Include: objects, people (describe by appearance — age range, gender), "
                "scene/setting, activities, mood/atmosphere, colors, location type, "
                "time of day, weather if visible, any text/signs visible. "
                "Be specific but concise. 10-25 tags. No explanation, just tags."
            )

            if provider == "anthropic":
                payload = {
                    "model": model_name,
                    "max_tokens": 200,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {
                                "type": "base64", "media_type": mime, "data": b64,
                            }},
                            {"type": "text", "text": tag_prompt},
                        ],
                    }],
                }
            else:
                _tok_key = "max_completion_tokens" if _uses_max_completion_tokens(model_name) else "max_tokens"
                payload = {
                    "model": model_name,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": tag_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        ],
                    }],
                    _tok_key: 200,
                    "temperature": 0.3,
                }
                # Reasoning models (o1/o3/o4/gpt-5) reject an explicit temperature.
                if _restricts_temperature(model_name):
                    payload.pop("temperature", None)

            h = {"Content-Type": "application/json"}
            if headers:
                h.update(headers)

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(chat_url, json=payload, headers=h)
                if resp.status_code != 200:
                    logger.error("ai_tag vision model: status %s: %s", resp.status_code, resp.text[:500])
                    return {"error": "Vision model request failed"}
                data = resp.json()
                # Anthropic returns content[0].text, OpenAI returns choices[0].message.content
                if provider == "anthropic":
                    content = (data.get("content") or [{}])[0].get("text", "")
                else:
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Clean up tags
            tags = [t.strip().lower() for t in content.split(",") if t.strip()]
            tag_str = ", ".join(tags[:30])
            img.ai_tags = tag_str
            db.commit()
            return {"ok": True, "ai_tags": tag_str}
        except HTTPException:
            raise
        except Exception:
            logger.exception("AI tagging failed")
            return {"error": "Auto-tagging failed"}
        finally:
            db.close()

    return router
