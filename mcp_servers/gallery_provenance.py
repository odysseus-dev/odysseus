"""Gallery image provenance — DB columns + PNG metadata (image pipeline step 3)."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

LOG = logging.getLogger("gallery_provenance")

_PROVENANCE_COLUMNS: list[tuple[str, str]] = [
    ("gen_seed", "INTEGER"),
    ("negative_prompt", "TEXT"),
    ("cfg_scale", "REAL"),
    ("steps", "INTEGER"),
    ("sampler", "TEXT"),
    ("scheduler_name", "TEXT"),
    ("clip_skip", "INTEGER"),
    ("gen_style", "TEXT"),
    ("gen_op", "TEXT"),
    ("source_image_id", "TEXT"),
    ("loras_json", "TEXT"),
    ("reference_images_json", "TEXT"),
    ("ip_method", "TEXT"),
    ("ip_weight", "REAL"),
]


def ensure_gallery_provenance_columns() -> None:
    """Add provenance columns to gallery_images if missing (idempotent)."""
    import os
    import sqlite3

    try:
        from core.database import DATABASE_URL
    except Exception:
        return
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not db_path or not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(gallery_images)")}
        for col, typ in _PROVENANCE_COLUMNS:
            if col not in existing:
                conn.execute(f"ALTER TABLE gallery_images ADD COLUMN {col} {typ}")
                LOG.info("Added gallery_images.%s", col)
        conn.commit()
    except Exception as exc:
        LOG.warning("gallery provenance migration: %s", exc)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _a1111_parameters_text(prov: dict[str, Any]) -> str:
    """A1111-compatible parameters string for PNG tEXt chunk."""
    prompt = prov.get("prompt") or ""
    neg = prov.get("negative_prompt") or ""
    steps = prov.get("steps", "")
    sampler = prov.get("sampler", "")
    cfg = prov.get("cfg_scale", "")
    seed = prov.get("seed", "")
    size = prov.get("size") or f"{prov.get('width', '')}x{prov.get('height', '')}"
    return (
        f"{prompt}\n"
        f"Negative prompt: {neg}\n"
        f"Steps: {steps}, Sampler: {sampler}, CFG scale: {cfg}, Seed: {seed}, "
        f"Size: {size}, Model hash: titan-sd, Model: {prov.get('style', '')}"
    )


def embed_png_provenance(png_bytes: bytes, prov: dict[str, Any]) -> bytes:
    """Embed generation parameters into PNG (portable provenance)."""
    try:
        import io

        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img = Image.open(io.BytesIO(png_bytes))
        pinfo = PngInfo()
        pinfo.add_text("parameters", _a1111_parameters_text(prov))
        pinfo.add_text("titan_provenance", json.dumps(prov, ensure_ascii=False))
        out = io.BytesIO()
        img.save(out, format="PNG", pnginfo=pinfo)
        return out.getvalue()
    except Exception as exc:
        LOG.debug("PNG metadata embed skipped: %s", exc)
        return png_bytes


def apply_provenance_to_gallery_row(row: Any, prov: dict[str, Any], *, op: str = "generate") -> None:
    """Set provenance fields on a GalleryImage ORM instance."""
    row.prompt = prov.get("prompt") or row.prompt
    row.size = prov.get("size") or row.size
    row.quality = prov.get("quality") or row.quality
    row.model = f"titan-sd:{prov.get('style', 'realistic')}"
    if hasattr(row, "gen_seed"):
        row.gen_seed = prov.get("seed")
    if hasattr(row, "negative_prompt"):
        row.negative_prompt = prov.get("negative_prompt")
    if hasattr(row, "cfg_scale"):
        row.cfg_scale = prov.get("cfg_scale")
    if hasattr(row, "steps"):
        row.steps = prov.get("steps")
    if hasattr(row, "sampler"):
        row.sampler = prov.get("sampler")
    if hasattr(row, "scheduler_name"):
        row.scheduler_name = prov.get("scheduler")
    if hasattr(row, "clip_skip"):
        row.clip_skip = prov.get("clip_skip")
    if hasattr(row, "gen_style"):
        row.gen_style = prov.get("style")
    if hasattr(row, "gen_op"):
        row.gen_op = op
    if hasattr(row, "loras_json") and prov.get("loras"):
        row.loras_json = json.dumps(prov.get("loras"), ensure_ascii=False)
    if hasattr(row, "reference_images_json") and prov.get("reference_images"):
        row.reference_images_json = json.dumps(prov.get("reference_images"), ensure_ascii=False)
    if hasattr(row, "ip_method") and prov.get("ip_method"):
        row.ip_method = prov.get("ip_method")
    if hasattr(row, "ip_weight") and prov.get("ip_weight") is not None:
        row.ip_weight = prov.get("ip_weight")


def format_tool_result_provenance(prov: dict[str, Any]) -> str:
    """Machine-readable lines for tool_event / chat regen history."""
    lines = [
        f"seed: {prov.get('seed')}",
        f"negative_prompt: {prov.get('negative_prompt') or ''}",
        f"cfg_scale: {prov.get('cfg_scale')}",
        f"steps: {prov.get('steps')}",
        f"sampler: {prov.get('sampler')}",
        f"scheduler: {prov.get('scheduler')}",
        f"clip_skip: {prov.get('clip_skip')}",
        f"style: {prov.get('style')}",
        f"quality: {prov.get('quality')}",
        f"size: {prov.get('size')}",
    ]
    if prov.get("ip_method"):
        lines.append(f"ip_method: {prov.get('ip_method')}")
    if prov.get("ip_weight") is not None:
        lines.append(f"ip_weight: {prov.get('ip_weight')}")
    refs = prov.get("reference_images")
    if isinstance(refs, list) and refs:
        lines.append(f"reference_images: {len(refs)} ref(s)")
    return "\n".join(lines)


def parse_tool_stdout_provenance(stdout: str) -> dict[str, Any]:
    """Parse provenance lines from generate_image tool stdout (kernel format)."""
    prov: dict[str, Any] = {}
    for line in (stdout or "").splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip().lower()
        val = raw.strip()
        if not key:
            continue
        if key == "seed":
            try:
                prov["seed"] = int(val)
            except (TypeError, ValueError):
                pass
        elif key == "cfg_scale":
            try:
                prov["cfg_scale"] = float(val)
            except (TypeError, ValueError):
                pass
        elif key in ("steps", "clip_skip"):
            try:
                prov[key] = int(val)
            except (TypeError, ValueError):
                pass
        elif key in (
            "negative_prompt", "sampler", "scheduler", "style", "quality", "size",
        ) and val:
            prov[key] = val
    return prov


def enrich_image_tool_result(result: dict[str, Any]) -> None:
    """Lift seed + resolved params from stdout into structured tool result fields."""
    if not isinstance(result, dict) or result.get("exit_code") != 0:
        return
    if isinstance(result.get("provenance"), dict):
        prov = dict(result["provenance"])
    else:
        prov = parse_tool_stdout_provenance(str(result.get("stdout") or ""))
    if not prov:
        return
    result["provenance"] = prov
    if prov.get("seed") is not None:
        result["seed"] = prov["seed"]
    for key in ("style", "quality", "size", "negative_prompt", "cfg_scale", "steps"):
        if prov.get(key) is not None and not result.get(key):
            result[key] = prov[key]


def build_image_args_from_tool_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Merge command JSON, structured fields, and stdout provenance for regen."""
    args: dict[str, Any] = {}
    if isinstance(ev.get("args"), dict):
        args.update(ev["args"])
    command = str(ev.get("command") or "").strip()
    if command.startswith("{"):
        try:
            parsed = json.loads(command)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if v is not None and k not in args:
                        args[k] = v
        except (json.JSONDecodeError, TypeError):
            pass
    prov = ev.get("provenance") if isinstance(ev.get("provenance"), dict) else {}
    if not prov:
        prov = parse_tool_stdout_provenance(str(ev.get("output") or ""))
    for key in (
        "seed", "style", "quality", "size", "negative_prompt",
        "cfg_scale", "steps", "sampler", "scheduler", "clip_skip", "op",
    ):
        if prov.get(key) is not None and args.get(key) is None:
            args[key] = prov[key]
    if ev.get("seed") is not None and args.get("seed") is None:
        args["seed"] = ev["seed"]
    if ev.get("gallery_id") and not args.get("source_image_id"):
        args["source_image_id"] = ev["gallery_id"]
    if ev.get("image_prompt") and not args.get("prompt"):
        args["prompt"] = ev["image_prompt"]
    return args


def enrich_image_tool_event(tool_event: dict[str, Any]) -> None:
    """Persist merged generate_image args on tool_event for history / regen."""
    if tool_event.get("tool") != "generate_image":
        return
    if tool_event.get("exit_code") not in (0, None):
        return
    args = build_image_args_from_tool_event(tool_event)
    if args:
        tool_event["args"] = args
