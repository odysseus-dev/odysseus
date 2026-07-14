"""Deterministic image execution kernel — shared by hub API (no LLM)."""

from __future__ import annotations

import base64
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Set

import httpx

from src.constants import GENERATED_IMAGES_DIR
from titan.control_net import resolve_control_for_scheduler
from titan.control_net_two_pass import (
    resolve_control_net_enabled,
    scheduler_two_pass_generations,
    two_pass_eligible,
)
from titan.image_proposal import build_proposal, load_source_provenance, proposal_to_scheduler_body
from titan.reference_images import aggregate_ip_weight, resolve_reference_images_for_scheduler
from titan.style_labels import STYLE_LABELS_LONG, get_active_styles, style_display_name

LOG = logging.getLogger("titan.image_kernel")

_SCHEDULER_URL = os.environ.get("TITAN_SCHEDULER_URL", "http://host.docker.internal:8150").rstrip("/")

_executed_proposals: Set[str] = set()
_MAX_DEDUPE = 256


def _valid_session_id(session_id: Optional[str]) -> Optional[str]:
    sid = (session_id or "").strip() or None
    if not sid:
        return None
    try:
        from core.database import SessionLocal
        from core.database import Session as DBSession

        db = SessionLocal()
        try:
            ok = db.query(DBSession.id).filter(DBSession.id == sid).first()
            return sid if ok else None
        finally:
            db.close()
    except Exception:
        return None


def _mark_executed(proposal_id: str) -> bool:
    """Return False if this proposal id was already executed (dedupe)."""
    pid = str(proposal_id or "").strip()
    if not pid:
        return True
    if pid in _executed_proposals:
        return False
    _executed_proposals.add(pid)
    if len(_executed_proposals) > _MAX_DEDUPE:
        # Drop arbitrary half — good enough for in-process dedupe.
        for _ in range(_MAX_DEDUPE // 2):
            _executed_proposals.pop()
    return True


async def resolve_proposal(proposal: Dict[str, Any], *, owner: Optional[str] = None) -> Dict[str, Any]:
    """Server-side resolve (presets, buckets, merged negative) without generating."""
    body = proposal_to_scheduler_body(proposal)
    if body.get("reference_images") and not body.get("ip_method"):
        body.pop("reference_images", None)
        body.pop("ip_weight", None)
    elif body.get("reference_images"):
        resolved_refs = resolve_reference_images_for_scheduler(body["reference_images"], owner=owner)
        if resolved_refs:
            body["reference_images"] = resolved_refs
            if body.get("ip_weight") is None:
                ip_w = aggregate_ip_weight(resolved_refs, proposal.get("ip_weight"))
                if ip_w is not None:
                    body["ip_weight"] = ip_w
    body["preview"] = True
    timeout = httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{_SCHEDULER_URL}/v1/images/resolve", json=body)
    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:300]
        return {"error": f"resolve failed ({resp.status_code}): {detail}"}
    data = resp.json()
    return {"resolved": (data or {}).get("resolved") or data}


def _normalize_image_b64(raw: Any) -> str:
    """Strip data-URL prefix; return raw base64 for scheduler."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.startswith("data:"):
        comma = s.find(",")
        if comma >= 0:
            s = s[comma + 1 :]
    return s


async def execute_proposal(
    raw_proposal: Dict[str, Any],
    *,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one ImageProposal through the scheduler and persist to gallery."""
    session_id = _valid_session_id(session_id)
    source_id = raw_proposal.get("source_image_id")
    src_prov = None
    if source_id:
        src_prov = load_source_provenance(str(source_id), owner)
        if not src_prov:
            return {"error": f"Source image not found: {source_id}", "exit_code": 1}

    proposal = build_proposal(raw_proposal, source_provenance=src_prov)
    if not proposal.get("prompt"):
        return {"error": "Proposal prompt is required", "exit_code": 1}
    if proposal.get("style") not in STYLE_LABELS_LONG:
        return {"error": "Proposal style must be realistic, anime, pixelart, or krea", "exit_code": 1}
    if proposal.get("style") not in get_active_styles():
        return {
            "error": (
                f"Style '{proposal.get('style')}' is not registered in Titan yet "
                "(add launch profile in Model Hub)."
            ),
            "exit_code": 1,
        }

    if not _mark_executed(str(proposal.get("id") or "")):
        return {"error": "This proposal was already executed", "exit_code": 1}

    op = proposal.get("op") or "generate"
    body = proposal_to_scheduler_body(proposal)
    if raw_proposal.get("studio_mode"):
        body["shutdown_after"] = False

    if body.get("reference_images") and not body.get("ip_method"):
        body.pop("reference_images", None)
        body.pop("ip_weight", None)
    elif body.get("reference_images"):
        resolved_refs = resolve_reference_images_for_scheduler(
            body["reference_images"],
            owner=owner,
        )
        if not resolved_refs:
            return {"error": "No readable reference images", "exit_code": 1}
        body["reference_images"] = resolved_refs
        if body.get("ip_method") in (None, "") and proposal.get("ip_method"):
            body["ip_method"] = proposal["ip_method"]
        if body.get("ip_weight") is None:
            ip_w = aggregate_ip_weight(resolved_refs, proposal.get("ip_weight"))
            if ip_w is not None:
                body["ip_weight"] = ip_w

    if body.get("control"):
        resolved_control = resolve_control_for_scheduler(body["control"], owner=owner)
        if resolved_control:
            body["control"] = resolved_control
        else:
            body.pop("control", None)

    init_b64 = _normalize_image_b64(proposal.get("image"))
    if init_b64:
        body["image"] = init_b64
        if proposal.get("strength") is not None:
            body["strength"] = proposal["strength"]
        elif body.get("strength") is None:
            body["strength"] = 0.55

    # img2img paths need init image from gallery file
    if op in ("regenerate", "upscale", "inpaint") and src_prov and src_prov.get("filename") and not body.get("image"):
        img_path = Path(GENERATED_IMAGES_DIR) / src_prov["filename"]
        if not img_path.is_file():
            return {"error": f"Source image file missing: {src_prov['filename']}", "exit_code": 1}
        b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
        body["image"] = b64
        if op == "upscale":
            body["upscale_factor"] = raw_proposal.get("upscale_factor") or 2
        if op == "inpaint" and proposal.get("mask"):
            body["mask"] = proposal["mask"]
        if proposal.get("strength") is None:
            body["strength"] = 0.45 if op == "regenerate" else 0.35

    scheduler_path = "/v1/images/generations"
    if op == "upscale":
        scheduler_path = "/v1/images/upscale"
    elif op == "inpaint":
        scheduler_path = "/v1/images/inpaint"
    elif op == "regenerate" and body.get("image"):
        scheduler_path = "/v1/images/img2img"

    control_net_enabled = resolve_control_net_enabled(raw=raw_proposal, proposal=proposal)
    use_two_pass = two_pass_eligible(
        op=op,
        proposal=proposal,
        body=body,
        control_net_enabled=control_net_enabled,
    )

    try:
        timeout = httpx.Timeout(connect=20.0, read=300.0, write=20.0, pool=20.0)
        if use_two_pass:
            data, two_pass = await scheduler_two_pass_generations(
                body,
                scheduler_url=_SCHEDULER_URL,
                owner=owner,
                timeout=timeout,
            )
            if two_pass:
                LOG.info("ControlNet two-pass completed for proposal %s", proposal.get("id"))
        else:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{_SCHEDULER_URL}{scheduler_path}", json=body)
            if resp.status_code != 200:
                error_text = resp.text[:500]
                try:
                    err = resp.json().get("error")
                    error_text = err.get("message", error_text) if isinstance(err, dict) else str(err or error_text)
                except Exception:
                    pass
                return {"error": f"Image generation failed ({resp.status_code}): {error_text}", "exit_code": 1}
            try:
                data = resp.json()
            except Exception:
                return {"error": "Scheduler returned non-JSON", "exit_code": 1}
    except RuntimeError as exc:
        return {"error": str(exc), "exit_code": 1}
    except Exception as exc:
        return {"error": f"Scheduler unreachable: {type(exc).__name__}: {exc}", "exit_code": 1}

    images = (data or {}).get("data") or []
    if not images or not images[0].get("b64_json"):
        return {"error": "No image returned from scheduler", "exit_code": 1}

    return _persist_scheduler_response(
        data,
        proposal=proposal,
        op=op,
        owner=owner,
        session_id=session_id,
        source_image_id=source_id,
    )


def _persist_scheduler_response(
    data: Dict[str, Any],
    *,
    proposal: Dict[str, Any],
    op: str,
    owner: Optional[str],
    session_id: Optional[str],
    source_image_id: Optional[str],
) -> Dict[str, Any]:
    from mcp_servers.gallery_provenance import (
        apply_provenance_to_gallery_row,
        embed_png_provenance,
        ensure_gallery_provenance_columns,
        format_tool_result_provenance,
    )

    ensure_gallery_provenance_columns()

    try:
        from src.settings import get_setting
        pub_base = (get_setting("app_public_url", "") or "").rstrip("/")
    except Exception:
        pub_base = ""

    style = proposal.get("style") or "realistic"
    size = proposal.get("size") or "1024x1024"
    quality = proposal.get("quality") or "high"
    display_prompt = proposal.get("display_prompt") or proposal.get("prompt") or ""

    img_dir = Path(GENERATED_IMAGES_DIR)
    img_dir.mkdir(parents=True, exist_ok=True)

    resolved_base = (data or {}).get("resolved") or {}
    images = (data or {}).get("data") or []
    saved: list[str] = []
    provenance_list: list[dict] = []
    for im in images:
        b64 = im.get("b64_json")
        if not b64:
            continue
        prov = dict(im.get("provenance") or resolved_base)
        if source_image_id:
            prov["source_image_id"] = source_image_id
        if proposal.get("reference_images"):
            prov["reference_images"] = proposal.get("reference_images")
        if proposal.get("ip_method"):
            prov["ip_method"] = proposal.get("ip_method")
        if proposal.get("ip_weight") is not None:
            prov["ip_weight"] = proposal.get("ip_weight")
        raw = base64.b64decode(b64)
        raw = embed_png_provenance(raw, prov)
        fname = f"{uuid.uuid4().hex[:12]}.png"
        (img_dir / fname).write_bytes(raw)
        saved.append(fname)
        provenance_list.append(prov)

    if not saved:
        return {"error": "Unexpected image format from scheduler", "exit_code": 1}

    image_urls = [f"{pub_base}/api/generated-image/{fname}" for fname in saved]
    image_url = image_urls[0]
    gallery_ids: list[str] = []

    try:
        from core.database import GalleryImage, SessionLocal

        db = SessionLocal()
        for fname, prov in zip(saved, provenance_list):
            gid = str(uuid.uuid4())
            row = GalleryImage(
                id=gid,
                filename=fname,
                prompt=prov.get("prompt") or display_prompt,
                model=f"titan-sd:{prov.get('style', style)}",
                size=prov.get("size") or size,
                quality=prov.get("quality") or quality,
                owner=owner,
                session_id=session_id,
            )
            apply_provenance_to_gallery_row(row, prov, op=op)
            if source_image_id and hasattr(row, "source_image_id"):
                row.source_image_id = source_image_id
            db.add(row)
            gallery_ids.append(gid)
        db.commit()
        db.close()
    except Exception as exc:
        LOG.warning("gallery save failed: %s", exc)

    prov0 = provenance_list[0] if provenance_list else resolved_base
    prov_block = format_tool_result_provenance(prov0) if prov0 else ""
    gallery_line = ""
    if gallery_ids:
        gallery_line = f"gallery_id: {gallery_ids[0]}\n"
        if len(gallery_ids) > 1:
            gallery_line += f"gallery_ids: {', '.join(gallery_ids)}\n"

    if len(image_urls) == 1:
        links_block = f"Direct link: {image_urls[0]}\n"
        headline = f"Generated image for: {display_prompt}\n"
    else:
        links_block = "Direct links:\n" + "\n".join(f"- {u}" for u in image_urls) + "\n"
        headline = f"Generated {len(image_urls)} images for: {display_prompt}\n"

    result_text = (
        f"{headline}"
        f"{links_block}"
        f"{gallery_line}"
        f"style: {prov0.get('style', style)} ({STYLE_LABELS_LONG.get(prov0.get('style', style), style)})\n"
        f"size: {prov0.get('size', size)} | quality: {prov0.get('quality', quality)} | count: {len(saved)}\n"
        f"{prov_block}\n"
        "(SD was shut down and the chat LLM restored.)"
    )

    return {
        "stdout": result_text,
        "exit_code": 0,
        "image_url": image_url,
        "image_urls": image_urls,
        "image_prompt": display_prompt,
        "image_model": style_display_name(prov0.get("style", style)),
        "image_size": prov0.get("size", size),
        "image_quality": prov0.get("quality", quality),
        "image_id": gallery_ids[0] if gallery_ids else None,
        "gallery_id": gallery_ids[0] if gallery_ids else None,
        "gallery_ids": gallery_ids,
        "seed": prov0.get("seed"),
        "provenance": prov0,
    }
