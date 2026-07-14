"""ImageProposal contract — shared across MCP, hub API, and UI (pipeline steps 6–7)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, Optional

from titan.control_net import normalize_control

PROPOSAL_PREFIX = "IMAGE_PROPOSAL:"
_OPS = frozenset({"generate", "regenerate", "upscale", "inpaint"})
_STYLES = frozenset({"realistic", "anime", "pixelart", "krea"})
_IP_METHODS = frozenset({"img2img"})
_REF_ROLES = frozenset({"identity", "style", "composition"})
_ASPECTS = {
    "square": "1024x1024",
    "portrait": "832x1216",
    "landscape": "1216x832",
}
_KREA_ASPECTS = {
    "square": "960x960",
    "portrait": "960x1440",
    "landscape": "1440x960",
}
_SIZE_RE = re.compile(r"\b(\d{3,4})x(\d{3,4})\b", re.I)


def _opt_int(val: Any) -> Optional[int]:
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _opt_float(val: Any) -> Optional[float]:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _opt_bool(val: Any) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False if s else None
    return None


def _size_from_prompt(prompt: str) -> str:
    m = _SIZE_RE.search(prompt or "")
    return f"{m.group(1)}x{m.group(2)}" if m else ""


def normalize_reference_images(raw: Any) -> list[Dict[str, Any]]:
    """Normalize reference image list for identity conditioning / img2img."""
    if not raw:
        return []
    if not isinstance(raw, list):
        return []
    out: list[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or entry.get("image") or entry.get("url") or "").strip()
        gallery_id = str(entry.get("gallery_id") or "").strip()
        b64 = entry.get("b64") or entry.get("data") or entry.get("image_b64")
        if not path and not gallery_id and not (b64 and str(b64).strip()):
            continue
        role = str(entry.get("role") or "identity").strip().lower()
        if role not in _REF_ROLES:
            role = "identity"
        weight = _opt_float(entry.get("weight"))
        if weight is None:
            weight = 0.7
        weight = max(0.0, min(1.0, float(weight)))
        item: Dict[str, Any] = {"role": role, "weight": weight}
        if path:
            item["path"] = path
        if gallery_id:
            item["gallery_id"] = gallery_id
        if b64 and str(b64).strip():
            item["b64"] = str(b64).strip()
        out.append(item)
    return out


def resolve_ip_method(args: Dict[str, Any], *, reference_images: list[Dict[str, Any]]) -> str:
    explicit = str(args.get("ip_method") or "").strip().lower()
    if explicit in _IP_METHODS:
        return explicit
    if reference_images:
        # Reference images require an explicit supported identity backend.
        # (IP-Adapter / PhotoMaker are not supported in Titan.)
        return ""
    if args.get("image") or args.get("init_image") or args.get("strength") is not None:
        return "img2img"
    return ""


def build_proposal(arguments: Dict[str, Any], *, source_provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normalize tool/API args into a canonical ImageProposal dict."""
    args = dict(arguments or {})
    src = dict(source_provenance or {})

    op = str(args.get("op") or src.get("gen_op") or "generate").strip().lower()
    if op not in _OPS:
        op = "generate"

    prompt = (args.get("prompt") or src.get("prompt") or "").strip()
    style = str(args.get("style") or src.get("gen_style") or src.get("style") or "").strip().lower()
    if style not in _STYLES:
        style = ""

    aspect = str(args.get("aspect") or "").strip().lower()
    size = str(args.get("size") or src.get("size") or "").strip()
    if not size:
        size = _size_from_prompt(prompt)
    aspect_map = _KREA_ASPECTS if style == "krea" else _ASPECTS
    if not size and aspect in aspect_map:
        size = aspect_map[aspect]
    if not size:
        if style == "krea":
            from titan.hub_sd_config import chat_defaults_for_style

            d = chat_defaults_for_style("krea")
            w, h = d.get("width"), d.get("height")
            size = f"{w}x{h}" if w and h else "960x1440"
        else:
            size = "1024x1024"

    quality = str(args.get("quality") or src.get("quality") or "high").strip().lower()
    if quality not in ("low", "medium", "high", "auto"):
        quality = "high"

    negative = (args.get("negative_prompt") or src.get("negative_prompt") or "").strip()
    n = _opt_int(args.get("n")) or 1
    n = max(1, min(4, n))

    seed = _opt_int(args.get("seed"))
    if seed is None and op in ("regenerate", "upscale", "inpaint"):
        seed = _opt_int(src.get("seed") or src.get("gen_seed"))

    reference_images = normalize_reference_images(
        args.get("reference_images") or src.get("reference_images")
    )
    ip_method = resolve_ip_method(args, reference_images=reference_images)

    control = normalize_control(args.get("control") or src.get("control"))
    control_net = _opt_bool(args.get("control_net"))
    if control_net is None:
        control_net = _opt_bool(src.get("control_net"))

    proposal: Dict[str, Any] = {
        "id": str(args.get("id") or uuid.uuid4()),
        "op": op,
        "prompt": prompt,
        "negative_prompt": negative,
        "style": style,
        "quality": quality,
        "size": size,
        "aspect": aspect if aspect in _ASPECTS or aspect in _KREA_ASPECTS else "",
        "n": n,
        "seed": seed,
        "source_image_id": (args.get("source_image_id") or src.get("source_image_id") or None),
        "strength": _opt_float(args.get("strength")),
        "mask": args.get("mask"),
        "loras": args.get("loras"),
        "display_prompt": (args.get("display_prompt") or prompt).strip(),
        "image": args.get("image") or args.get("init_image"),
        "reference_images": reference_images,
        "ip_method": ip_method or None,
        "ip_weight": _opt_float(args.get("ip_weight") if args.get("ip_weight") is not None else src.get("ip_weight")),
        "control": control,
        "control_net": control_net,
    }
    if args.get("shutdown_after") is not None:
        proposal["shutdown_after"] = bool(args.get("shutdown_after"))
    if args.get("scene"):
        proposal["scene"] = True
    for key in ("cfg_scale", "steps", "sampler", "scheduler"):
        val = args.get(key)
        if val is not None and str(val).strip() != "":
            proposal[key] = val
    if proposal["source_image_id"]:
        proposal["source_image_id"] = str(proposal["source_image_id"]).strip() or None
    return proposal


def encode_proposal_response(proposal: Dict[str, Any]) -> str:
    return PROPOSAL_PREFIX + json.dumps(proposal, ensure_ascii=False)


def parse_proposal_from_text(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(PROPOSAL_PREFIX):
            try:
                return json.loads(line[len(PROPOSAL_PREFIX):])
            except json.JSONDecodeError:
                return None
    if text.strip().startswith(PROPOSAL_PREFIX):
        try:
            return json.loads(text.strip()[len(PROPOSAL_PREFIX):])
        except json.JSONDecodeError:
            return None
    return None


def load_source_provenance(image_id: str, owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load gallery provenance for regenerate/upscale/inpaint."""
    if not image_id:
        return None
    try:
        from core.database import GalleryImage, SessionLocal
        from src.auth_helpers import owner_filter

        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(GalleryImage.id == image_id)
            if owner:
                q = owner_filter(q, GalleryImage, owner)
            row = q.first()
            if not row:
                return None
            prov: Dict[str, Any] = {
                "prompt": row.prompt,
                "size": row.size,
                "quality": row.quality,
                "style": getattr(row, "gen_style", None),
                "gen_style": getattr(row, "gen_style", None),
                "negative_prompt": getattr(row, "negative_prompt", None),
                "seed": getattr(row, "gen_seed", None),
                "gen_seed": getattr(row, "gen_seed", None),
                "cfg_scale": getattr(row, "cfg_scale", None),
                "steps": getattr(row, "steps", None),
                "sampler": getattr(row, "sampler", None),
                "scheduler": getattr(row, "scheduler_name", None),
                "clip_skip": getattr(row, "clip_skip", None),
                "source_image_id": image_id,
                "filename": row.filename,
            }
            if getattr(row, "loras_json", None):
                try:
                    prov["loras"] = json.loads(row.loras_json)
                except Exception:
                    pass
            if getattr(row, "reference_images_json", None):
                try:
                    prov["reference_images"] = json.loads(row.reference_images_json)
                except Exception:
                    pass
            if getattr(row, "ip_method", None):
                prov["ip_method"] = row.ip_method
            if getattr(row, "ip_weight", None) is not None:
                prov["ip_weight"] = row.ip_weight
            return prov
        finally:
            db.close()
    except Exception:
        return None


def proposal_to_scheduler_body(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Map ImageProposal → scheduler /v1/images/generations body."""
    body: Dict[str, Any] = {
        "prompt": proposal.get("prompt") or "",
        "negative_prompt": proposal.get("negative_prompt") or "",
        "style": proposal.get("style") or "realistic",
        "quality": proposal.get("quality") or "high",
        "size": proposal.get("size") or "1024x1024",
        "n": proposal.get("n") or 1,
        "shutdown_after": proposal.get("shutdown_after") if proposal.get("shutdown_after") is not None else True,
    }
    for key in ("cfg_scale", "steps", "sampler", "scheduler", "loras", "control"):
        if proposal.get(key) is not None:
            body[key] = proposal[key]
    if proposal.get("scene"):
        body["scene"] = True
    if proposal.get("seed") is not None:
        body["seed"] = proposal["seed"]
    if proposal.get("strength") is not None:
        body["strength"] = proposal["strength"]
    if proposal.get("image"):
        body["image"] = proposal["image"]
    if proposal.get("reference_images"):
        body["reference_images"] = proposal["reference_images"]
    if proposal.get("ip_method"):
        body["ip_method"] = proposal["ip_method"]
    if proposal.get("ip_weight") is not None:
        body["ip_weight"] = proposal["ip_weight"]
    return body
