"""
image_gen_server.py — Titan smart image generation (built-in MCP).

Routing per op is configurable (titan/image_pipeline_config.py):
  wizard — NEEDS_USER_INPUT confirm, then execute on confirm=true
  card   — IMAGE_PROPOSAL JSON for UI Generovat button

Execution always goes through titan.image_kernel (single jádro).
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LOG = logging.getLogger("image_gen_mcp")

from titan.style_labels import STYLE_LABELS_LONG, get_active_styles

server = Server("image_gen")

_ASPECTS = {
    "square": "1024x1024",
    "portrait": "832x1216",
    "landscape": "1216x832",
}
_TRUTHY = {
    "yes", "true", "1", "y", "confirm", "confirmed", "approve", "approved", "ok",
    "go", "generate", "proceed", "start", "run", "do it", "doit", "yep", "yeah",
}
_CONFIRM_WAITING: set[str] = set()


def _param_fingerprint(prompt: str, style: str, size: str, quality: str, negative: str) -> str:
    return f"{prompt}|{style}|{size}|{quality}|{negative}"


def _size_from_prompt(prompt: str) -> str:
    import re
    m = re.search(r"\b(\d{3,4})x(\d{3,4})\b", prompt or "")
    return f"{m.group(1)}x{m.group(2)}" if m else ""


def _needs_input(
    text: str,
    *,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
    ctx: Optional[dict] = None,
) -> list[TextContent]:
    from titan.image_wizard import wizard_message

    return [TextContent(type="text", text=wizard_message(
        text, session_id=session_id, owner=owner, ctx=ctx,
    ))]


def _kernel_to_content(result: dict) -> list[TextContent]:
    if result.get("error"):
        return [TextContent(type="text", text=f"Error: {result['error']}")]
    return [TextContent(type="text", text=result.get("stdout") or "Error: empty result")]


@server.list_tools()
async def list_tools() -> list[Tool]:
    from titan.image_pipeline_config import config_as_dict

    modes = config_as_dict()
    mode_hint = (
        f"Routing: generate={modes['generate']}, regenerate={modes['regenerate']} "
        f"(wizard=chat confirm, card=UI button)."
    )
    return [
        Tool(
            name="generate_image",
            description=(
                "Generate or propose image(s) via local Titan VRAM scheduler. "
                f"{mode_hint}\n"
                "Prompt format by style: anime/realistic/pixelart → comma-separated TAGS; "
                "krea (KREA2) → natural-language prose paragraph (NOT danbooru tags). "
                "Anime: danbooru (1boy, 1girl, blue_hair). Realistic: short descriptor tags. "
                "KREA: subject+scene+lighting in sentences; no masterpiece/8k quality spam.\n"
                "Regenerate: op=regenerate, source_image_id=gallery_id, seed when same seed needed.\n"
                "When prompt and style are both set, generation runs in this call. "
                "Use confirm=true only after a confirm summary when wizard_confirm=always."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": (
                            "SDXL styles: comma-separated TAGS (anime danbooru / realistic descriptors). "
                            "style=krea: natural-language prose paragraph describing the static scene — NOT tags."
                        ),
                    },
                    "op": {
                        "type": "string",
                        "enum": ["generate", "regenerate", "upscale", "inpaint"],
                    },
                    "source_image_id": {"type": "string"},
                    "style": {"type": "string", "enum": ["realistic", "anime", "pixelart", "krea"]},
                    "aspect": {"type": "string", "enum": ["square", "portrait", "landscape"]},
                    "size": {"type": "string"},
                    "quality": {"type": "string"},
                    "negative_prompt": {"type": "string"},
                    "n": {
                        "type": "integer",
                        "description": (
                            "Batch count 1–4. Omit unless the user explicitly asked for "
                            "multiple images/variants; default is 1."
                        ),
                    },
                    "cfg_scale": {"type": "number"},
                    "steps": {"type": "integer"},
                    "sampler": {"type": "string"},
                    "scheduler": {"type": "string"},
                    "seed": {"type": "integer"},
                    "strength": {"type": "number"},
                    "control": {
                        "type": "object",
                        "description": (
                            "Optional ControlNet composition guide (sd.cpp). "
                            "Fields: type (canny|depth|pose|raw), image/path/b64, weight 0–1."
                        ),
                        "properties": {
                            "type": {"type": "string", "enum": ["canny", "depth", "pose", "openpose", "raw"]},
                            "path": {"type": "string"},
                            "b64": {"type": "string"},
                            "image": {"type": "string"},
                            "gallery_id": {"type": "string"},
                            "weight": {"type": "number"},
                            "preprocess": {"type": "boolean"},
                        },
                    },
                    "control_net": {
                        "type": "boolean",
                        "description": (
                            "Two-pass ControlNet canny for txt2img. "
                            "Usually auto-detected from the user's chat message "
                            "(e.g. 's control netem', 'with controlnet'). Set explicitly only if needed."
                        ),
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": (
                            "Set true only after the user approved the parameters "
                            "(required when n>1 or after a confirm summary)."
                        ),
                    },
                },
                "required": ["prompt"],
            },
        )
    ]


@server.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "generate_image":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    raw_args = dict(arguments or {})
    owner = (raw_args.pop("_odysseus_owner", None) or "").strip() or None
    session_id = (raw_args.pop("_odysseus_session_id", None) or "").strip() or None
    user_text = (raw_args.pop("_odysseus_user_text", None) or "").strip()

    from titan.image_params import normalize_image_args
    from titan.image_pipeline_config import (
        should_use_card,
        should_use_wizard,
        should_auto_execute_wizard,
        wizard_fingerprint_auto_confirm,
    )
    from titan.image_proposal import (
        build_proposal,
        encode_proposal_response,
        load_source_provenance,
    )

    from titan.session_image_context import load_session_image_context

    arguments = normalize_image_args(raw_args, source_text=user_text)
    session_ctx = load_session_image_context(session_id, owner)
    prompt = (arguments.get("prompt") or "").strip()
    style = (arguments.get("style") or "").strip().lower()
    aspect = (arguments.get("aspect") or "").strip().lower()
    size = (arguments.get("size") or "").strip()
    quality = (arguments.get("quality") or "high").strip().lower()
    negative = (arguments.get("negative_prompt") or "").strip()
    confirm_raw = arguments.get("confirm")
    confirm = (str(confirm_raw).strip().lower() in _TRUTHY) if confirm_raw is not None else False

    def _opt_int(key):
        v = arguments.get(key)
        try:
            return int(v) if v is not None and str(v).strip() != "" else None
        except (ValueError, TypeError):
            return None

    def _opt_float(key):
        v = arguments.get(key)
        try:
            return float(v) if v is not None and str(v).strip() != "" else None
        except (ValueError, TypeError):
            return None

    n = _opt_int("n")
    if "n" not in raw_args or raw_args.get("n") is None:
        n = 1
    else:
        n = max(1, min(4, n or 1))
    cfg_scale = _opt_float("cfg_scale")
    steps = _opt_int("steps")
    seed = _opt_int("seed")
    sampler = (arguments.get("sampler") or "").strip() or None
    scheduler = (arguments.get("scheduler") or "").strip() or None

    if not prompt:
        return _needs_input(
            "No prompt given. Ask the user what they want the image to depict.",
            session_id=session_id, owner=owner, ctx=session_ctx,
        )

    try:
        from src.settings import get_setting
        if not get_setting("image_gen_enabled", True):
            return [TextContent(type="text", text="Error: Image generation is disabled by the administrator.")]
    except Exception:
        pass

    if style not in STYLE_LABELS_LONG:
        return _needs_input(
            "Which style should I use? Ask the user to choose:\n"
            "- realistic (ThisIsReal SDXL v3.0 — photoreal)\n"
            "- anime (Nova Anime XL IL v19 — anime/illustration)\n"
            "- pixelart (Pixel Storm XL v1.0 — pixel art; trigger: pixel art)\n"
            "- krea (Dark Beast KREA 2 — KREA2 stack; write prompt as prose, not tags)\n"
            'Then call generate_image again with style="realistic", style="anime", style="pixelart", or style="krea" '
            "(keep the same prompt).",
            session_id=session_id, owner=owner, ctx=session_ctx,
        )

    if style == "krea" and style not in get_active_styles():
        return _needs_input(
            "KREA (Dark Beast KREA 2) checkpoint is not on disk yet. "
            "Add it in Titan Model Hub, or use style=\"realistic\" / style=\"anime\" for now.",
            session_id=session_id, owner=owner, ctx=session_ctx,
        )

    if not size:
        size = _size_from_prompt(prompt)
    if not size:
        size = _ASPECTS.get(aspect or "square", "1024x1024")
    if quality not in ("low", "medium", "high", "auto"):
        quality = "high"

    source_id = (arguments.get("source_image_id") or "").strip() or None
    src_prov = load_source_provenance(source_id, owner) if source_id else None
    if source_id and not src_prov:
        return [TextContent(type="text", text=f"Error: source image not found: {source_id}")]

    arguments.update({
        "prompt": prompt,
        "style": style,
        "size": size,
        "quality": quality,
        "negative_prompt": negative,
        "n": n,
    })
    if cfg_scale is not None:
        arguments["cfg_scale"] = cfg_scale
    if steps is not None:
        arguments["steps"] = steps
    if seed is not None:
        arguments["seed"] = seed
    if sampler:
        arguments["sampler"] = sampler
    if scheduler:
        arguments["scheduler"] = scheduler

    from titan.image_followup import check_image_followup_conflicts
    from titan.image_wizard import validate_tool_params

    validation = validate_tool_params(seed=seed, n=n, raw_args=raw_args)
    if validation:
        return _needs_input(
            validation, session_id=session_id, owner=owner, ctx=session_ctx,
        )

    followup = check_image_followup_conflicts(
        arguments,
        raw_args,
        confirm=confirm,
    )
    if followup:
        return _needs_input(
            followup, session_id=session_id, owner=owner, ctx=session_ctx,
        )

    proposal = build_proposal(arguments, source_provenance=src_prov)
    proposal["display_prompt"] = prompt
    op = proposal.get("op") or "generate"

    # --- UI card path ---
    if should_use_card(op):
        try:
            from titan.image_kernel import resolve_proposal
            resolved = await resolve_proposal(proposal, owner=owner)
            if resolved.get("resolved"):
                proposal["resolved"] = resolved["resolved"]
        except Exception as exc:
            LOG.debug("proposal resolve skipped: %s", exc)
        return [TextContent(type="text", text=encode_proposal_response(proposal))]

    # --- Chat wizard path ---
    if not should_use_wizard(op):
        return [TextContent(type="text", text=f"Error: no trigger configured for op={op}")]

    fp = _param_fingerprint(prompt, style, size, quality, negative)
    if not confirm:
        if should_auto_execute_wizard(prompt=prompt, style=style, op=op):
            confirm = True
        elif wizard_fingerprint_auto_confirm() and fp in _CONFIRM_WAITING:
            _CONFIRM_WAITING.discard(fp)
            confirm = True
        else:
            _CONFIRM_WAITING.add(fp)
            extra = []
            if cfg_scale is not None:
                extra.append(f"cfg_scale={cfg_scale}")
            if steps is not None:
                extra.append(f"steps={steps}")
            if sampler:
                extra.append(f"sampler={sampler}")
            if scheduler:
                extra.append(f"scheduler={scheduler}")
            if seed is not None:
                extra.append(f"seed={seed}")
            if source_id:
                extra.append(f"source_image_id={source_id}")
            extra_line = ("- advanced: " + ", ".join(extra) + "\n") if extra else ""
            return _needs_input(
                "Confirm these parameters with the user before I generate:\n"
                f"- style: {style} ({STYLE_LABELS_LONG[style]})\n"
                f"- size: {size}" + (f" (aspect={aspect})" if aspect else "") + "\n"
                f"- quality: {quality}\n"
                f"- count: {n}\n"
                f"- prompt: {prompt}\n"
                f"- negative_prompt: {negative or '(none)'}\n"
                + extra_line + "\n"
                "When the user approves (yes / go / approve), call generate_image again "
                "with confirm=true and the SAME parameters. "
                "Do NOT use ui_control for image generation.\n"
                "(Generating swaps the chat LLM out for SD for ~45 s, then restores it.)",
                session_id=session_id, owner=owner, ctx=session_ctx,
            )

    _CONFIRM_WAITING.discard(fp)
    from titan.image_kernel import execute_proposal

    result = await execute_proposal(proposal, owner=owner, session_id=session_id)
    return _kernel_to_content(result)


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
