"""In-process agent tools for the scoped ``photo_editor`` capability family.

These handlers run as the logged-in owner and drive the image editor over the
owner-scoped in-process bridge (``/api/editor/bridge/*``) plus the app's
existing image transforms (``/api/image/*``). They are only offered to the
model when the ``photo_editor`` family is active (the Quick Edit popup or an
explicit photo-edit chat turn), never in the general tool list.

Loopback calls carry the internal token and impersonate ``owner`` (middleware
app.py), so the editor bridge resolves the same ``owner:session`` scope the
browser subscriber uses.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.tools._common import _INTERNAL_BASE, _internal_headers

logger = logging.getLogger(__name__)


def _provider_message() -> str:
    try:
        from src.ai_interaction import IMAGE_PROVIDER_REQUIRED_MESSAGE

        return IMAGE_PROVIDER_REQUIRED_MESSAGE
    except Exception:
        return (
            "Removing/adding objects needs an image generation provider; enable one "
            "in Settings > Models."
        )


def _provider_available(owner: Optional[str]) -> bool:
    try:
        from src.ai_interaction import image_editing_provider_available

        return bool(image_editing_provider_available(owner))
    except Exception:
        return True

# Local diffusion/image servers can take 90-110s per image (8 steps) and
# longer on a cold start; the previous 120s default timed out mid-generation.
_EDITOR_TIMEOUT = 300.0
# A mask covering less than this fraction of the image cannot drive a useful
# regional inpaint (tiny figures, empty SAM output): fall back to grounding or
# whole-image instruction mode instead.
_MIN_MASK_FRACTION = 0.002
_AI_OPS = {
    "remove-bg": "/api/image/remove-bg",
    "upscale": "/api/image/upscale-local",
    "denoise": "/api/image/denoise",
    "enhance-face": "/api/image/enhance-face",
    "inpaint": "/api/image/inpaint",
    "generative": "/api/image/inpaint",
}
_PLACE_OP = "layers.placeImage"


def _parse(content: Any) -> Dict[str, Any]:
    raw = content if isinstance(content, str) else json.dumps(content or {})
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _session(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    return str(args.get("session") or args.get("editor_session") or ctx.get("session_id") or "default")


async def _post(path: str, payload: Dict[str, Any], owner: Optional[str], timeout: float = _EDITOR_TIMEOUT) -> Dict[str, Any]:
    headers = {**_internal_headers(owner), "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{_INTERNAL_BASE}{path}", json=payload, headers=headers)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}


def _as_data_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("data:"):
        return value
    if value.startswith("/"):
        return None
    return f"data:image/png;base64,{value}"


def _data_url_bytes(data_url: str) -> bytes:
    _, _, b64 = data_url.partition(",")
    return base64.b64decode(b64)


def _persist_png(image_bytes: bytes, owner: Optional[str], session_id: Optional[str], prompt: str, model: str) -> Dict[str, Any]:
    """Save an edited/generated PNG and return {image_id, image_url, image_size}."""
    from core.database import GalleryImage, SessionLocal
    from src.constants import GENERATED_IMAGES_DIR

    db = SessionLocal()
    try:
        root = Path(GENERATED_IMAGES_DIR).resolve()
        root.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:12]}.png"
        (root / filename).write_bytes(image_bytes)
        new_id = str(uuid.uuid4())
        width = height = 0
        try:
            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as img:
                width, height = img.size
        except Exception:
            pass
        size = f"{width}x{height}" if width and height else ""
        # The editor bridge uses an owner-scoped session id (e.g. "pewds:3")
        # that is not a chat-sessions row. GalleryImage.session_id is a FK to
        # sessions.id, so drop an unknown id instead of raising IntegrityError.
        if session_id:
            from core.database import Session as _ChatSession

            try:
                known = db.query(_ChatSession.id).filter(_ChatSession.id == session_id).first()
            except Exception:
                known = None
            if known is None:
                session_id = None
        derived = GalleryImage(
            id=new_id,
            filename=filename,
            prompt=prompt or None,
            caption=None,
            model=model,
            size=size or None,
            quality=None,
            tags=None,
            ai_tags=None,
            session_id=session_id,
            album_id=None,
            owner=owner,
            file_hash=hashlib.sha256(image_bytes).hexdigest(),
            file_size=len(image_bytes),
            width=width or None,
            height=height or None,
        )
        db.add(derived)
        db.commit()
        return {"image_id": new_id, "image_url": f"/api/generated-image/{filename}", "image_size": size or None}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def _place_layer(data_url: str, name: str, owner: Optional[str], session: str, *, masked: bool = False, blend: str = "normal", opacity: float = 100.0, mode: Optional[str] = None) -> bool:
    params: Dict[str, Any] = {
        "dataUrl": data_url,
        "name": name,
        "masked": bool(masked),
        "blend": blend or "normal",
        "opacity": opacity,
    }
    # Forward the edit mode so the client can build a difference mask for
    # instruction-mode results: the whole image was regenerated under an
    # instruction prompt, so untouched pixels must stay pixel-identical (the
    # client keeps only the changed region) instead of the new layer overwriting
    # the entire canvas. Omit the keys entirely when no mode is given so the
    # client keeps its current behavior (e.g. generate_image must not diff-mask).
    if mode:
        params["mode"] = mode
        params["differenceMask"] = mode == "instruction"
        if mode == "instruction":
            # Exclude a thin edge band from the client difference mask: full-frame
            # regenerations can shift pixels along the border.
            params["edgeExcludePx"] = 3
    payload = {
        "session": session,
        "op": _PLACE_OP,
        "params": params,
        "wait": True,
        "timeout": 60,
    }
    try:
        data = await _post("/api/editor/bridge/command", payload, owner, timeout=90)
        return data.get("ok") is not False
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("editor layer placement failed: %s", exc)
        return False


async def _fetch_selection_mask(owner: Optional[str], session: str) -> Optional[str]:
    """Return the editor's current selection as a data-URL mask, or None.

    Generative ops (inpaint/generative) need a mask; the planner/judge may not
    have supplied one, so fall back to whatever is selected in the editor.
    """
    payload = {
        "session": session,
        "op": "selection.exportMask",
        "params": {},
        "wait": True,
        "timeout": 30,
    }
    try:
        data = await _post("/api/editor/bridge/command", payload, owner, timeout=45)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("editor selection mask fetch failed: %s", exc)
        return None
    result = data.get("result") if isinstance(data, dict) else None
    if isinstance(result, dict):
        return _as_data_url(result.get("mask"))
    return None


def _mask_white_fraction(data_url: Optional[str]) -> float:
    """Fraction of a grayscale mask that is selected (white, >=128)."""
    if not data_url:
        return 0.0
    try:
        from PIL import Image as _PILImage

        with _PILImage.open(io.BytesIO(_data_url_bytes(data_url))).convert("L") as im:
            total = im.width * im.height
            if not total:
                return 0.0
            hist = im.histogram()
            return sum(hist[128:]) / total
    except Exception:
        return 0.0


def _image_is_blank(data_url: str) -> bool:
    """True when a result has an alpha channel that is essentially transparent."""
    try:
        from PIL import Image as _PILImage

        with _PILImage.open(io.BytesIO(_data_url_bytes(data_url))) as im:
            if im.mode not in ("RGBA", "LA") and "transparency" not in im.info:
                return False
            alpha = im.convert("RGBA").getchannel("A")
            hist = alpha.histogram()
            total = sum(hist)
            return total > 0 and (sum(hist[1:]) / total) < 0.001
    except Exception:
        return False


def _rect_mask_data_url(image_data_url: str, box: List[float], *, pad_frac: float = 0.15) -> Optional[str]:
    """Build a rectangular mask data URL from a [x1,y1,x2,y2] box, dilated.

    Used when the grounding detector finds an object but SAM returns an empty
    mask (tiny figures): a dilated box guarantees a usable region.
    """
    try:
        from PIL import Image as _PILImage, ImageDraw

        with _PILImage.open(io.BytesIO(_data_url_bytes(image_data_url))) as im:
            w, h = im.size
        x1, y1, x2, y2 = (float(v) for v in box)
        bw, bh = x2 - x1, y2 - y1
        x1, y1 = max(0.0, x1 - bw * pad_frac), max(0.0, y1 - bh * pad_frac)
        x2, y2 = min(float(w), x2 + bw * pad_frac), min(float(h), y2 + bh * pad_frac)
        mask = _PILImage.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rectangle([x1, y1, x2, y2], fill=255)
        buf = io.BytesIO()
        mask.save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


async def _ground_box(owner: Optional[str], image_data_url: str, label: str) -> Optional[List[float]]:
    """Use the in-app grounding detector (OWL-ViT via /api/image/mask) for a box."""
    try:
        data = await _post("/api/image/mask", {"image": image_data_url, "text": label}, owner, timeout=90)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("editor grounding box failed: %s", exc)
        return None
    box = data.get("bbox") if isinstance(data, dict) else None
    if isinstance(box, (list, tuple)) and len(box) == 4:
        try:
            return [float(v) for v in box]
        except Exception:
            return None
    return None


def _ensure_keep_clause(text: str) -> str:
    """Append a keep-the-subject clause when the prompt lacks one."""
    body = str(text or "").strip()
    if not body:
        return body
    if re.search(r"\b(keep|preserve|leave|unchanged|identical|same)\b", body, re.I):
        return body
    return body.rstrip(". ") + ". Keep the main subject and everything else in the image exactly the same."


def _mask_label(args: Dict[str, Any], prompt: str) -> str:
    """Best-effort object noun for grounding a regional removal."""
    label = str(args.get("label") or args.get("object") or "").strip()
    if label:
        return label
    m = re.search(
        r"\b(?:remove|delete|erase|eliminate|get\s+rid\s+of|take\s+out)\s+(?:the\s+|a\s+|an\s+)?"
        r"([a-z][a-z \-]{1,30}?)(?:\s+(?:on|in|at|from|to|near|behind|beside|by)\b|[.,;!?]|$)",
        prompt or "",
        re.I,
    )
    if m:
        noun = m.group(1).strip().lower()
        if noun and noun not in {"background", "bg"}:
            return noun
    for noun in ("person", "people", "man", "woman", "car", "dog", "sign", "logo"):
        if re.search(rf"\b{re.escape(noun)}\b", prompt or "", re.I):
            return "person" if noun in {"person", "people", "man", "woman"} else noun
    return "object"


class PlanEditTool:
    """Plan an image edit as ordered editor operations (no side effects)."""

    async def execute(self, content: Any, ctx: Dict[str, Any]):
        args = _parse(content)
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return "plan_edit: invalid", {"error": "plan_edit needs a non-empty `prompt`.", "exit_code": 1}
        state = args.get("state") if isinstance(args.get("state"), dict) else {}
        try:
            data = await _post(
                "/api/editor/plan",
                {"prompt": prompt, "state": state, "mode": "plan", "session": _session(args, ctx)},
                ctx.get("owner"),
            )
        except Exception as exc:
            return "plan_edit: error", {"error": f"plan_edit failed: {exc}", "exit_code": 1}
        steps = data.get("steps") or []
        reply = str(data.get("reply") or "")
        return f"plan_edit: {len(steps)} step(s)", {
            "output": reply or f"Planned {len(steps)} editor step(s).",
            "reply": reply,
            "planner": data.get("planner"),
            "steps": steps,
            "exit_code": 0,
        }


class RunEditorCommandTool:
    """Run a single validated editor op in the browser via the owner bridge."""

    async def execute(self, content: Any, ctx: Dict[str, Any]):
        args = _parse(content)
        op = str(args.get("op") or "").strip()
        if not op:
            return "run_editor_command: invalid", {"error": "run_editor_command needs `op`.", "exit_code": 1}
        params = args.get("params") if isinstance(args.get("params"), dict) else {}
        payload = {
            "session": _session(args, ctx),
            "op": op,
            "params": params,
            "wait": bool(args.get("wait", True)),
            "timeout": float(args.get("timeout") or 60),
        }
        try:
            data = await _post("/api/editor/bridge/command", payload, ctx.get("owner"))
        except Exception as exc:
            return f"run_editor_command: {op}", {"error": f"run_editor_command failed: {exc}", "exit_code": 1}
        ok = data.get("ok")
        result = data.get("result")
        summary = json.dumps({"ok": ok, "error": data.get("error"), "result": result}, ensure_ascii=False)
        return f"run_editor_command: {op}", {
            "output": summary[:4000],
            "result": result,
            "screenshot": data.get("screenshot"),
            "ok": ok,
            "exit_code": 0 if ok is not False else 1,
        }


class GetEditorScreenshotTool:
    """Capture the current editor canvas as a data URL."""

    async def execute(self, content: Any, ctx: Dict[str, Any]):
        args = _parse(content)
        payload = {
            "session": _session(args, ctx),
            "op": "screenshot",
            "params": {},
            "wait": True,
            "timeout": float(args.get("timeout") or 30),
        }
        try:
            data = await _post("/api/editor/bridge/command", payload, ctx.get("owner"), timeout=45)
        except Exception as exc:
            return "get_editor_screenshot: error", {"error": f"get_editor_screenshot failed: {exc}", "exit_code": 1}
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        shot = data.get("screenshot") or result.get("dataUrl")
        if not shot:
            return "get_editor_screenshot: unavailable", {
                "error": "No editor screenshot was returned (is the editor open?).",
                "exit_code": 1,
            }
        return "get_editor_screenshot", {"output": "Captured the editor canvas.", "screenshot": shot, "exit_code": 0}


class AiEditImageTool:
    """Apply an AI image transform (remove-bg/upscale/denoise/enhance/inpaint)."""

    async def execute(self, content: Any, ctx: Dict[str, Any]):
        args = _parse(content)
        _t = {"start": time.perf_counter()}
        op = str(args.get("op") or "").strip().lower()
        if op not in _AI_OPS:
            return "ai_edit_image: invalid", {
                "error": f"ai_edit_image `op` must be one of {sorted(_AI_OPS)}.",
                "exit_code": 1,
            }
        owner = ctx.get("owner")
        if op in ("inpaint", "generative") and not _provider_available(owner):
            return "ai_edit_image: provider required", {
                "error": _provider_message(),
                "failure_kind": "image_provider_unavailable",
                "needs_provider": True,
                "exit_code": 1,
            }
        session = _session(args, ctx)
        image = _as_data_url(args.get("image"))
        if image is None and isinstance(args.get("image"), str) and args["image"].startswith("/"):
            # A same-origin gallery/attachment URL: fetch it (owner-scoped) as bytes.
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(f"{_INTERNAL_BASE}{args['image']}", headers=_internal_headers(owner))
                    resp.raise_for_status()
                    image = _as_data_url("data:image/png;base64," + base64.b64encode(resp.content).decode())
            except Exception:
                image = None
        used_canvas = False
        if not image:
            shot = await GetEditorScreenshotTool().execute(json.dumps({"session": session}), ctx)
            image = (shot[1] or {}).get("screenshot") if isinstance(shot, tuple) else None
            used_canvas = bool(image)
        if not image:
            return "ai_edit_image: no image", {
                "error": "ai_edit_image needs an `image` (data URL or same-origin URL) or an open editor.",
                "exit_code": 1,
            }
        _t["image"] = time.perf_counter()
        payload: Dict[str, Any] = {"image": image}
        model_choice = str(args.get("model") or "").strip()
        mask_used = False
        place_mode: Optional[str] = None
        if op == "upscale":
            payload["scale"] = int(args.get("scale") or 2)
        elif op == "denoise":
            payload["strength"] = float(args.get("strength") or 0.5)
        elif op in ("inpaint", "generative"):
            payload["prompt"] = str(args.get("prompt") or "").strip()
            if model_choice:
                # inpaint_proxy reads `_model` to route to a specific model.
                payload["_model"] = model_choice
            mode = str(args.get("mode") or "instruction").strip().lower()
            _steps = int(args.get("steps") or 0)
            if _steps > 0:
                payload["steps"] = _steps
            mask_used = False
            mask = None
            if mode == "masked":
                mask = _as_data_url(args.get("mask"))
                if not mask:
                    mask = await _fetch_selection_mask(owner, session)
                # An empty/tiny SAM mask on small figures is useless: drop it.
                if mask and _mask_white_fraction(mask) < _MIN_MASK_FRACTION:
                    mask = None
                if not mask:
                    # Fall back to the grounding detector (OWL-ViT): a dilated
                    # box mask around the matching object.
                    box = await _ground_box(owner, image, _mask_label(args, payload["prompt"]))
                    if box:
                        candidate = _rect_mask_data_url(image, box, pad_frac=0.15)
                        if candidate and _mask_white_fraction(candidate) >= _MIN_MASK_FRACTION:
                            mask = candidate
                if mask:
                    payload["mask"] = mask
                    payload["mode"] = "masked"
                    mask_used = True
                else:
                    # Nothing usable to mask -> whole-image instruction fallback
                    # rather than placing a transparent/empty layer.
                    mode = "instruction"
            if mode != "masked":
                # Instruction mode: whole-image edit with no tight mask. Send a
                # full-white mask so the edit endpoint accepts the request and
                # regenerates the whole frame under the instruction prompt, which
                # names what must stay unchanged. This avoids the mask-compositing
                # halos that loose selections caused.
                payload["mode"] = "instruction"
                payload.pop("mask", None)
                payload["prompt"] = _ensure_keep_clause(payload["prompt"])
                try:
                    from PIL import Image as _PILImage

                    _raw = _data_url_bytes(image)
                    _w, _h = _PILImage.open(io.BytesIO(_raw)).size
                    _mbuf = io.BytesIO()
                    _PILImage.new("L", (_w, _h), 255).save(_mbuf, "PNG")
                    payload["mask"] = "data:image/png;base64," + base64.b64encode(_mbuf.getvalue()).decode()
                except Exception:
                    pass
            place_mode = "masked" if mask_used else "instruction"
        _t["prep"] = time.perf_counter()
        try:
            data = await _post(_AI_OPS[op], payload, owner)
        except Exception as exc:
            logger.info(
                "image-op ai_edit_image op=%s stage=provider FAILED image_ms=%.1f prep_ms=%.1f total_ms=%.1f err=%s",
                op, (_t["image"] - _t["start"]) * 1000.0, (_t["prep"] - _t["image"]) * 1000.0,
                (time.perf_counter() - _t["start"]) * 1000.0, exc,
            )
            return f"ai_edit_image: {op}", {"error": f"ai_edit_image ({op}) failed: {exc}", "exit_code": 1}
        _t["provider"] = time.perf_counter()
        result = data.get("image") or data.get("data")
        data_url = _as_data_url(result)
        if not data_url:
            return f"ai_edit_image: {op}", {"error": f"ai_edit_image ({op}) returned no image.", "exit_code": 1}
        if _image_is_blank(data_url):
            return f"ai_edit_image: {op}", {
                "error": f"ai_edit_image ({op}) produced an empty layer.",
                "failure_kind": "empty_result",
                "exit_code": 1,
            }

        info: Dict[str, Any] = {
            "output": f"Applied AI edit '{op}'" + (" to the current canvas." if used_canvas else "."),
            "op": op,
            "model": data.get("model") or model_choice or "",
            "exit_code": 0,
        }
        try:
            info.update(_persist_png(_data_url_bytes(data_url), owner, ctx.get("session_id"), f"ai_edit_image:{op}", f"ai_edit_image:{op}"))
        except Exception as exc:
            logger.warning("ai_edit_image persist failed: %s", exc)
        _t["persist"] = time.perf_counter()
        if bool(args.get("place", True)):
            placed = await _place_layer(
                data_url,
                f"AI {op}",
                owner,
                session,
                masked=mask_used,
                blend=str(args.get("blend") or "normal"),
                opacity=float(args.get("opacity") or 100),
                mode=place_mode,
            )
            info["placed"] = placed
            if placed:
                info["output"] += " Placed the result as a new layer."
        _t["place"] = time.perf_counter()
        logger.info(
            "image-op ai_edit_image op=%s mode=%s canvas=%s image_ms=%.1f mask_ms=%.1f "
            "model_ms=%.1f persist_ms=%.1f place_ms=%.1f total_ms=%.1f",
            op, place_mode or "instruction", used_canvas,
            (_t["image"] - _t["start"]) * 1000.0,
            (_t["prep"] - _t["image"]) * 1000.0,
            (_t["provider"] - _t["prep"]) * 1000.0,
            (_t["persist"] - _t["provider"]) * 1000.0,
            (_t["place"] - _t["persist"]) * 1000.0,
            (_t["place"] - _t["start"]) * 1000.0,
        )
        return f"ai_edit_image: {op}", info


class GenerateImageLayerTool:
    """Generate an image with the configured image model and place it as a layer."""

    async def execute(self, content: str, ctx: dict):
        args = _parse(content)
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return "generate_image: invalid", {"error": "generate_image needs a non-empty `prompt`.", "exit_code": 1}
        owner = ctx.get("owner")
        if not _provider_available(owner):
            return "generate_image: provider required", {
                "error": _provider_message(),
                "failure_kind": "image_provider_unavailable",
                "needs_provider": True,
                "exit_code": 1,
            }
        session = _session(args, ctx)
        payload: Dict[str, Any] = {"prompt": prompt}
        for key in ("model", "size", "quality"):
            if args.get(key):
                payload[key] = args[key]
        try:
            from src.ai_interaction import do_generate_image

            data = await do_generate_image(
                json.dumps(payload, ensure_ascii=False),
                session_id=session,
                owner=owner,
                incognito=True,
            )
        except Exception as exc:
            return "generate_image: error", {"error": f"generate_image failed: {exc}", "exit_code": 1}
        result = data.get("image_url") or data.get("image") or data.get("data")
        data_url = _as_data_url(result)
        if not data_url:
            return "generate_image", {"error": "generate_image returned no image.", "exit_code": 1}
        info: Dict[str, Any] = {
            "output": f"Generated an image for: {prompt[:120]}",
            "image_size": data.get("image_size"),
            "model": data.get("image_model") or str(args.get("model") or ""),
            "exit_code": 0,
        }
        try:
            info.update(_persist_png(_data_url_bytes(data_url), owner, ctx.get("session_id"), prompt, "generate_image"))
        except Exception as exc:
            logger.warning("generate_image persist failed: %s", exc)
        if bool(args.get("place", True)):
            placed = await _place_layer(
                data_url,
                str(args.get("name") or "Generated image"),
                owner,
                session,
                masked=bool(args.get("mask")),
                blend=str(args.get("blend") or "normal"),
                opacity=float(args.get("opacity") or 100),
            )
            info["placed"] = placed
            if placed:
                info["output"] += " Placed it as a new layer."
        return "generate_image", info
