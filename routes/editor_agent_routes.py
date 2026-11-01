"""Editor agent routes: natural-language planning for the image editor.

Provides:
  POST /api/editor/plan             -> {reply, planner, steps:[{op,params}], done?}
  POST /api/editor/bridge/command   -> enqueue validated ops for the open editor
  GET  /api/editor/bridge/events    -> SSE stream of queued commands (browser subscriber)
  POST /api/editor/bridge/result    -> browser posts results/screenshots back
  POST /api/editor/bridge/await     -> wait for a queued command's result

The browser editor (static/js/editor/nl-edit.js) posts {prompt, state, mode, screenshot}
and consumes {reply, planner, steps} or {done:true}. When mode == "check" and the model
reports completion, the route returns {"done": true} to stop the client's self-check loop.

The planner is wired to the app's configured chat model (vision-capable when a screenshot
is supplied) via set_editor_planner(); a deterministic keyword planner is the fallback so
the editor always gets a usable plan.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import re
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src import editor_style_guide as style_guide
from src import editor_agent_model as editor_agent_model
from src import shutdown as _shutdown

logger = logging.getLogger(__name__)

# ── Op catalog (kept in sync with static/js/editor/editor-control.js) ──
OP_CATALOG = [
    "document.info", "document.importImage",
    "document.crop", "document.resizeCanvas", "document.savePsd",
    "layers.list", "layers.select", "layers.addRaster", "layers.placeImage", "layers.addAdjustment",
    "layers.duplicate", "layers.delete", "layers.setOpacity", "layers.setBlend",
    "layers.group", "layers.ungroup", "layers.addStyle",
    "ai.generativeExpand",
    "selection.selectAll", "selection.deselect", "selection.invert",
    "selection.rectangle", "selection.ellipse",
    "selection.subject", "selection.sky", "selection.background", "selection.samText",
    "selection.colorRange", "selection.maskBounds", "selection.exportMask",
    "adjustments.apply", "adjustments.applyMasked",
    "filters.apply", "filters.applyMasked",
    "transform.start", "transform.apply", "transform.cancel",
    "text.add",
    "history.mark", "history.collapse", "history.undo", "history.redo",
    "export.png", "export.gallery", "screenshot",
]
# Retouch/selection ops are defined once in the shared style guide so the
# server planner and the browser fallback cannot drift; a plan naming one of
# these would otherwise be filtered out of the catalog.
for _extra_op in style_guide.NEW_OP_NAMES:
    if _extra_op not in OP_CATALOG:
        OP_CATALOG.append(_extra_op)
del _extra_op
_OP_SET = frozenset(OP_CATALOG)

# Canonical argument shapes advertised to the model so it stops guessing. The
# browser client tolerates loose forms as a safety net, but the model should
# emit exactly these.
_ADJUSTMENT_TYPES = (
    "brightness-contrast {brightness,contrast} (multipliers, 1=neutral), "
    "exposure {exposure,offset,gamma}, white-balance {temperature,tint}, "
    "hue-saturation {hue,saturation,lightness}, vibrance {vibrance}, black-white, "
    "shadows-highlights {shadows,highlights}, levels, curves, color-balance, "
    "photo-filter {color,density,preserve_luminosity}, selective-color, gradient-map, "
    "invert, posterize, threshold, channel-mixer, color-lookup"
)
_FILTER_IDS = (
    "blur-gaussian, blur-motion, blur-zoom, sharpen, sharpen-edges, unsharp-mask, "
    "noise-add, noise-despeckle, pixelate-mosaic, pixelate-crystallize, stylize-emboss, "
    "stylize-find-edges, stylize-solarize, distort-twirl, distort-pinch, distort-spherize, "
    "distort-ripple, render-clouds, clarity {amount,radius}, vignette {amount,feather}"
)
_CANONICAL_GUIDANCE = (
    "Canonical op shapes (use exactly these, never invent keys or names): "
    "adjustments.apply / adjustments.applyMasked params = "
    "{\"type\": <one of: " + _ADJUSTMENT_TYPES + ">, \"params\": {...}}; "
    "filters.apply / filters.applyMasked params = {\"id\": <one of: " + _FILTER_IDS + ">}. "
    "Never nest a numeric bag under {\"adjustments\": {...}} with no type, and never invent "
    "adjustment types or filter ids. \"clarity\" (params amount,radius) and \"vignette\" "
    "(params amount,feather) are real filters — use them for local-contrast and framing. "
    "Canvas: document.crop {x,y,width,height}, document.resizeCanvas {width,height,anchor}, "
    "ai.generativeExpand {width,height,anchor,prompt,mode:'instruction',feather}. Text/styles: "
    "text.add {text,x,y,color,size}, layers.addStyle {type,params}. Export: document.savePsd. "
    "ai_edit_image takes a mode: 'instruction' (default; whole image, no mask, prompt names "
    "what stays unchanged) for background/scene/season/style changes, adding an object to an "
    "existing photo, and removing diffuse distractions; 'masked' (tight selection first) ONLY "
    "for explicit regional removal of a clearly identified object. generate_image is ONLY for "
    "creating a brand-new image."
)
# The shared style guide is the single source of truth for intent routing,
# calibrated strengths and the low-key/temperature/portrait rules. Both the
# server planner and the browser fallback read it (the client fetches
# GET /api/editor/style-guide).
_STYLE_GUIDANCE = style_guide.guidance()

# EDITOR_PLANNER: async (messages: list[dict], owner: Optional[str]) -> str | dict. Set by app.py.
_EDITOR_PLANNER: Optional[Callable[[List[dict], Optional[str]], Any]] = None
_MAX_STEPS = 32
_QUEUE_MAX = 64
# Generative editor steps (SAM mask + local diffusion) take 30-110s each, so
# bridge waits and the agent's per-step wait must outlast the provider.
_BRIDGE_DEFAULT_TIMEOUT = 330.0

# ── Strict JSON ops schema (structured outputs / grammar) ──
# The planner (the local Qwen3.5-9B at :8097 and every fallback) is constrained
# to this schema so it can only emit valid editor ops. Allowed ops:
#   * the deterministic editor ops (OP_CATALOG),
#   * generate_edit  -> image model (instruction, optional mask) [=ai_edit_image]
#   * select_object  -> SAM mask by description (seneca /v1/images/mask)
#                       [=selection.samText]
#   * ask_user       -> ask 2-3 concrete choices (never a silent tone default).
# The model-facing aliases are translated to the internal execution ops below,
# so the browser client and the executor keep their existing op vocabulary.
_MODEL_PLAN_OPS = tuple(dict.fromkeys(
    list(OP_CATALOG) + ["generate_edit", "select_object", "ai_edit_image",
                        "generate_image", "ask_user"]
))
_ALLOWED_PLAN_OPS = frozenset(OP_CATALOG) | {"ai_edit_image", "generate_image"}
_PLAN_OP_ALIASES = {
    "generate_edit": "ai_edit_image",      # image model: instruction + optional mask
    "select_object": "selection.samText",  # SAM mask by description
}
# Inner op values the ai_edit_image tool accepts (src/agent_tools/editor_tools.py
# `_AI_OPS`). A model-authored ai_edit_image step frequently omits the inner op or
# invents one (e.g. "restyle"), which the tool then rejects. Normalise it so
# instruction/restyle edits become op "generative" and masked region edits become
# "inpaint".
_AI_IMAGE_INNER_OPS = frozenset(
    {"remove-bg", "upscale", "denoise", "enhance-face", "inpaint", "generative"}
)


def _normalize_ai_step(params: Any) -> Dict[str, Any]:
    """Repair an ai_edit_image step's inner op so it always runs (restyle ->
    generative, masked -> inpaint)."""
    params = dict(params) if isinstance(params, dict) else {}
    mode = str(params.get("mode") or "").strip().lower()
    inner = str(params.get("op") or "").strip().lower()
    if inner not in _AI_IMAGE_INNER_OPS:
        inner = "inpaint" if mode == "masked" else "generative"
        params["op"] = inner
    if inner in ("inpaint", "generative") and not mode:
        params["mode"] = "masked" if inner == "inpaint" else "instruction"
    return params
PLAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": list(_MODEL_PLAN_OPS),
                        "description": (
                            "generate_edit = AI image model edit (params {prompt, "
                            "mode:'instruction'|'masked'}; do NOT add an inner 'op' — "
                            "instruction/restyle edits run as the image model's "
                            "'generative' op, masked region edits as 'inpaint'). "
                            "select_object = SAM mask by text (params {text}). "
                            "ask_user = ask with 2-3 choices (params {question, options})."
                        ),
                    },
                    "params": {"type": "object"},
                },
                "required": ["op"],
            },
        },
        "ask_user": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question", "options"],
        },
        "done": {"type": "boolean"},
    },
    "required": ["reply", "steps"],
}
PLAN_RESPONSE_FORMAT: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {"name": "editor_plan", "schema": PLAN_SCHEMA},
}


def _normalize_ask_user(payload: Any) -> Optional[Dict[str, Any]]:
    """Validate an ask_user payload: one question and 2-3 concrete choices."""
    if not isinstance(payload, dict):
        return None
    question = str(payload.get("question") or "").strip()
    raw = payload.get("options")
    labels: List[str] = []
    for opt in (raw or []):
        if isinstance(opt, str):
            lbl = opt.strip()
        elif isinstance(opt, dict):
            lbl = str(opt.get("label") or "").strip()
        else:
            lbl = ""
        if lbl:
            labels.append(lbl)
    labels = labels[:3]
    if not question or len(labels) < 2:
        return None
    # Match the shape the ask_user tool + the editor client expect: option
    # objects with a label (and an optional description).
    return {
        "question": question,
        "options": [{"label": lbl, "description": ""} for lbl in labels],
        "multi": False,
    }


def _validate_plan(data: Any) -> Optional[Dict[str, Any]]:
    """Validate a raw planner plan against the strict ops schema.

    Returns a normalized plan (aliases translated, ops checked against the
    allowed set, ask_user normalized to 2-3 choices) or None when the plan is
    invalid or empty. Callers turn None into an ask_user plan — never a silent
    tone default.
    """
    if not isinstance(data, dict):
        return None
    reply = str(data.get("reply") or "")
    ask_user = _normalize_ask_user(data.get("ask_user"))
    raw_steps = data.get("steps")
    if raw_steps is None:
        raw_steps = []
    if not isinstance(raw_steps, list):
        return None
    steps: List[Dict[str, Any]] = []
    for s in raw_steps[:_MAX_STEPS]:
        if not isinstance(s, dict):
            continue  # drop a malformed step rather than rejecting the whole plan
        op = str(s.get("op") or "")
        if op == "ask_user":
            au = _normalize_ask_user(s.get("params"))
            if au is not None and ask_user is None:
                ask_user = au
            continue
        op = _PLAN_OP_ALIASES.get(op, op)
        if op not in _ALLOWED_PLAN_OPS:
            continue  # drop a single unknown/hallucinated op; keep the valid steps
        params = s.get("params") if isinstance(s.get("params"), dict) else {}
        if op == "ai_edit_image":
            params = _normalize_ai_step(params)
        steps.append({"op": op, "params": params})
    # Guardrails apply to model plans too (no stacked lifts, no brighten after
    # retouch.skin, no global adjustment after a masked recolour).
    steps = style_guide.enforce_guards(steps)
    done = bool(data.get("done"))
    if not steps and not done and ask_user is None:
        return None
    out: Dict[str, Any] = {"reply": reply, "planner": "llm", "steps": steps}
    if ask_user is not None:
        out["ask_user"] = ask_user
    if done:
        out["done"] = True
    return out

# Generative edits (remove/add/replace content) require an image provider. When
# none is available the agent must stop and say so plainly rather than apply a
# deterministic approximation that silently does the wrong thing.
try:
    from src.ai_interaction import IMAGE_PROVIDER_REQUIRED_MESSAGE as _PROVIDER_REQUIRED_MESSAGE
except Exception:  # pragma: no cover - defensive
    _PROVIDER_REQUIRED_MESSAGE = (
        "Removing/adding objects needs an image generation provider; enable one in "
        "Settings > Models."
    )

_AI_CONTENT_OPS = frozenset({"ai_edit_image", "generate_image"})

# Model ids that are editing/inpainting models rather than text-to-image ones.
_EDIT_MODEL_RE = re.compile(r"edit|inpaint|fill|kontext|controlnet|removal|qwen-image-edit", re.I)

# Content nouns that signal a generative object edit rather than a tone/filter
# tweak. Kept narrow so "make it black and white" / "add a vignette" stay
# deterministic.
_GEN_OBJECTS = (
    r"(?:persons?|people|men|man|women|woman|guys?|girls?|boys?|someone|somebody|"
    r"objects?|car|cars|sign|signs|logos?|watermarks?|trees?|buildings?|"
    r"reflections?|animals?|dogs?|cats?|birds?|hands?|fingers?|moons?|suns?|clouds?|"
    r"stars?|fire|smoke|snow|rain|flowers?|mountains?|planets?|balloons?|rockets?|hats?|"
    r"glasses|sunglasses|shades|beard|beards|stickers?|frames?|borders?|"
    r"rainbows?|wildflowers?|sunflowers?|tulips?|daisies?|leaves|pumpkins?|lanterns?|"
    r"butterflies?|bees?|boats?|sailboats?|kites?|umbrellas?)"
)
_REMOVE_RE = re.compile(
    r"\b(?:remove|delete|erase|eliminate|get\s+rid\s+of|take\s+out|photoshop\s+out|paint\s+out)\b"
    r"[^.!?\n]{0,60}?\b" + _GEN_OBJECTS + r"\b",
    re.I,
)
_ADD_RE = re.compile(
    r"\b(?:add|insert|put|place|draw|paint|create|generate)\b"
    r"[^.!?\n]{0,40}?\b" + _GEN_OBJECTS + r"\b",
    re.I,
)
_REPLACE_RE = re.compile(
    r"\b(?:replace|swap|substitute)\b|\b(?:change|turn)\b[^.!?\n]{0,40}?\b(?:to|into)\b",
    re.I,
)
# Removing diffuse clutter ("remove the distractions") is generative too, but
# the specific-noun _REMOVE_RE above does not catch it.
_REMOVE_CLEANUP_RE = re.compile(
    r"\b(?:remove|delete|erase|eliminate|get\s+rid\s+of|take\s+out|clean\s+up|declutter|clear)\b"
    r"[^.!?\n]{0,40}?\b(?:distractions?|clutter|junk|mess|noise|"
    r"unwanted\s+(?:objects?|items?|things?)|stuff|things|blemishes?|spots?)\b",
    re.I,
)

# Changing the whole background/scene (or placing the subject somewhere new)
# is a generative replacement, not the local remove-bg op. "make the
# background nicer/better" is included; plain "blur the background" is not.
_BG_RE = re.compile(
    r"\b(?:replace|change|swap|put|place|set|make|improve|enhance|beautify|upgrade)\b"
    r"[^.!?\n]{0,40}?\b(?:background|backdrop|scene)\b"
    r"|\b(?:put|place)\b[^.!?\n]{0,30}?\b(?:on|in|at)\s+(?:a|an|the)\s+"
    r"(?:beach|street|mountains?|forest|city|park|ocean|sea|desert|room|stage|field|snow|"
    r"night|moon|mars|space|island|jungle)\b",
    re.I,
)
# Season/weather changes and an explicit "generative fill" change scene
# content (not just tone), so they must be a generative edit, never a
# colour adjustment/filter step.
_SEASON_WEATHER_RE = re.compile(
    r"\b(?:snow(?:y|ing|fall)?|winter|autumn(?:al)?|fall\s+(?:leaves|foliage)|spring|"
    r"rain(?:y|ing)?|fog(?:gy)?|mist(?:y)?|storm(?:y)?|overcast)\b",
    re.I,
)
_GENERATIVE_FILL_RE = re.compile(r"\bgenerative(?:\s+fill)?\b", re.I)
# Whole-image artistic RESTYLE requests (turn the photo into a painting, anime,
# pixel art, ...). These are a model instruction edit over the whole frame, not a
# local adjustment. The client has a short fast-path regex; the server must
# generalise so unknown restyle phrasings still reach the model.
_RESTYLE_RE = re.compile(
    r"\b(?:re[\s-]?style|painting|painterly|water[\s-]?colou?r|oil\s+paint(?:ing)?|"
    r"acrylic|gouache|pastel|sketch|pencil|charcoal|ink\s+drawing|line\s+art|comic|"
    r"cartoon|anime|manga|illustration|3d\s+render|cgi|claymation|clay|pixel\s+art|"
    r"voxel|low[\s-]?poly|mosaic|stained\s+glass|pop\s+art|art\s+nouveau|ukiyo[\s-]?e|"
    r"surreal|fantasy\s+art|steampunk|cyberpunk|vaporwave|synthwave|graffiti|woodcut|"
    r"linocut|etching|lithograph|impressionist|cubist|abstract|doodle|vector\s+art|"
    r"neon\s+sign|psychedelic|gothic|comic[\s-]?book|storybook)\b",
    re.I,
)

# Text/title/PSD/format requests are deterministic edits, never generative.
_TEXT_RE = re.compile(
    r"\b(?:title|caption|subtitle|label|heading|watermark|text|word(?:ing)?)\b", re.I,
)
_TEXT_ADD_RE = re.compile(r"\b(?:add|insert|put|place|write|overlay|create|set)\b", re.I)
_STYLE_RE = re.compile(
    r"\b(drop[\s-]?shadow|outer[\s-]?glow|glow|stroke|outline|emboss|bevel)\b", re.I,
)
_PSD_RE = re.compile(r"\b(?:psd|photoshop file|layered file)\b", re.I)

# Aspect -> exact output size. Instagram/format intents crop to the aspect and
# then resize to this size (then optionally add a title).
_ASPECT_TARGETS = (
    (re.compile(r"\b(?:1080\s*[x×]\s*1350|4\s*:\s*5|four[\s-]?fifths?|instagram)\b", re.I), (1080, 1350)),
    (re.compile(r"\b(?:1080\s*[x×]\s*1080|1\s*:\s*1|square)\b", re.I), (1080, 1080)),
    (re.compile(r"\b(?:1080\s*[x×]\s*1920|9\s*:\s*16|story|reel|tiktok|vertical)\b", re.I), (1080, 1920)),
    (re.compile(r"\b(?:1920\s*[x×]\s*1080|16\s*:\s*9|widescreen|landscape video)\b", re.I), (1920, 1080)),
)
_DIM_RE = re.compile(r"\b(\d{3,5})\s*[x×]\s*(\d{3,5})\b")


def _is_text_request(text: str) -> bool:
    return bool(_TEXT_RE.search(text) and _TEXT_ADD_RE.search(text))


def _format_target(text: str) -> Optional[Tuple[int, int]]:
    for pattern, dims in _ASPECT_TARGETS:
        if pattern.search(text):
            return dims
    m = _DIM_RE.search(text)
    if m:
        try:
            return (int(m.group(1)), int(m.group(2)))
        except Exception:
            return None
    return None


def _extend_requested(text: str) -> bool:
    if re.search(r"\b(?:extend|expand|wider|taller|letterbox)\b", text, re.I):
        return True
    return bool(re.search(
        r"\badd\b[^.!?\n]{0,25}?\b(?:canvas|border|space|margin)\b", text, re.I,
    ))


def _generative_kind(prompt: str) -> Optional[str]:
    """Classify a prompt as a generative content edit: 'remove'/'add'/'replace'.

    Returns None for tone/colour/filter/text/selection edits that deterministic
    editor ops handle correctly. 'remove the background' is deliberately not
    matched (that is the local remove-bg op).
    """
    text = str(prompt or "")
    if not text.strip():
        return None
    # Text/title, PSD and format requests are deterministic — never generative.
    if _is_text_request(text) or _PSD_RE.search(text) or _format_target(text):
        return None
    # An explicit brand-new image ("create/generate/make a new image/picture/logo
    # ...") is a text-to-image generation, never an edit of the current photo.
    if _is_new_image_request(text):
        return "add"
    # Replacing the background/scene (or putting the subject somewhere new) is
    # a generative replacement even though it is phrased like a background op.
    if _BG_RE.search(text):
        return "replace"
    if re.search(
        r"\b(?:remove|delete|erase|eliminate|get\s+rid\s+of)\s+(?:the\s+)?background\b",
        text,
        re.I,
    ):
        return None
    # Season/weather changes and explicit "generative fill" change content, not
    # tone. Only treat them as generative when the user is not asking to REMOVE
    # that element ("remove the rain" stays a removal).
    if _GENERATIVE_FILL_RE.search(text) or _SEASON_WEATHER_RE.search(text):
        if not re.search(
            r"\b(?:remove|delete|erase|eliminate|get\s+rid\s+of|take\s+out)\b", text, re.I
        ):
            return "replace"
    if _RESTYLE_RE.search(text) and not _is_new_image_request(text):
        # A whole-image artistic restyle is a model instruction edit (keep the
        # subject/composition), never a local tone/colour adjustment.
        return "restyle"
    if _REPLACE_RE.search(text):
        return "replace"
    if _ADD_RE.search(text):
        return "add"
    if _REMOVE_RE.search(text) or _REMOVE_CLEANUP_RE.search(text):
        return "remove"
    return None


_NEW_IMAGE_RE = re.compile(
    r"\b(?:create|generate|make|produce|draw|design)\b[^.!?\n]{0,30}?\b"
    r"(?:new\s+)?(?:image|picture|photo|illustration|artwork|graphic|logo|icon|wallpaper|poster)\b",
    re.I,
)
_EDIT_CONTEXT_RE = re.compile(
    r"\b(?:this|that|these|it|the\s+(?:photo|image|picture)|attached|from\s+this|"
    r"of\s+(?:this|the\s+photo)|edit|in\s+(?:this|the)\s+photo)\b",
    re.I,
)
_KEEP_CLAUSE_RE = re.compile(r"\bkeep\b|\bunchanged\b|\bpreserve\b|\bsame\b|\bidentical\b", re.I)


def _is_new_image_request(text: str) -> bool:
    """True only for a from-scratch image generation (not an edit of an existing photo)."""
    if not _NEW_IMAGE_RE.search(text):
        return False
    return not _EDIT_CONTEXT_RE.search(text)


def _instruction_prompt(text: str) -> str:
    """Append a keep-unchanged clause so instruction edits preserve the subject."""
    t = str(text or "").strip()
    if not t:
        return t
    if _KEEP_CLAUSE_RE.search(t):
        return t
    return (
        t.rstrip(".")
        + ". Keep the main subject and everything else in the image exactly the same."
    )


def _restyle_prompt(text: str) -> str:
    """Whole-image artistic restyle instruction (subject/composition preserved)."""
    t = str(text or "").strip()
    if t:
        base = (
            "Restyle the entire image as requested, applying the artistic style "
            "across the whole frame: " + t
        )
    else:
        base = "Restyle the entire image in a new artistic style."
    return _instruction_prompt(base)


def _generative_mode(kind: str, prompt: str) -> str:
    """'masked' only for an explicit regional removal of a clearly identified object.

    Everything else generative (background/season/style replacement, adding an
    object to the scene, removing diffuse distractions) uses 'instruction'.
    """
    if kind == "remove":
        text = str(prompt or "")
        if _REMOVE_RE.search(text) and not _REMOVE_CLEANUP_RE.search(text):
            return "masked"
    return "instruction"


def _removal_prompt(text: str) -> str:
    """A more explicit diffuse-removal instruction that names common clutter."""
    t = str(text or "").strip() or "Remove the distracting clutter from the photo"
    return (
        t.rstrip(". ")
        + ". Remove any distracting clutter such as roads, guardrails, utility poles, "
        "signs, wires and debris, and fill the cleared area with the surrounding "
        "landscape so it looks natural."
    )


# Rich, scene-specific descriptions so a background replacement produces a
# convincing scene (Qwen-Image-Edit is an instruction model and a bare "a beach"
# tends to yield a flat blue backdrop). Keyed by keywords in the request.
_SCENE_HINTS = (
    (r"beach|shore|seaside|coast|tropical",
     "a sunny tropical beach",
     "golden sand, turquoise sea with gentle waves, a clear blue sky with soft "
     "clouds, and natural daylight"),
    (r"mountain|alps|peak|valley",
     "a scenic mountain landscape",
     "distant peaks, natural daylight and soft haze"),
    (r"forest|woods|jungle",
     "a lush green forest",
     "dappled natural daylight and depth"),
    (r"city|street|urban|downtown",
     "a realistic city street",
     "recognisable city buildings and depth of field with natural perspective"),
    (r"field|meadow|grass",
     "an open grassy field",
     "natural daylight and a distant horizon"),
    (r"snow|winter",
     "a snow-covered landscape",
     "cold, soft daylight and a pale sky"),
    (r"desert|dunes",
     "a warm desert",
     "sand dunes and a clear sky"),
    (r"studio|plain|neutral|solid",
     "a clean neutral studio backdrop",
     "soft, even lighting"),
)
_SCENE_DEFAULT = (
    "a realistic natural background",
    "matching the subject's lighting, perspective and shadows",
)
# Season/weather scene descriptions ("make it snowy/autumn").
_SEASON_HINTS = (
    (r"snow|winter|snowy", "a snowy winter scene",
     "fresh snow covering the ground and surfaces, soft cold daylight, a pale overcast sky"),
    (r"autumn|autumnal|fall", "an autumn scene",
     "vivid orange and golden foliage, warm low sunlight and fallen leaves"),
    (r"spring", "a bright spring scene",
     "fresh green foliage and blossoms under bright natural daylight"),
    (r"rain|storm|overcast", "a rainy overcast scene",
     "wet reflective surfaces, raindrops and a dramatic overcast sky"),
    (r"fog|mist", "a foggy scene",
     "soft drifting mist and diffuse light"),
    (r"sky|clouds", "a new sky",
     "realistic clouds and natural lighting matching the scene"),
)


def _scene_prompt(text: str) -> str:
    """Deterministic, templated instruction for a background/scene change.

    Qwen-Image-Edit is an instruction model and a bare scene noun tends to yield
    a flat backdrop, so the scene is expanded into concrete detail. The result is
    passed to the provider unmodified.
    """
    t = str(text or "").strip()
    if not t:
        return t
    if _BG_RE.search(t):
        scene, details = _SCENE_DEFAULT
        for pattern, scene_name, description in _SCENE_HINTS:
            if re.search(pattern, t, re.I):
                scene, details = scene_name, description
                break
        return (
            f"Replace the entire background behind the person with {scene}: {details}. "
            "Keep the person exactly the same."
        )
    if _SEASON_WEATHER_RE.search(t) or _GENERATIVE_FILL_RE.search(t):
        scene, details = _SCENE_DEFAULT
        for pattern, scene_name, description in _SEASON_HINTS:
            if re.search(pattern, t, re.I):
                scene, details = scene_name, description
                break
        return (
            f"Change the scene to {scene}: {details}. "
            "Keep the main subject and everything else in the image exactly the same."
        )
    return t


def _generative_step(kind: str, prompt: str) -> Dict[str, Any]:
    """The AI step a generative intent must use instead of deterministic ops."""
    instruction = str(prompt or "").strip()
    if kind == "restyle":
        return {
            "op": "ai_edit_image",
            "params": {"op": "generative", "mode": "instruction",
                       "prompt": _restyle_prompt(instruction), "steps": 8},
        }
    if kind == "add":
        # A brand-new image is the only case for generate_image; adding an object
        # to an existing photo is an instruction edit of the whole image.
        if _is_new_image_request(prompt):
            return {"op": "generate_image", "params": {"prompt": instruction}}
        return {
            "op": "ai_edit_image",
            "params": {
                "op": "generative",
                "mode": "instruction",
                "prompt": _instruction_prompt(instruction),
                "steps": 8,
            },
        }
    mode = _generative_mode(kind, prompt)
    op = "inpaint" if kind == "remove" else "generative"
    if mode == "instruction":
        if kind == "remove":
            base = _removal_prompt(instruction)
        else:
            base = _scene_prompt(instruction)
        params: Dict[str, Any] = {"op": op, "mode": "instruction", "prompt": _instruction_prompt(base)}
    else:
        params = {"op": op, "mode": "masked", "prompt": instruction}
    if kind in ("remove", "replace"):
        # Removal and background-replacement edits need more sampler steps than
        # the server's default of 4.
        params["steps"] = 8
    return {"op": "ai_edit_image", "params": params}


_SKY_ADD_RE = re.compile(
    r"\b(?:sky|moons?|suns?|sunset|sunrise|clouds?|stars?|night|aurora|stars?)\b",
    re.I,
)
_REMOVE_NOUN_RE = re.compile(
    r"\b(?:remove|delete|erase|eliminate|get\s+rid\s+of|take\s+out|clean\s+up|declutter|clear)\b"
    r"[^.!?\n]{0,60}?\b("
    + _GEN_OBJECTS
    + r"|distractions?|clutter|junk|mess|noise|stuff|things)\b",
    re.I,
)


def _selection_step_for(kind: str, prompt: str) -> Optional[Dict[str, Any]]:
    """A deterministic selection step that yields the mask a MASKED ai_edit_image needs.

    Instruction-mode edits operate on the whole image and need no mask, so this
    returns None for them. Masked mode is only used for an explicit regional
    object removal; the selection targets that object so ``selection.exportMask``
    has a tight region to export.
    """
    text = str(prompt or "")
    if kind == "remove" and _generative_mode(kind, text) == "masked":
        m = _REMOVE_NOUN_RE.search(text)
        label = (m.group(1).lower() if m else "person")
        if label.endswith("s"):
            label = label[:-1]
        if label in ("people",):
            label = "person"
        return {"op": "selection.subject", "params": {"label": label}}
    return None


def _generative_steps(kind: str, prompt: str) -> List[Dict[str, Any]]:
    """The selection step (when needed) followed by the AI content step."""
    steps: List[Dict[str, Any]] = []
    selection = _selection_step_for(kind, prompt)
    if selection:
        steps.append(selection)
    steps.append(_generative_step(kind, prompt))
    return steps


def _plan_uses_ai_content(steps: Any) -> bool:
    return any(isinstance(s, dict) and s.get("op") in _AI_CONTENT_OPS for s in (steps or []))


def _ensure_generative(prompt: str, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Force a generative intent onto an AI content op, never a substitute.

    If the prompt is a generative content edit (remove/add/replace) but the
    plan has no ai_edit_image/generate_image step, replace the plan with the
    correct AI step so the agent cannot silently do an unrelated deterministic
    edit. Non-generative prompts are returned unchanged.
    """
    kind = _generative_kind(prompt)
    if not kind:
        return steps
    # Background/scene replacement is deterministic: always use the templated
    # scene prompt so a model rewrite cannot drop the scene details (a model
    # ai_edit_image step would otherwise pass with its own vague prompt).
    if kind == "replace" and (
        _BG_RE.search(str(prompt or ""))
        or _SEASON_WEATHER_RE.search(str(prompt or ""))
        or _GENERATIVE_FILL_RE.search(str(prompt or ""))
    ):
        return _generative_steps(kind, prompt)
    # For a restyle (and any other generative intent) the planner is expected to
    # choose generate_edit itself; the _RESTYLE_RE keyword list is only a fast
    # path used when the model did not already pick an AI content op.
    if not _plan_uses_ai_content(steps):
        return _generative_steps(kind, prompt)
    return steps


def _image_provider_available(owner: Optional[str]) -> bool:
    try:
        from src.ai_interaction import image_editing_provider_available

        return bool(image_editing_provider_available(owner))
    except Exception:
        return True  # unknown: let the tool attempt and report its own error


def _state_dims(state: Any) -> Optional[Tuple[int, int]]:
    """Current canvas size from the client-sent state (flat or nested)."""
    if not isinstance(state, dict):
        return None
    candidates = [state]
    for key in ("document", "document_info", "info"):
        if isinstance(state.get(key), dict):
            candidates.append(state[key])
    for src in candidates:
        w = src.get("width") or src.get("imgWidth")
        h = src.get("height") or src.get("imgHeight")
        try:
            if w and h:
                return (int(round(float(w))), int(round(float(h))))
        except Exception:
            continue
    return None


def _title_text(prompt: str) -> Optional[str]:
    quoted = re.search(r"[\"'\u201c\u201d\u2018\u2019]([^\"'\u201c\u201d\u2018\u2019]{1,40})[\"'\u201c\u201d\u2018\u2019]", prompt)
    if quoted and quoted.group(1).strip():
        return quoted.group(1).strip()
    caps = re.search(r"\b([A-Z][A-Z0-9]{1,20})\b", prompt)
    if caps and caps.group(1).lower() not in {"add", "title", "text", "psd"}:
        return caps.group(1)
    return None


def _crop_rect_for_aspect(cur: Tuple[int, int], target: Tuple[int, int]) -> Optional[Dict[str, int]]:
    cw, ch = cur
    tw, th = target
    if cw <= 0 or ch <= 0 or tw <= 0 or th <= 0:
        return None
    target_ratio = tw / th
    cur_ratio = cw / ch
    if abs(cur_ratio - target_ratio) < 0.02:
        return None  # already the right aspect
    if cur_ratio > target_ratio:
        # too wide -> trim the sides
        new_w = int(round(ch * target_ratio))
        new_w = min(cw, max(1, new_w))
        return {"x": (cw - new_w) // 2, "y": 0, "width": new_w, "height": ch}
    # too tall -> trim top/bottom
    new_h = int(round(cw / target_ratio))
    new_h = min(ch, max(1, new_h))
    return {"x": 0, "y": (ch - new_h) // 2, "width": cw, "height": new_h}


def _extend_dims(cur: Tuple[int, int], target: Tuple[int, int]) -> Tuple[int, int]:
    """Target canvas that grows (never crops) toward the target aspect."""
    cw, ch = cur
    target_ratio = target[0] / target[1]
    cur_ratio = cw / ch
    if abs(cur_ratio - target_ratio) < 0.02:
        return (cw, ch)
    if cur_ratio < target_ratio:
        return (int(round(ch * target_ratio)), ch)  # add width
    return (cw, int(round(cw / target_ratio)))  # add height


def _plan_text_steps(prompt: str) -> List[Dict[str, Any]]:
    title = _title_text(prompt) or "Title"
    steps: List[Dict[str, Any]] = [{
        "op": "text.add",
        "params": {"text": title, "x": 40, "y": 40, "color": "#ffffff", "size": 64},
    }]
    if _STYLE_RE.search(prompt):
        steps.append({"op": "layers.addStyle", "params": {"type": "drop-shadow", "params": {}}})
    return steps


def _plan_format_steps(prompt: str, state: Any, provider_available: bool) -> Optional[List[Dict[str, Any]]]:
    target = _format_target(prompt)
    if not target:
        return None
    cur = _state_dims(state)
    if not cur:
        # No canvas size known: ask the client to crop to the aspect itself.
        return None
    steps: List[Dict[str, Any]] = []
    if _extend_requested(prompt):
        new_w, new_h = _extend_dims(cur, target)
        if (new_w, new_h) == cur:
            return None
        if provider_available:
            steps.append({"op": "ai.generativeExpand", "params": {
                "width": new_w, "height": new_h,
                "anchor": "center", "mode": "instruction", "feather": True,
                "steps": 8,
                "prompt": "extend the surrounding scene and sky naturally into the new border, "
                          "keep the original photo unchanged"}})
        else:
            steps.append({"op": "document.resizeCanvas", "params": {
                "width": new_w, "height": new_h, "anchor": "center"}})
    else:
        rect = _crop_rect_for_aspect(cur, target)
        if rect:
            steps.append({"op": "document.crop", "params": rect})
        # Only resize when an exact output size was requested.
        if abs(target[0] / target[1] - cur[0] / cur[1]) >= 0.02 or target in ((1080, 1350), (1080, 1080), (1080, 1920), (1920, 1080)):
            steps.append({"op": "document.resizeCanvas", "params": {
                "width": target[0], "height": target[1], "anchor": "center"}})
    if not steps:
        return None
    # Optional caption/title that came with the format request.
    if _is_text_request(prompt):
        steps.extend(_plan_text_steps(prompt))
    return steps


def _plan_psd_steps(prompt: str) -> Optional[List[Dict[str, Any]]]:
    if _PSD_RE.search(prompt):
        return [{"op": "document.savePsd", "params": {}}]
    return None


def _is_background_removal_intent(prompt: str) -> bool:
    """Linear-time, bounded intent parser for background removal commands.

    Replaces the previous backtracking regular expression flagged by CodeQL
    (#1212) while preserving the exact command grammar.
    """
    if not prompt or not isinstance(prompt, str):
        return False
    i = len(prompt)
    while i > 0 and prompt[i - 1] in ".! \t\r\n":
        i -= 1
    cleaned = prompt[:i].strip()
    if not cleaned:
        return False

    tokens = cleaned.split()
    if not tokens:
        return False

    idx = 0
    if tokens[idx].lower() == "please":
        idx += 1
        if idx >= len(tokens):
            return False

    if tokens[idx].lower() not in {"remove", "erase", "delete", "clear", "strip"}:
        return False
    idx += 1
    if idx >= len(tokens):
        return False

    if tokens[idx].lower() == "the":
        idx += 1
        if idx >= len(tokens):
            return False

    if tokens[idx].lower() not in {"bg", "background", "backdrop"}:
        return False
    idx += 1

    if idx == len(tokens):
        return True

    if tokens[idx].lower() in {"from", "of"}:
        idx += 1
        if idx < len(tokens) and tokens[idx].lower() == "the":
            idx += 1
        if (
            idx + 1 < len(tokens)
            and tokens[idx].lower() in {"active", "current", "selected"}
            and tokens[idx + 1].lower() == "layer"
            and idx + 2 == len(tokens)
        ):
            return True

    return False


def _plan_special(
    prompt: str, state: Any, provider_available: bool,
) -> Optional[Tuple[List[Dict[str, Any]], str, str]]:
    """Deterministic overrides for intents the model reliably botches.

    Canvas format/crop/extend, text + layer styles and PSD export must not be
    left to the model.     Returns (steps, reply, note) or None. These never need an
    image provider except generative border fill, used only when one exists.
    """
    if _is_background_removal_intent(prompt):
        return [{"op": "ai.removeBackground", "params": {}}], "Removing the active layer's background.", ""
    # Targeted recolour is fully deterministic (object mask + masked hue shift)
    # and must never be left to the model, which sometimes substitutes a
    # generative placeImage. Highest priority so it can never vary.
    recolor = style_guide.recolor_plan(prompt)
    if recolor:
        return list(recolor["steps"]), str(recolor["reply"]), ""
    if _format_target(prompt):
        steps = _plan_format_steps(prompt, state, provider_available)
        if not steps:
            return None
        target = _format_target(prompt)
        reply = f"Setting the canvas to {target[0]}\u00d7{target[1]}."
        note = ""
        if _extend_requested(prompt) and not provider_available:
            note = ("No image generation provider is configured, so the new border "
                    "was extended with edge/transparent pixels.")
        return steps, reply, note
    if _PSD_RE.search(prompt):
        steps = _plan_psd_steps(prompt)
        return (steps, "Saving a layered PSD.", "") if steps else None
    if _is_text_request(prompt):
        steps = _plan_text_steps(prompt)
        if steps:
            reply = ("Adding the title with a drop shadow."
                     if _STYLE_RE.search(prompt) else "Adding the title text.")
            return steps, reply, ""
    # Structural ops (fill, text edit, align, rotate/flip, copy/paste, flatten,
    # merge-down, colour overlay) — the guide's deterministic routing so the
    # model never substitutes a generic adjustment/transform for them.
    structural = style_guide.structural_plan(prompt)
    if structural:
        return list(structural["steps"]), str(structural["reply"]), ""
    return None


_DIMS_CLAIM_RE = re.compile(r"(\d{2,5})\s*[x\u00d7]\s*(\d{2,5})")
_CANVAS_CHANGE_OPS = frozenset({
    "document.crop", "document.resizeCanvas", "ai.generativeExpand", "document.importImage",
})
_TEXT_OPS = frozenset({"text.add", "layers.addStyle", "document.savePsd"})


def _step_op_signature(step: Any) -> str:
    """Identify an executed step by op plus its adjustment type / filter id."""
    if not isinstance(step, dict):
        return ""
    op = str(step.get("op") or "")
    params = step.get("params") or {}
    if op in ("adjustments.apply", "adjustments.applyMasked"):
        return f"{op}:{params.get('type') or ''}"
    if op in ("filters.apply", "filters.applyMasked"):
        return f"{op}:{params.get('id') or ''}"
    return op


# Phrases that name a specific edit. If the reply claims one of these but the
# executed steps do not contain it, the reply is rewritten from the real steps.
_OP_CLAIM_KEYWORDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("photo.autoTone", ("auto tone", "auto-tone", "autotone", "auto-tune")),
    ("adjustments.apply:shadows-highlights", ("shadow", "highlight")),
    ("adjustments.apply:white-balance", ("white balance", "white-balance",
                                         "temperature", "colour cast", "color cast")),
    ("adjustments.apply:vibrance", ("vibrance",)),
    ("adjustments.apply:brightness-contrast", ("brightness", "contrast")),
    ("adjustments.apply:black-white", ("black and white", "black & white",
                                       "grayscale", "greyscale", "monochrome")),
    ("filters.apply:clarity", ("clarity", "local contrast")),
    ("filters.apply:vignette", ("vignette",)),
    ("filters.apply:sharpen", ("sharpen", "crisper")),
    ("retouch.skin", ("skin", "complexion")),
    ("retouch.teeth", ("teeth", "smile")),
]

_OP_LABELS: Dict[str, str] = {
    "photo.autoTone": "auto tone",
    "adjustments.apply:shadows-highlights": "a shadow lift (highlights protected)",
    "adjustments.apply:white-balance": "white balance",
    "adjustments.apply:vibrance": "vibrance",
    "adjustments.apply:brightness-contrast": "brightness/contrast",
    "adjustments.apply:black-white": "black and white",
    "filters.apply:clarity": "local contrast (clarity)",
    "filters.apply:vignette": "a light vignette",
    "filters.apply:sharpen": "sharpening",
    "selection.face": "a face selection",
    "selection.background": "a background selection",
    "retouch.skin": "gentle skin retouching",
    "retouch.teeth": "teeth whitening",
}


def _reply_from_ops(steps: List[Dict[str, Any]]) -> str:
    """Describe what was actually executed, never what was planned."""
    labels: List[str] = []
    for step in steps or []:
        sig = _step_op_signature(step)
        label = _OP_LABELS.get(sig)
        if label is None:
            label = sig.split(":")[0] or "an edit"
        if label not in labels:
            labels.append(label)
    if not labels:
        return "Done."
    return "Done \u2014 I applied " + ", ".join(labels) + "."


def _reply_matches_ops(planned_reply: str, steps: List[Dict[str, Any]]) -> bool:
    """True when the reply does not claim an edit that was not executed."""
    text = (planned_reply or "").lower()
    if not text:
        return True
    # "drop shadow" is a layer style, not a shadows/highlights adjustment.
    text = text.replace("drop shadow", "drop-shadow-style")
    executed = {_step_op_signature(step) for step in (steps or [])}
    for sig, keywords in _OP_CLAIM_KEYWORDS:
        if sig in executed:
            continue
        if any(keyword in text for keyword in keywords):
            return False
    return True



def _verified_reply(
    prompt: str,
    steps: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    final_state: Any,
    planned_reply: str,
) -> str:
    """Build the final reply from ACTUAL executed results + final document state.

    Never let a plan's optimistic prose claim an edit that did not happen.
    """
    failed = [
        r for r in (results or [])
        if isinstance(r, dict) and r.get("ok") is False
    ]
    if failed:
        op = str(failed[0].get("op") or "step")
        err = failed[0].get("error") or "it did not complete"
        return (f"I couldn't finish the {op} step ({err}). "
                f"Nothing else was left changed — the image is back as it was.")
    dims = _state_dims(final_state)
    step_ops = [str(s.get("op") or "") for s in (steps or []) if isinstance(s, dict)]
    claim = _DIMS_CLAIM_RE.search(planned_reply or "")
    if claim and dims:
        try:
            claimed = (int(claim.group(1)), int(claim.group(2)))
        except Exception:
            claimed = None
        if claimed and (abs(dims[0] - claimed[0]) > 2 or abs(dims[1] - claimed[1]) > 2):
            if not any(op in _CANVAS_CHANGE_OPS for op in step_ops):
                return (f"I planned to make it {claimed[0]}\u00d7{claimed[1]}, but the canvas is "
                        f"still {dims[0]}\u00d7{dims[1]} — the resize didn't apply. "
                        f"Please try again in the editor.")
    if not steps and not results:
        return planned_reply or "I didn't have a concrete edit to apply."
    if not _is_text_request(prompt) and not _reply_matches_ops(planned_reply, steps):
        return _reply_from_ops(steps)
    if _is_text_request(prompt) and any(op in _TEXT_OPS for op in step_ops) and not failed:
        return planned_reply or "Added the text."
    if dims and any(op in _CANVAS_CHANGE_OPS for op in step_ops):
        return (planned_reply or f"Done — the canvas is now {dims[0]}\u00d7{dims[1]}.")
    return planned_reply


def set_editor_planner(
    fn: Optional[Callable[[List[dict], Optional[str]], Any]],
) -> None:
    global _EDITOR_PLANNER
    _EDITOR_PLANNER = fn


_ROUTING_GUIDANCE = (
    "Routing rules (follow strictly): removing objects/people, adding new content "
    "(e.g. a moon, an animal, a sign), or replacing/changing what is in the picture "
    "are GENERATIVE edits -> use op \"ai_edit_image\" (op \"inpaint\" to remove, "
    "\"generative\" to replace, with a descriptive prompt) or op \"generate_image\" "
    "({prompt}) for added content. NEVER approximate these with selection.rectangle, "
    "adjustments.apply, filters.apply, transform.* or any other deterministic op. "
    "Use deterministic ops only for tone/colour/filter/crop/resize/text/selection "
    "edits that do not invent or delete picture content. \"remove the background\" "
    "is op \"ai_edit_image\" op \"remove-bg\". If a generative edit is requested and "
    "no image provider is configured, do NOT substitute a deterministic edit: return "
    "{\"reply\": \"" + _PROVIDER_REQUIRED_MESSAGE + "\", \"steps\": []}."
)

# Strict ops vocabulary for the model (the schema is also sent as a structured
# output constraint, so the model can only emit these ops).
_SCHEMA_NOTE = (
    "Reply with ONLY a JSON object matching the ops schema. Valid step ops are the "
    "deterministic editor ops above plus: \"generate_edit\" (a model image edit; "
    "params {\"prompt\": str, \"mode\": \"instruction\"|\"masked\", \"mask\": optional "
    "object description}) for any edit that invents, removes, replaces, recolours or "
    "restyles image content; \"select_object\" (params {\"text\": object description}) "
    "to select an object by description; and \"ask_user\" (params {\"question\": str, "
    "\"options\": [2-3 short choices]}) when the request is ambiguous. For "
    "\"generate_edit\" set params {\"prompt\": str, \"mode\": \"instruction\"|\"masked\"} "
    "and do NOT set an inner \"op\" field: the server maps instruction/restyle edits to "
    "the image model's \"generative\" op and masked region edits to \"inpaint\". Prefer "
    "generate_edit over deterministic approximations for content changes, and ask_user "
    "with 2-3 concrete choices rather than guessing a tone tweak."
)

_SYSTEM_PROMPT = (
    "You are a photo-editor planning agent. Convert the user's request into a JSON plan of "
    "editor operations. Return ONLY JSON: {\"reply\": str, \"steps\": [{\"op\": str, \"params\": {}}]}. "
    "Available ops: " + ", ".join(OP_CATALOG) + ", plus ai_edit_image and generate_image. "
    + _ROUTING_GUIDANCE + " "
    + _CANONICAL_GUIDANCE + " " + _STYLE_GUIDANCE + " "
    "Use selection.* before targeted edits when possible. Keep to <=6 steps. "
    "If a screenshot is attached, use it to judge the current result. "
    + _SCHEMA_NOTE
)

_CHECK_SYSTEM_PROMPT = (
    "You are a photo-editor quality checker. A screenshot of the editor's CURRENT result is "
    "attached along with the user's request and the iteration number. Decide whether the edit "
    "satisfies the request. Return ONLY JSON: if it is complete, {\"done\": true, \"reply\": str}; "
    "if more work is needed, {\"done\": false, \"reply\": str, \"steps\": [{\"op\": str, \"params\": {}}]}. "
    "Available ops: " + ", ".join(OP_CATALOG) + ", plus ai_edit_image and generate_image. "
    + _ROUTING_GUIDANCE + " "
    + _CANONICAL_GUIDANCE + " " + _STYLE_GUIDANCE + " "
    "Never repeat steps that already ran; <=4 steps. "
    + _SCHEMA_NOTE
)


_GENERIC_TONE_OPS = frozenset({"photo.autoTone", "adjustments.apply", "filters.apply"})


def _plan_is_generic_tone_only(steps: Any) -> bool:
    """True when a plan is nothing but whole-image tone/colour/filter steps."""
    items = [s for s in (steps or []) if isinstance(s, dict)]
    return bool(items) and all(s.get("op") in _GENERIC_TONE_OPS for s in items)


def _unsure_plan(prompt: str, plan: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Demote a bare whole-image tone plan to an ask when the prompt is unsure.

    When the planner produced ONLY generic tone ops (photo.autoTone /
    adjustments.apply / filters.apply) for a prompt that matches no concrete
    intent and names no concrete defect, the editor is guessing ("it just adds
    vibrance and brightness"). Ask the user with 2-3 concrete choices instead.
    """
    if not isinstance(plan, dict):
        return plan
    steps = plan.get("steps") or []
    if not _plan_is_generic_tone_only(steps):
        return plan
    if _generative_kind(prompt):
        return plan
    if style_guide.has_concrete_intent(prompt):
        return plan
    return style_guide.ask_user_plan()


def _fallback_plan(prompt: str) -> Dict[str, Any]:
    kind = _generative_kind(prompt)
    if kind:
        label = {"remove": "object removal", "add": "content addition",
                 "replace": "content replacement", "restyle": "artistic restyle"}[kind]
        return {
            "reply": f"Planned an AI {label}.",
            "planner": "fallback",
            "steps": _generative_steps(kind, prompt),
        }
    guided = style_guide.fallback_plan(prompt)
    if guided is not None:
        guided["steps"] = style_guide.enforce_guards(guided.get("steps") or [])
        return guided
    # Nothing mapped to a concrete intent: ASK instead of silently applying a
    # default tone tweak. The old fallback emitted a generic
    # photo.autoTone/adjustments/filters stack here, which is the "it just adds
    # vibrance and brightness" bug for unrecognized prompts.
    return style_guide.ask_user_plan()


def _parse_plan(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        data = raw
    else:
        text = raw if isinstance(raw, str) else ""
        if not text:
            return None
        try:
            data = json.loads(text)
        except Exception:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                data = json.loads(text[start:end + 1])
            except Exception:
                return None
    # Every plan is validated against the strict ops schema; an invalid or empty
    # plan returns None so the caller falls back to ask_user (never a tone
    # default). See _validate_plan.
    return _validate_plan(data)


def _normalize_steps(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(body.get("steps"), list):
        raw = body["steps"]
    elif body.get("op"):
        raw = [{"op": body["op"], "params": body.get("params") or {}}]
    else:
        raw = []
    out: List[Dict[str, Any]] = []
    for step in raw[:_MAX_STEPS]:
        if not isinstance(step, dict):
            continue
        op = str(step.get("op") or "")
        if op not in _OP_SET:
            continue
        params = step.get("params")
        out.append({"op": op, "params": params if isinstance(params, dict) else {}})
    return out


async def _json_object(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Expected a JSON object")
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected a JSON object")
    return body


def _prompt(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 4096:
        raise HTTPException(413, "Editor prompt is too long")
    return text


def _bridge_timeout(value: Any) -> float:
    try:
        timeout = float(value or _BRIDGE_DEFAULT_TIMEOUT)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(400, "Invalid bridge timeout")
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 900:
        raise HTTPException(400, "Invalid bridge timeout")
    return timeout


def _is_admin(request: Request) -> bool:
    """Return True only for authorized administrator browser sessions or auth-disabled single-user mode.

    Delegated credentials (bearer API tokens) are explicitly rejected:
    per src/tool_security.py, bearer tokens are long-lived credentials handed to
    third parties/scripts and must remain capped at non-admin policies even
    when minted by an administrator.
    """
    # 1. Delegated credentials (bearer API tokens) must NEVER receive admin metadata privileges
    if getattr(request.state, "api_token", False):
        return False

    # 2. In-process internal tool bypass (loopback agent tools)
    try:
        from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN, INTERNAL_TOOL_USER
        import secrets
        hdr = request.headers.get(INTERNAL_TOOL_HEADER)
        if hdr and secrets.compare_digest(hdr, INTERNAL_TOOL_TOKEN):
            return True
        if getattr(request.state, "current_user", None) == INTERNAL_TOOL_USER:
            return True
    except Exception:
        pass

    # 3. Explicitly auth-disabled single-user operation (AUTH_ENABLED=false)
    try:
        from src.auth_helpers import _auth_disabled
        if _auth_disabled():
            return True
    except Exception:
        pass

    # 4. Middleware require_admin
    try:
        from core.middleware import require_admin
        require_admin(request)
        return True
    except Exception:
        pass

    # 5. Direct session/user check when request.state.current_user is set
    try:
        auth_mgr = getattr(request.app.state, "auth_manager", None)
        if auth_mgr and getattr(auth_mgr, "is_configured", False):
            from src.auth_helpers import get_current_user
            user = get_current_user(request) or getattr(request.state, "current_user", None)
            if user and getattr(auth_mgr, "is_admin", None) and auth_mgr.is_admin(user):
                return True
    except Exception:
        pass

    return False


def _user(request: Request) -> Optional[str]:
    # Handle API bearer tokens explicitly: require 'chat' scope and attribute to real owner
    if getattr(request.state, "api_token", False):
        from src.auth_helpers import require_api_token_scope
        return require_api_token_scope(request, "chat")
    from src.auth_helpers import get_current_user, _auth_disabled
    u = get_current_user(request)
    if u:
        return u
    if _auth_disabled():
        return None
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if auth_mgr is not None and getattr(auth_mgr, "is_configured", False):
        raise HTTPException(401, "Not authenticated")
    client = getattr(request, "client", None)
    host = (client.host if client else "") or ""
    if host in ("127.0.0.1", "::1", "localhost", "testclient") or not host:
        return None
    raise HTTPException(401, "Not authenticated")


async def _default_editor_planner(messages: List[dict], owner: Optional[str]) -> Optional[str]:
    """Plan with the local editor-agent model, falling back through the chain.

    Order: per-request ``agent_model`` override -> local Qwen3.5-9B (vision,
    screenshot attached) -> the same model text-only with a VL caption of the
    screenshot -> the hosted default/utility chat model. The model actually
    used is recorded so the route can report it.
    """
    result = await editor_agent_model.plan_with_editor_agent(
        messages, owner, override=editor_agent_model.get_override(),
        response_format=PLAN_RESPONSE_FORMAT,
    )
    if result is None:
        return None
    raw, model_used, source = result
    editor_agent_model.set_last_model(model_used, source)
    return raw


def setup_editor_agent_routes() -> APIRouter:
    router = APIRouter(tags=["editor-agent"])

    if _EDITOR_PLANNER is None:
        set_editor_planner(_default_editor_planner)

    # scoped session -> list of that session's live subscriber queues; id -> result future.
    # A command is BROADCAST to every live subscriber (per-connection queue), never to a
    # single shared queue: EventSource reconnects overlap, and with one shared queue a
    # command could be consumed by a stale subscriber while the active page got nothing.
    subscribers: Dict[Tuple[Optional[str], str], List["asyncio.Queue[dict]"]] = {}
    backlog: Dict[Tuple[Optional[str], str], List[dict]] = {}
    pending: Dict[str, Tuple[Optional[str], asyncio.Future]] = {}
    max_pending = 1024

    def _subscribe(session: str) -> "asyncio.Queue[dict]":
        q: "asyncio.Queue[dict]" = asyncio.Queue(maxsize=_QUEUE_MAX)
        subscribers.setdefault(session, []).append(q)
        # Replay anything enqueued while this session had no live subscriber.
        for cmd in backlog.pop(session, []) or []:
            try:
                q.put_nowait(cmd)
            except asyncio.QueueFull:
                break
        return q

    def _unsubscribe(session: str, q: "asyncio.Queue[dict]") -> None:
        subs = subscribers.get(session)
        if subs and q in subs:
            subs.remove(q)
        if not subs:
            subscribers.pop(session, None)

    def _broadcast(session: str, cmd: dict) -> int:
        subs = subscribers.get(session) or []
        if subs:
            for q in list(subs):
                try:
                    q.put_nowait(cmd)
                except asyncio.QueueFull:
                    pass
            return len(subs)
        # No live subscriber yet: hold it so a subscriber that connects shortly
        # after still receives the command (bounded ring buffer).
        bl = backlog.setdefault(session, [])
        bl.append(cmd)
        if len(bl) > _QUEUE_MAX:
            del bl[0: len(bl) - _QUEUE_MAX]
        return 0

    def _expire_command(session, cid, fut):
        entry = pending.get(cid)
        if entry is not None and entry[1] is fut:
            pending.pop(cid, None)
            if not fut.done():
                fut.cancel()
        queued = backlog.get(session)
        if queued:
            backlog[session] = [cmd for cmd in queued if cmd.get("id") != cid]
            if not backlog[session]:
                backlog.pop(session, None)

    def _scoped(session: Optional[str], owner: Optional[str]) -> Tuple[Optional[str], str]:
        # Usernames and session ids may both contain colons.
        return (owner, str(session or 'default'))

    def _scope_label(scope: Tuple[Optional[str], str]) -> str:
        return f"{scope[0] or 'anon'}:{scope[1]}"

    @router.post("/api/editor/plan")
    async def plan(request: Request) -> JSONResponse:
        owner = _user(request)
        body = await _json_object(request)
        prompt = _prompt(body.get("prompt"))
        state = body.get("state") or {}
        mode = str(body.get("mode") or "plan").strip().lower()
        screenshot = body.get("screenshot")
        agent_model = str(body.get("agent_model") or "").strip()
        if not prompt and mode != "check":
            return JSONResponse({"reply": "No prompt provided.", "planner": "none", "steps": [],
                                 "agent_model": agent_model or editor_agent_model.local_model()})

        system = _CHECK_SYSTEM_PROMPT if mode == "check" else _SYSTEM_PROMPT
        text = json.dumps({"prompt": prompt, "state": state}, ensure_ascii=False)
        content: Any = text
        if isinstance(screenshot, str) and screenshot.startswith("data:image/"):
            content = [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": screenshot}},
            ]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]

        raw: Any = None
        planner = _EDITOR_PLANNER
        editor_agent_model.set_override(agent_model)
        editor_agent_model.set_last_model("", "")
        if planner is not None:
            try:
                raw = planner(messages, owner)
                if inspect.isawaitable(raw):
                    raw = await raw
            except Exception:
                raw = None
        _used = editor_agent_model.get_last_model()
        parsed = _parse_plan(raw)
        if _used and _used[0]:
            model_used = _used[0]
        elif parsed:
            model_used = agent_model or editor_agent_model.local_model()
        else:
            model_used = "fallback" if agent_model else editor_agent_model.local_model()
        if isinstance(parsed, dict):
            parsed["agent_model"] = model_used
        if mode != "check":
            _special = _plan_special(prompt, state, _image_provider_available(owner))
            if _special:
                _special_steps, _special_reply, _ = _special
                return JSONResponse({
                    "reply": _special_reply,
                    "planner": "deterministic",
                    "steps": style_guide.enforce_guards(_special_steps) or _special_steps,
                    "agent_model": model_used,
                })
        if parsed and mode != "check":
            _strengthened = style_guide.strengthen(prompt, list(parsed.get("steps") or []))
            if _strengthened != parsed.get("steps"):
                parsed["steps"] = _strengthened
                parsed["planner"] = str(parsed.get("planner") or "llm") + "+guide"
            # Keep /plan and /agent consistent: a generative intent must never be
            # left on a deterministic substitute (e.g. a model returning only
            # selection/adjustment steps for 'remove the person on the left').
            _generative = _ensure_generative(prompt, list(parsed.get("steps") or []))
            if _generative != parsed.get("steps"):
                parsed["steps"] = _generative
                parsed["planner"] = str(parsed.get("planner") or "llm") + "+generative"
            # Same safety guards the /agent path applies, so /plan never returns
            # a stacked-lift or a global adjustment after a masked recolour.
            parsed["steps"] = style_guide.enforce_guards(list(parsed.get("steps") or []))
            # A bare whole-image tone plan for an unspecified prompt is a guess:
            # ask the user instead of silently tweaking.
            _maybe = _unsure_plan(prompt, parsed)
            if _maybe is not parsed and isinstance(_maybe, dict):
                _maybe["agent_model"] = model_used
                parsed = _maybe

        if mode == "check":
            # Never let a self-check re-plan substitute a generative placeImage
            # for a deterministic recolour; re-issue the masked-hue plan instead.
            _recolor = style_guide.recolor_plan(prompt)
            if _recolor and _plan_uses_ai_content(list((parsed or {}).get("steps") or [])):
                return JSONResponse({
                    "reply": str(_recolor["reply"]),
                    "planner": "deterministic",
                    "steps": style_guide.enforce_guards(list(_recolor["steps"])),
                    "agent_model": model_used,
                })
            if parsed and (parsed.get("done") or parsed.get("steps")):
                return JSONResponse(parsed)
            return JSONResponse({
                "done": True,
                "reply": (parsed or {}).get("reply") or "The edit looks complete.",
                "planner": (parsed or {}).get("planner") or "check",
                "steps": [],
                "agent_model": model_used,
            })
        if parsed:
            return JSONResponse(parsed)
        _fallback = _fallback_plan(prompt)
        _fallback["agent_model"] = model_used
        return JSONResponse(_fallback)

    @router.post("/api/editor/bridge/command")
    async def bridge_command(request: Request) -> JSONResponse:
        owner = _user(request)
        body = await _json_object(request)
        session = _scoped(body.get("session"), owner)
        steps = _normalize_steps(body)
        wait = bool(body.get("wait"))
        timeout = _bridge_timeout(body.get("timeout"))
        ids: List[str] = []
        if len(pending) + len(steps) > max_pending:
            raise HTTPException(429, "Too many pending editor commands")
        for step in steps:
            cid = uuid.uuid4().hex
            ids.append(cid)
            fut = asyncio.get_event_loop().create_future()
            pending[cid] = (owner, fut)
            _broadcast(session, {"id": cid, "op": step["op"], "params": step["params"]})

        if wait and len(steps) == 1:
            cid = ids[0]
            entry = pending.get(cid)
            fut = entry[1]
            try:
                res = await asyncio.wait_for(fut, timeout=timeout)
                if isinstance(res, dict):
                    res.setdefault("status", "completed" if res.get("ok") else "failed")
                    res.setdefault("completed", bool(res.get("ok")))
                    return JSONResponse(res)
                return JSONResponse({"id": cid, "ok": True, "status": "completed", "completed": True, "result": res})
            except asyncio.TimeoutError:
                return JSONResponse({"ok": False, "error": "timeout", "status": "timed_out", "completed": False, "id": cid}, status_code=504)
            finally:
                _expire_command(session, cid, fut)
        else:
            for cid in ids:
                entry = pending.get(cid)
                if entry is not None:
                    asyncio.get_running_loop().call_later(timeout, _expire_command, session, cid, entry[1])

        return JSONResponse({
            "queued": ids,
            "session": _scope_label(session),
            "status": "queued",
            "completed": False,
            "ok": False,
        })

    @router.get("/api/editor/bridge/events")
    async def bridge_events(request: Request, session: str = "default") -> StreamingResponse:
        owner = _user(request)
        scoped = _scoped(session, owner)

        async def gen():
            q = _subscribe(scoped)
            try:
                yield ": connected\n\n"
                while not _shutdown.is_shutting_down():
                    try:
                        cmd = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(cmd)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                _unsubscribe(scoped, q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/api/editor/bridge/result")
    async def bridge_result(request: Request) -> JSONResponse:
        owner = _user(request)
        body = await _json_object(request)
        cid = str(body.get("id") or "")
        entry = pending.get(cid)
        if entry is None:
            return JSONResponse({"ok": True})
        if entry[0] != owner:
            return JSONResponse({"ok": False, "error": "unknown command"}, status_code=404)
        _, fut = entry
        if fut is not None and not fut.done():
            is_ok = bool(body.get("ok"))
            fut.set_result({
                "id": cid,
                "ok": is_ok,
                "status": "completed" if is_ok else "failed",
                "completed": is_ok,
                "result": body.get("result"),
                "error": body.get("error"),
                "screenshot": body.get("screenshot"),
            })
        return JSONResponse({"ok": True})

    @router.post("/api/editor/bridge/await")
    async def bridge_await(request: Request) -> JSONResponse:
        owner = _user(request)
        body = await _json_object(request)
        cid = str(body.get("id") or "")
        timeout = _bridge_timeout(body.get("timeout"))
        entry = pending.get(cid)
        if entry is None or entry[0] != owner:
            return JSONResponse({"ok": False, "error": "unknown command", "status": "unknown", "completed": False}, status_code=404)
        fut = entry[1]
        try:
            result = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
            if pending.get(cid) == (owner, fut):
                pending.pop(cid, None)
            res = dict(result) if isinstance(result, dict) else {"result": result, "ok": True}
            res.setdefault("status", "completed" if res.get("ok") else "failed")
            res["completed"] = bool(res.get("ok"))
            return JSONResponse(res)
        except asyncio.TimeoutError:
            return JSONResponse({"ok": False, "error": "timeout", "status": "timed_out", "completed": False, "id": cid})
        except asyncio.CancelledError:
            return JSONResponse({"ok": False, "error": "cancelled", "status": "cancelled", "completed": False, "id": cid})

    _AGENT_SYSTEM_PROMPT = (
        "You are the photo editor agent. You drive the open image editor and "
        "may also run AI image operations. Available editor ops: "
        + ", ".join(OP_CATALOG)
        + ", plus ai_edit_image and generate_image. "
        "Respond with ONLY a JSON object: {\"reply\": <short text>, "
        "\"steps\": [{\"op\": <string>, \"params\": {...}}]}. "
        + _ROUTING_GUIDANCE + " "
        + _CANONICAL_GUIDANCE + " " + _STYLE_GUIDANCE + " "
        "Prefer AI content ops for generative requests: \"remove the person on the left\" "
        "-> op \"ai_edit_image\" op \"inpaint\" with a prompt; \"add a full moon in the sky\" "
        "-> op \"generate_image\" with a prompt. "
        "Use deterministic editor ops (adjustments.apply, filters.apply, selection.*, "
        "layers.placeImage, text.add, ...) only for non-generative, precise edits. "
        "Keep steps minimal and ordered. "
        + _SCHEMA_NOTE
    )

    def _scoped_editor_tools() -> List[str]:
        """The exact tool list offered to the model for a photo_editor turn."""
        fallback = [
            "plan_edit", "run_editor_command", "get_editor_screenshot",
            "ai_edit_image", "generate_image", "ask_user",
        ]
        try:
            from src.tool_policy import ToolPolicy
            from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
            from src.turn_contract import resolve_turn_contract

            contract = resolve_turn_contract(
                capabilities={"photo_editor"},
                schemas=FUNCTION_TOOL_SCHEMAS,
                policy=ToolPolicy(),
                required_capabilities={"photo_editor"},
            )
            return sorted(contract.offered) or fallback
        except Exception:
            return fallback

    @router.post("/api/editor/agent")
    async def editor_agent(request: Request) -> JSONResponse:
        """Scoped photo_editor agent turn (Quick Edit).

        Offers ONLY the editor tool family plus ask_user, plans edits, and
        drives the open editor through the owner-scoped bridge. AI edit and
        generation actions land as new layers; the original is never
        overwritten.
        """
        owner = _user(request)
        body = await _json_object(request)
        from src.settings import get_setting
        disabled = get_setting("disabled_tools", []) or []
        if not isinstance(disabled, (list, tuple, set)):
            disabled = []
        if "plan_edit" in disabled:
            raise HTTPException(403, "Editor tool is disabled")
        prompt = _prompt(body.get("prompt"))
        session = str(body.get("editor_session") or body.get("session") or "default")
        state = body.get("state") if isinstance(body.get("state"), dict) else {}
        screenshot = body.get("screenshot")
        # Optional user-picked image model (from the editor model selector).
        model_choice = str(body.get("model") or body.get("image_model") or "").strip()
        generation_model_choice = str(body.get("generation_model") or "").strip()
        use_image_settings = body.get("_use_image_settings") is True
        if use_image_settings:
            from src.editor_image_settings import editor_image_models
            image_models = editor_image_models(owner=owner)
            model_choice = image_models["edit"]
            generation_model_choice = image_models["generation"]
        # Optional user-picked PLANNER model (the editor agent/planner model).
        agent_model = str(body.get("agent_model") or "").strip()
        tools_offered = [tool for tool in _scoped_editor_tools() if tool not in disabled]
        scoped = _scoped(session, owner)
        trace: List[Dict[str, Any]] = [
            {"type": "session", "session": _scope_label(scoped), "tools_offered": tools_offered},
        ]
        if not prompt:
            return JSONResponse({
                "ok": False, "reply": "No prompt provided.",
                "tools_offered": tools_offered, "steps": [], "trace": trace,
                "agent_model": agent_model or editor_agent_model.local_model(),
            })
        text = json.dumps({"prompt": prompt, "state": state}, ensure_ascii=False)
        content: Any = text
        if isinstance(screenshot, str) and screenshot.startswith("data:image/"):
            content = [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": screenshot}},
            ]
        messages = [
            {"role": "system", "content": _AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        raw = None
        planner = _EDITOR_PLANNER
        editor_agent_model.set_override(agent_model)
        editor_agent_model.set_last_model("", "")
        if planner is not None:
            try:
                raw = planner(messages, owner)
                if hasattr(raw, "__await__"):
                    raw = await raw
            except Exception:
                raw = None
        _used = editor_agent_model.get_last_model()
        parsed = _parse_plan(raw)
        if _used and _used[0]:
            model_used = _used[0]
        elif parsed:
            model_used = agent_model or editor_agent_model.local_model()
        else:
            model_used = "fallback" if agent_model else editor_agent_model.local_model()
        plan = parsed or _fallback_plan(prompt)
        _strengthened = style_guide.strengthen(prompt, list(plan.get("steps") or []))
        if _strengthened != plan.get("steps"):
            plan = {**plan, "steps": _strengthened,
                    "planner": f"{plan.get('planner') or 'llm'}+guide"}
        kind = _generative_kind(prompt)
        provider_available = _image_provider_available(owner)
        # Deterministic overrides for intents the model reliably botches: canvas
        # format/crop/extend, text + layer styles, and PSD export.
        _special = _plan_special(prompt, state, provider_available)
        special_note = ""
        if _special:
            special_steps, special_reply, special_note = _special
            steps = style_guide.enforce_guards(special_steps) or list(special_steps)
            reply = special_reply
            kind = None
            plan = {**plan, "steps": steps,
                    "planner": f"{plan.get('planner') or 'llm'}+deterministic"}
        else:
            steps = _ensure_generative(prompt, list(plan.get("steps") or []))
            steps = style_guide.enforce_guards(steps)
            if steps != (plan.get("steps") or []):
                plan = {**plan, "steps": steps,
                        "planner": f"{plan.get('planner') or 'llm'}+generative"}
            reply = str(plan.get("reply") or "")
            # A bare whole-image tone plan for an unspecified prompt is a guess:
            # ask the user instead of silently tweaking.
            # A bare whole-image tone plan for an unspecified prompt is a guess:
            # ask the user instead of silently tweaking. Only replace when the
            # guard actually changed the plan (identity), so a model-supplied
            # ask_user never wipes out a concrete generative edit.
            _base_plan = {**plan, "steps": steps}
            _maybe = _unsure_plan(prompt, _base_plan)
            if _maybe is not _base_plan and isinstance(_maybe, dict) and _maybe.get("ask_user"):
                plan = _maybe
                steps = []
                reply = str(_maybe.get("reply") or "")
        trace.append({
            "type": "plan", "tool": "plan_edit",
            "planner": plan.get("planner"), "steps": steps,
            "image_provider": provider_available, "agent_model": model_used,
        })
        # A vague prompt with no concrete defect asks the user instead of
        # guessing a token edit (2-3 concrete options).
        ask_user_payload = plan.get("ask_user")
        if isinstance(ask_user_payload, dict) and not steps and not _special:
            trace.append({"type": "ask_user",
                          "question": ask_user_payload.get("question")})
            return JSONResponse({
                "ok": True,
                "ask_user": ask_user_payload,
                "ask_user_pending": True,
                "reply": reply or str(ask_user_payload.get("question") or ""),
                "tools_offered": tools_offered,
                "steps": [],
                "trace": trace,
                "results": [],
                "agent_model": model_used,
            })
        # This route dispatches tools directly, outside execute_tool_block.
        # Apply the same administrator tool switches before any step runs.
        needed = {"plan_edit"}
        for step in steps:
            op = step.get("op")
            needed.add(op if op in {"ai_edit_image", "generate_image"} else "run_editor_command")
        if needed.intersection(disabled):
            raise HTTPException(403, "Editor tool is disabled")
        # No image provider: stop before any edit runs and tell the user plainly
        # (zero canvas changes; nothing to roll back).
        if _plan_uses_ai_content(steps):
            from src.auth_helpers import require_privilege
            require_privilege(request, "can_generate_images")
        if (kind or _plan_uses_ai_content(steps)) and not provider_available:
            trace.append({"type": "blocked", "reason": "image_provider_unavailable"})
            return JSONResponse({
                "ok": False,
                "blocked": True,
                "needs_provider": True,
                "reply": _PROVIDER_REQUIRED_MESSAGE,
                "message": _PROVIDER_REQUIRED_MESSAGE,
                "image_provider": False,
                "tools_offered": tools_offered,
                "steps": steps,
                "trace": trace,
                "results": [],
                "agent_model": model_used,
            })
        needed = {"plan_edit"}
        for step in steps:
            op = step.get("op")
            needed.add(op if op in {"ai_edit_image", "generate_image"} else "run_editor_command")
        if needed.intersection(disabled):
            raise HTTPException(403, "Editor tool is disabled")
        results: List[Dict[str, Any]] = []
        for step in steps:
            op = step.get("op")
            params = step.get("params") or {}
            if op in ("ai_edit_image", "generate_image"):
                try:
                    from src.agent_tools.editor_tools import AiEditImageTool, GenerateImageLayerTool
                except Exception:
                    results.append({"op": op, "ok": False, "error": "editor tools unavailable"})
                    break
                tool_payload = dict(params)
                # Planner output is untrusted: it cannot redirect this turn to
                # another editor session or override an explicit model choice.
                tool_payload["session"] = session
                if use_image_settings:
                    tool_payload["_use_image_settings"] = True
                    if op == "generate_image" and state.get("width") and state.get("height"):
                        try:
                            width, height = int(state["width"]), int(state["height"])
                            if width > 0 and height > 0:
                                tool_payload["size"] = f"{width}x{height}"
                        except (TypeError, ValueError):
                            pass
                # Route to the user-picked model when one is selected.
                selected_model = generation_model_choice if op == "generate_image" else model_choice
                if selected_model:
                    tool_payload["model"] = selected_model
                elif op == "generate_image" or use_image_settings:
                    # Let server settings resolve Auto, not a planner-invented model.
                    tool_payload.pop("model", None)
                # Hand the AI step the canvas image the client already captured
                # for this request. Without it the tool falls back to a separate
                # bridge screenshot, which is not reliably answered right after a
                # selection step (the masked path was losing the canvas image).
                if isinstance(screenshot, str) and screenshot.startswith("data:image/"):
                    tool_payload["image"] = screenshot
                else:
                    tool_payload.pop("image", None)
                tool_cls = AiEditImageTool if op == "ai_edit_image" else GenerateImageLayerTool
                try:
                    _desc, res = await tool_cls().execute(
                        json.dumps(tool_payload),
                        {"owner": owner, "session_id": _scope_label(scoped), "editor_session": session,
                         "incognito": body.get("incognito") is True},
                    )
                except Exception:
                    logger.exception("Editor image operation failed")
                    res = {"error": "Editor image operation failed", "exit_code": 1}
                res = res if isinstance(res, dict) else {}
                ok = res.get("exit_code", 0) == 0
                step_result = {
                    k: res.get(k) for k in ("image_id", "image_url", "placed", "output", "model")
                    if k in res
                }
                results.append({
                    "op": op, "ok": ok, "result": step_result,
                    # Surface the real provider/tool failure so the reply can
                    # say what went wrong instead of a bare ok:true/ok:false.
                    "error": None if ok else (res.get("error") or res.get("output") or "it did not complete"),
                })
                trace.append({"type": "step", "op": op, "ok": ok, "result": step_result})
                if not ok:
                    break
                continue
            cid = uuid.uuid4().hex
            fut = asyncio.get_event_loop().create_future()
            pending[cid] = (owner, fut)
            _broadcast(scoped, {"id": cid, "op": op, "params": params})
            try:
                res = await asyncio.wait_for(pending[cid][1], timeout=_BRIDGE_DEFAULT_TIMEOUT)
            except asyncio.TimeoutError:
                results.append({"op": op, "ok": False, "error": "timeout"})
                trace.append({"type": "step", "op": op, "ok": False, "error": "timeout"})
                break
            finally:
                _expire_command(scoped, cid, fut)
            results.append(res)
            trace.append({"type": "step", "op": op, "ok": res.get("ok"), "result": res.get("result")})
            if res.get("ok") is False:
                break
        # Post-run verification: read the ACTUAL canvas back and base the reply
        # on executed results + final document.info, never the plan.
        final_state: Dict[str, Any] = {}
        if steps and not any(r.get("ok") is False for r in results):
            try:
                _info_id = uuid.uuid4().hex
                _info_fut = asyncio.get_event_loop().create_future()
                pending[_info_id] = (owner, _info_fut)
                _broadcast(scoped, {"id": _info_id, "op": "document.info", "params": {}})
                _info = await asyncio.wait_for(pending[_info_id][1], timeout=60)
                if isinstance(_info, dict) and isinstance(_info.get("result"), dict):
                    final_state = _info["result"]
            except Exception:
                final_state = {}
            finally:
                _expire_command(scoped, _info_id, _info_fut)
        planned_reply = reply
        reply = _verified_reply(prompt, steps, results, final_state, planned_reply)
        if special_note and special_note.strip() not in reply:
            reply = (reply + " " + special_note.strip()).strip()
        return JSONResponse({
            "ok": not any(r.get("ok") is False for r in results),
            "reply": reply or "Done.",
            "planned_reply": planned_reply,
            "image_provider": provider_available,
            "final_state": final_state,
            "tools_offered": tools_offered,
            "tools": ["plan_edit"],
            "steps": steps,
            "trace": trace,
            "results": results,
            "agent_model": model_used,
        })

    @router.get("/api/editor/style-guide")
    async def style_guide_route() -> JSONResponse:
        """The shared intent -> ops guide (single source of truth).

        The browser fallback planner fetches this so the client and server
        routing rules cannot drift.
        """
        return JSONResponse(style_guide.as_json())

    @router.get("/api/editor/image-models")
    async def editor_image_models(request: Request) -> JSONResponse:
        """List the image models available from configured, owner-visible providers.

        Each entry is ``{id, label, kind, provider}`` where ``id`` is the model id
        to pass as ``model`` to ai_edit_image / generate_image, and ``kind`` is
        ``edit`` (editing/inpainting models) or ``generate`` (text-to-image).
        """
        owner = _user(request)
        is_admin = _is_admin(request)
        models: List[Dict[str, Any]] = []
        seen: set = set()
        try:
            from core.database import SessionLocal, ModelEndpoint
            from src.auth_helpers import owner_filter
            from src.endpoint_resolver import _endpoint_enabled_models
            from src.image_model_ids import looks_like_image_generation_model

            db = SessionLocal()
            try:
                query = owner_filter(
                    db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True),  # noqa: E712
                    ModelEndpoint,
                    owner,
                )
                for ep in query.all():
                    # Image-only endpoints (the local Qwen server) list their
                    # models verbatim. Gateway/chat endpoints (OpenRouter, …)
                    # mix chat and image models, so expose only ids that are
                    # recognisably image-capable — the same test the request
                    # router uses.
                    image_endpoint = getattr(ep, "model_type", None) == "image"
                    endpoint_id = getattr(ep, "id", None)
                    provider = getattr(ep, "name", None)
                    if not provider:
                        provider = (
                            getattr(ep, "base_url", "")
                            if is_admin
                            else (f"Endpoint {endpoint_id}" if endpoint_id else "image")
                        )
                    provider = provider or "image"
                    for name in _endpoint_enabled_models(ep):
                        if not image_endpoint and not looks_like_image_generation_model(name):
                            continue
                        key = (str(endpoint_id or provider), name)
                        if key in seen:
                            continue
                        seen.add(key)
                        entry = {
                            "id": name,
                            "label": name,
                            "kind": "edit" if _EDIT_MODEL_RE.search(name) else "generate",
                            "provider": provider,
                            "endpoint_id": endpoint_id,
                        }
                        if is_admin:
                            entry["base_url"] = getattr(ep, "base_url", "") or ""
                        models.append(entry)
            finally:
                db.close()
        except Exception:
            logger.warning("editor image-models lookup failed", exc_info=True)
        return JSONResponse({"models": models})

    @router.get("/api/editor/agent-models")
    async def editor_agent_models(request: Request) -> JSONResponse:
        """List the models the editor agent/planner can run on.

        Each entry is ``{id, label, source, vision}`` where ``id`` is
        passed as ``agent_model`` to /api/editor/plan or /api/editor/agent, and
        ``source`` is ``local`` (the local Qwen3.5-9B vision server) or
        ``hosted`` (the configured default/utility chat model that the planner
        falls back to).
        For non-admin users, private URLs and internal probe status are omitted.
        """
        owner = _user(request)
        is_admin = _is_admin(request)
        try:
            models = await editor_agent_model.list_editor_agent_models(owner)
            if not is_admin:
                sanitized = []
                for m in models:
                    m_copy = dict(m)
                    m_copy.pop("base_url", None)
                    sanitized.append(m_copy)
                models = sanitized
        except Exception:
            logger.warning("editor agent-models lookup failed", exc_info=True)
            models = []

        probe_info = {}
        if is_admin:
            probe_info = editor_agent_model.probe_status()
            if not probe_info:
                editor_agent_model.startup_probe()
                probe_info = editor_agent_model.probe_status()

        return JSONResponse({
            "models": models,
            "probe": probe_info,
        })

    @router.post("/api/editor/self-check")
    async def editor_self_check(request: Request) -> JSONResponse:
        """Same-origin VL self-check: compare BEFORE/AFTER against the intent.

        A configured vision server may not provide browser CORS headers. This route
        proxies the two images + intent to the agent VLM and returns
        ``{done, issues, retry_hint, text}`` (``done=True`` with
        ``reason="unavailable"`` on any failure so the client never loops).
        """
        owner = _user(request)
        body = await _json_object(request)
        result = await editor_agent_model.self_check(
            body.get("before"),
            body.get("after"),
            _prompt(body.get("prompt") or body.get("intent")),
            model=str(body.get("model") or "").strip() or None,
            owner=owner,
        )
        return JSONResponse(result)

    return router
