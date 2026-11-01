"""Single source of truth for photo-editor intent routing.

Both the server-side planner (``routes/editor_agent_routes.py``) and the
browser fallback planner (``static/js/editor/nl-edit.js``) read this guide so
their behaviour cannot drift. It is served verbatim to the client at
``GET /api/editor/style-guide``.

Calibrated strengths live here; this file is the only place to tune routing.
It is intentionally dependency-free so the route module can import it cheaply.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional

STYLE_GUIDE_VERSION = 8

# New editor ops (implemented client-side, static) with canonical params. The
# server op catalog must include these or a valid plan would be filtered out.
NEW_OPS: List[Dict[str, Any]] = [
    {
        "op": "photo.autoTone",
        "params": {},
        "help": "Camera-Raw-style auto tone/colour (per-channel levels, auto white "
                "balance, exposure, contrast). The single preferred global lift.",
    },
    {
        "op": "retouch.skin",
        "params": {"smooth": 8, "opacity": 0.35, "blemish": True,
                   "radius": 12, "threshold": 16},
        "help": "Gentle frequency-separation skin smoothing that keeps texture, over "
                "the selection intersected with skin tones. Keep it subtle: smooth "
                "<=14, opacity <=0.45; it never brightens the face.",
    },
    {
        "op": "retouch.blemish",
        "params": {"radius": 4, "threshold": 16},
        "help": "Remove small blemishes inside the selection or skin region.",
    },
    {
        "op": "retouch.teeth",
        "params": {"desat": 0.6, "bright": 16},
        "help": "Whiten teeth within the current selection (desaturate yellow, "
                "lift brightness with a small feather).",
    },
    {
        "op": "selection.face",
        "params": {"mode": "replace"},
        "help": "Select a face (SAM text 'face', upper-centre ellipse fallback).",
    },
    {
        "op": "selection.background",
        "params": {"mode": "replace", "subject": "person", "fuzziness": 30},
        "help": "Select the background (SAM subject inverted, corner-colour "
                "fallback). Subject-aware; prefer over a plain global blur.",
    },
    {
        "op": "fill.flood",
        "params": {"color": "#ff0000", "x": 0, "y": 0, "tolerance": 32},
        "help": "Paint-bucket flood-fill a pixel layer from a point "
                "{x,y,color,tolerance}. Use for 'fill ... with <colour>' when there "
                "is no active selection.",
    },
    {
        "op": "selection.fill",
        "params": {"color": "#ff0000"},
        "help": "Fill the current selection with a colour on the active pixel layer "
                "{color}. Use for 'fill the selection with <colour>'.",
    },
    {
        "op": "text.edit",
        "params": {"content": "New text", "size": 96, "color": "#ff0000"},
        "help": "Edit an EXISTING text layer {id|index|name, content, font, size, "
                "color, align, tracking, lineHeight}. Use for 'make the text "
                "bigger/smaller' and 'make the title <colour>' — never text.add.",
    },
    {
        "op": "layers.align",
        "params": {"mode": "center"},
        "help": "Align/distribute the selected layers to the canvas {mode:left|center|"
                "right|top|middle|bottom|distribute-h|distribute-v}.",
    },
    {
        "op": "layer.rotate",
        "params": {"degrees": 90},
        "help": "Rotate the ACTIVE LAYER by 90/180/270 {degrees, id|index|name}. Use "
                "unless the prompt names the whole image/canvas/photo.",
    },
    {
        "op": "layer.flip",
        "params": {"axis": "h"},
        "help": "Flip the ACTIVE LAYER in place {axis:h|v, id|index|name}.",
    },
    {
        "op": "document.rotate",
        "params": {"degrees": 90},
        "help": "Rotate the WHOLE document (all layers + canvas) by 90/180/270 "
                "{degrees} — for 'rotate the whole image/canvas/photo'.",
    },
    {
        "op": "document.flip",
        "params": {"axis": "h"},
        "help": "Flip the WHOLE document {axis:h|v} — for 'flip the canvas/whole "
                "image'.",
    },
    {
        "op": "selection.copy",
        "params": {},
        "help": "Copy (or cut, {cut:true}) the selection pixels to the internal "
                "clipboard.",
    },
    {
        "op": "selection.paste",
        "params": {},
        "help": "Paste the internal clipboard as a new layer {name}.",
    },
    {
        "op": "layers.flatten",
        "params": {},
        "help": "Flatten all layers into a single layer.",
    },
    {
        "op": "layers.mergeDown",
        "params": {},
        "help": "Merge the active layer down into the layer below.",
    },
]

NEW_OP_NAMES: List[str] = [op["op"] for op in NEW_OPS]


def _adj(kind: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"op": "adjustments.apply", "params": {"type": kind, "params": params}}


def _filter(fid: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"op": "filters.apply", "params": {"id": fid, **(params or {})}}


# Ordered intent routes. The first route whose keyword appears wins.
INTENT_ROUTES: List[Dict[str, Any]] = [
    {
        "id": "teeth",
        "keywords": ["teeth", "whiten my smile", "whiter smile", "smile looks"],
        "steps": [
            {"op": "selection.face", "params": {"mode": "replace"}},
            {"op": "retouch.teeth", "params": {"desat": 0.6, "bright": 16}},
        ],
        "reply": "Selecting the face and whitening teeth.",
    },
    {
        "id": "portrait_retouch",
        "keywords": ["skin", "complexion", "blemish", "acne", "pores", "retouch",
                     "look better", "look my best", "make me look", "beautify",
                     "flawless", "smooth my face", "clear up", "clean up my face",
                     "portrait", "selfie"],
        # Do NOT lead with a whole-image photo.autoTone: lightening the whole frame
        # washes out the face (the client suppresses autoTone for portrait intents
        # unless the histogram shows real under/over-exposure). Go straight to a
        # gentle skin retouch; keep it subtle and never brighten the face.
        "min_steps": 3,
        "steps": [
            {"op": "selection.face", "params": {"mode": "replace"}},
            {"op": "retouch.skin", "params": {"smooth": 8, "opacity": 0.35,
                                              "blemish": True, "radius": 12,
                                              "threshold": 16}},
            {"op": "selection.deselect", "params": {}},
        ],
        "reply": "Selecting the face and gently retouching the skin.",
    },
    {
        "id": "restore",
        "keywords": ["restore", "restoration", "old photo", "old photograph",
                     "old picture", "faded photo", "faded picture", "faded",
                     "recover this photo", "bring back this photo",
                     "photo restoration"],
        # Never a large exposure lift: recover contrast + colour from a faded
        # baseline, then a slight sharpen.
        "min_steps": 4,
        "steps": [
            {"op": "photo.autoTone", "params": {}},
            _adj("brightness-contrast", {"brightness": 0, "contrast": 18}),
            _adj("white-balance", {"temperature": 0, "tint": 5}),
            _adj("vibrance", {"vibrance": 18}),
            _filter("sharpen"),
        ],
        "reply": "Restoring contrast and colour, then a light sharpen.",
    },
    {
        "id": "background",
        "keywords": ["background"],
        "steps": [
            {"op": "selection.background",
             "params": {"mode": "replace", "subject": "person", "fuzziness": 30}},
            {"op": "filters.applyMasked", "params": {"id": "blur-gaussian"}},
        ],
        "reply": "Selecting the background and softening it.",
    },
    {
        "id": "lighting",
        # Low-key lighting: one auto-tone pass (the preferred global lift) plus
        # at most one shadow-lifting, highlight-protecting adjustment. Both are
        # sanctioned together; never a stack of brightness/exposure lifts.
        "keywords": ["lighting", "exposure", "too dark", "underexposed",
                     "underexpose", "backlit", "back-lit", "brighten", "brighter",
                     "lift the shadows", "dark photo", "dark image"],
        "min_steps": 2,
        "steps": [
            {"op": "photo.autoTone", "params": {}},
            {"op": "adjustments.apply",
             "params": {"type": "shadows-highlights",
                        "params": {"shadows": 30, "highlights": -15}}},
        ],
        "reply": "Auto tone (levels and white balance), then one shadow lift "
                 "that protects the highlights.",
    },
    {
        "id": "cool",
        "keywords": ["moodier", "moody", "cooler", "cool it", "cinematic", "teal",
                     "blue tone", "cold", "night mood", "colder"],
        "steps": [_adj("white-balance", {"temperature": -25})],
        "reply": "Cooling the white balance (negative temperature).",
    },
    {
        "id": "warm",
        "keywords": ["warmer", "warm it", "golden", "summer", "sunset",
                     "golden hour", "warmth"],
        "steps": [_adj("white-balance", {"temperature": 25})],
        "reply": "Warming the white balance (positive temperature).",
    },
    {
        "id": "black_white",
        "keywords": ["black and white", "black & white", "grayscale", "greyscale",
                     "monochrome", "b&w"],
        "steps": [_adj("black-white", {})],
        "reply": "Converting to black and white.",
    },
    {
        "id": "polish",
        # Generic "make it look good" intents: a decisive multi-step grade with
        # visible strength (a single timid adjustment scores as imperceptible).
        "keywords": ["pretty", "beautiful", "make it look good", "look good",
                     "professional", "professionally", "editorial", "magazine",
                     "glam", "glamour", "pop", "punchy", "striking", "vibrant",
                     "film", "film look", "cinematic look", "instagram",
                     "social media", "elevate", "polish"],
        "min_steps": 3,
        "steps": [
            {"op": "photo.autoTone", "params": {}},
            _adj("vibrance", {"vibrance": 25}),
            _filter("clarity", {"amount": 22, "radius": 30}),
            _filter("vignette", {"amount": 22, "feather": 0.6}),
        ],
        "reply": "Balancing tone, adding vibrance and local contrast, with a light vignette.",
    },
    {
        "id": "vivid",
        "keywords": ["vivid", "dramatic", "saturate", "saturation",
                     "colourful", "colorful"],
        "steps": [_adj("vibrance", {"vibrance": 35})],
        "reply": "Boosting vibrance.",
    },
    {
        "id": "sharpen",
        "keywords": ["sharpen", "crisper", "crisp", "more detail", "sharper"],
        "steps": [_filter("sharpen")],
        "reply": "Sharpening.",
    },
]

# A prompt that only asks for a vague "improvement" with no concrete defect and
# no matching intent cannot be planned deterministically -> ask the user.
_AMBIGUOUS_PATTERNS = [
    "clean this up", "clean it up", "fix it", "fix this", "make it better",
    "make this better", "improve", "touch up", "touch it up", "do something",
    "enhance", "make it nice", "polish",
]
_AMBIGUOUS_OPTIONS = [
    {"label": "Auto tone & color",
     "description": "photo.autoTone: balanced levels, white balance, contrast."},
    {"label": "Brighten (lift shadows)",
     "description": "shadows-highlights: brighten without blowing highlights."},
    {"label": "Cooler / cinematic",
     "description": "white-balance negative: cooler, moodier look."},
]
_DEFECT_HINTS = [
    "skin", "teeth", "background", "light", "dark", "colour", "color", "warm",
    "cool", "blur", "sharpen", "contrast", "bright", "shadow", "face",
    "blemish", "noise", "grain", "crop", "vibrance", "saturat", "black and white",
    "grayscale", "portrait", "moody", "exposure", "tone",
]

# Low-key safety: at most ONE global lift; prefer shadows/highlights and keep
# highlights protected. Global-lift adjustment types are counted together.
GLOBAL_LIFT_TYPES = ("autoTone", "brightness-contrast", "exposure", "levels",
                     "shadows-highlights")
_SAFE_LOWKEY_LIFT = {
    "op": "adjustments.apply",
    "params": {"type": "shadows-highlights",
               "params": {"shadows": 30, "highlights": -15}},
}


def match(prompt: str) -> Optional[Dict[str, Any]]:
    """Return the first matching intent route for a prompt, or None."""
    text = str(prompt or "").lower()
    for route in INTENT_ROUTES:
        if any(keyword in text for keyword in route["keywords"]):
            return route
    return None


def is_ambiguous(prompt: str) -> bool:
    text = str(prompt or "").lower()
    if match(text):
        return False
    if any(hint in text for hint in _DEFECT_HINTS):
        return False
    return any(pattern in text for pattern in _AMBIGUOUS_PATTERNS)


def ask_user_plan() -> Dict[str, Any]:
    question = "What would you like me to do with this photo?"
    return {
        "reply": question,
        "planner": "fallback",
        "steps": [],
        "ask_user": {
            "question": question,
            "options": [dict(o) for o in _AMBIGUOUS_OPTIONS],
            "multi": False,
        },
    }


def has_concrete_intent(prompt: str) -> bool:
    """True when a prompt maps to a concrete edit (a recipe, look, recolour,
    structural op) or names a concrete defect. Used to decide whether a plan made
    only of generic whole-image tone ops should be trusted or replaced by an
    ask_user prompt.
    """
    text = str(prompt or "").lower()
    if match(text) or look_plan(text) or recolor_plan(text) or structural_plan(text):
        return True
    return any(hint in text for hint in _DEFECT_HINTS)


def fallback_plan(prompt: str) -> Optional[Dict[str, Any]]:
    """Deterministic plan from the guide, or None when nothing matches."""
    route = match(prompt)
    if route:
        return {
            "reply": route["reply"],
            "planner": "fallback",
            "steps": enforce_guards([dict(step) for step in route["steps"]]),
        }
    if is_ambiguous(prompt):
        return ask_user_plan()
    return None


# ── Structural op routing ──────────────────────────────────────────────
# Deterministic plans for structural editor ops the sampling model reliably
# substitutes with a generic adjustments/transform step. Mirrors the browser
# fallback (static/js/editor/nl-edit.js localPlan) so the server and client
# agree; the server path must emit these or a wrong model plan suppresses the
# browser fallback.
_NAMED_COLORS = {
    "red": "#ff0000", "blue": "#0000ff", "green": "#00a000", "yellow": "#ffff00",
    "orange": "#ff8000", "purple": "#8000ff", "pink": "#ff69b4", "black": "#000000",
    "white": "#ffffff", "grey": "#808080", "gray": "#808080", "cyan": "#00ffff",
    "magenta": "#ff00ff", "brown": "#8b4513", "teal": "#008080",
}
_COLOR_TOKEN = (r"(red|blue|green|yellow|orange|purple|pink|black|white|grey|"
                r"gray|cyan|magenta|brown|teal|#[0-9a-fA-F]{3,6})")


def color_for_name(name: str) -> Optional[str]:
    n = str(name or "").strip().lower()
    if re.match(r"^#[0-9a-f]{3,6}$", n):
        return n
    return _NAMED_COLORS.get(n)


# ── Named reference looks + targeted recolour ──────────────────────────
# Server-only deterministic plans (reached via routes._plan_special ->
# structural_plan). Deliberately NOT added to INTENT_ROUTES: the browser
# client consumes `intents` and cannot run an ai_edit_image step, so strong
# looks must stay on the server path.
_LOOK_KEEP_CLAUSE = " Keep the main subject and composition exactly the same."

LOOKS = [
    {
        "id": "wes_anderson",
        "keywords": ["wes anderson"],
        "mode": "recipe",
        "steps": [
            {"op": "photo.autoTone", "params": {}},
            _adj("white-balance", {"temperature": 10, "tint": 6}),
            _adj("vibrance", {"vibrance": 18}),
            _adj("brightness-contrast", {"brightness": 6, "contrast": -10}),
        ],
        "reply": "Gave it a Wes Anderson look: pastel palette, warm highlights and soft contrast.",
    },
    {
        "id": "film_noir",
        "keywords": ["film noir"],
        "mode": "recipe",
        "steps": [
            _adj("black-white", {}),
            _adj("brightness-contrast", {"contrast": 38}),
            _adj("shadows-highlights", {"shadows": -22, "highlights": 18}),
            _filter("vignette", {"amount": 45, "feather": 0.5}),
        ],
        "reply": "Gave it a film-noir look: high-contrast black and white with deep shadows.",
    },
    {
        "id": "vintage_kodachrome",
        "keywords": ["kodachrome", "vintage film"],
        "mode": "recipe",
        "steps": [
            {"op": "photo.autoTone", "params": {}},
            _adj("white-balance", {"temperature": 12, "tint": 8}),
            _adj("vibrance", {"vibrance": 22}),
            _adj("brightness-contrast", {"contrast": 14}),
            _filter("vignette", {"amount": 12, "feather": 0.7}),
        ],
        "reply": "Gave it a vintage Kodachrome look: warm saturated colour and lifted contrast.",
    },
    {
        "id": "cyberpunk",
        "keywords": ["cyberpunk"],
        "mode": "instruction",
        "prompt": "Restyle the photo as a cyberpunk scene: vivid neon magenta and cyan "
                  "lighting, wet reflective surfaces, deep shadows and a futuristic "
                  "atmosphere." + _LOOK_KEEP_CLAUSE,
        "reply": "Restyling it as a cyberpunk scene with the image model.",
    },
    {
        "id": "studio_ghibli",
        "keywords": ["ghibli", "studio ghibli"],
        "mode": "instruction",
        "prompt": "Restyle the photo as a soft hand-painted anime scene in a "
                  "Studio-Ghibli-inspired pastel style: luminous pastel palette, "
                  "gentle painterly shading, clean edges and warm sunlight."
                  + _LOOK_KEEP_CLAUSE,
        "reply": "Restyling it as a Studio-Ghibli-inspired pastel scene with the image model.",
    },
]


def look_plan(prompt: str) -> Optional[Dict[str, Any]]:
    """Deterministic plan for a named reference look, or None.

    Recipe looks (Wes Anderson, film noir, vintage Kodachrome) are pure
    adjustment/filter stacks; strong looks (cyberpunk, Studio Ghibli) use
    ai_edit_image instruction mode.
    """
    p = str(prompt or "").lower()
    for look in LOOKS:
        if any(k in p for k in look["keywords"]):
            if look["mode"] == "instruction":
                steps = [{"op": "ai_edit_image",
                          "params": {"op": "generative", "mode": "instruction",
                                     "prompt": look["prompt"], "steps": 8}}]
            else:
                steps = [dict(s) for s in look["steps"]]
            return {"reply": look["reply"], "planner": "deterministic", "steps": steps}
    return None


_RECOLOR_ACTION_RE = re.compile(
    r"\brecolou?r\b"
    r"|\bmake\s+only\b"
    r"|\bmake\s+the\b[^.]{0,30}?\b" + _COLOR_TOKEN + r"\b"
    r"|\b(?:change|turn|convert|paint|colou?r)\b[^.]{0,40}?\b" + _COLOR_TOKEN + r"\b",
    re.I,
)
_RECOLOR_EXCLUDE_RE = re.compile(
    r"\b(?:background|backdrop|blur|black\s+and\s+white|grayscale|greyscale|"
    r"monochrome|b&w|text|title|font|caption|label|logo|watermark|drop\s+shadow|"
    r"generative)\b",
    re.I,
)


_RECOLOR_TRAILING_ADJECTIVES = frozenset({
    "bright", "brighter", "vivid", "vibrant", "dark", "darker", "light",
    "lighter", "deep", "pale", "more", "less", "very", "really",
    "slightly", "totally", "completely",
})


def _hex_rgb01(hex_color: str):
    h = str(hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    try:
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except Exception:
        return None


def _hue_degrees(hex_color: str) -> Optional[float]:
    rgb = _hex_rgb01(hex_color)
    if rgb is None:
        return None
    r, g, b = rgb
    mx, mn = max(rgb), min(rgb)
    if mx == mn:
        return 0.0
    d = mx - mn
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60.0


def _hue_delta(src_hex: str, tgt_hex: str) -> int:
    sh = _hue_degrees(src_hex)
    th = _hue_degrees(tgt_hex)
    if sh is None or th is None:
        return 0
    return int(round((th - sh + 180.0) % 360.0 - 180.0))


def _is_chromatic(hex_color: str) -> bool:
    """True when a pure hue rotation can plausibly reach this colour.

    White / black / grey targets have (near) zero saturation and cannot be
    reached by rotating hue, so they go through the image model instead.
    """
    rgb = _hex_rgb01(hex_color)
    if rgb is None:
        return False
    mx, mn = max(rgb), min(rgb)
    return (mx - mn) >= 0.15 and mx >= 0.12


def _recolor_object_phrase(prompt: str, target_word: str) -> str:
    """Best-effort object description for the mask ("red car" in 'the red car')."""
    m = re.search(
        r"\b(?:make|recolou?r|change|turn|convert|paint|colou?r)\b\s+(?:only\s+)?(.+)",
        prompt, re.I,
    )
    phrase = (m.group(1) if m else prompt).strip()
    if target_word:
        phrase = re.sub(r"\b" + re.escape(target_word) + r"\b", " ", phrase,
                        count=1, flags=re.I)
    words = " ".join(phrase.split()).strip(" .,!?").split()
    if len(words) > 1 and words[0].casefold() in {"to", "into", "the", "a", "an"}:
        words.pop(0)
    if len(words) > 1 and words[-1].casefold() in {"to", "into"}:
        words.pop()
    while len(words) > 1 and words[-1].lower() in _RECOLOR_TRAILING_ADJECTIVES:
        words.pop()
    phrase = " ".join(words)
    return phrase or "the subject"


def recolor_plan(prompt: str) -> Optional[Dict[str, Any]]:
    """Deterministic plan for a targeted recolour, or None.

    'make only the red car blue' -> select the OBJECT by description
    (``selection.samText`` "red car"; falling back to a colour-range selection
    via ``fallbackColor`` when the mask is empty) and then shift the MASKED
    HUE toward the target with ``adjustments.applyMasked`` (preserving shading,
    highlights and reflections). Never a flat ``selection.fill`` and never a
    whole-image ``adjustments.apply`` tint. When the target cannot be reached by
    a hue rotation (white/black/grey) or no source colour is known, the masked
    region is recoloured with ``ai_edit_image`` instruction mode instead.
    """
    p = str(prompt or "")
    if not _RECOLOR_ACTION_RE.search(p):
        return None
    if _RECOLOR_EXCLUDE_RE.search(p):
        return None
    colors = [m.group(1).lower() for m in
              re.finditer(r"\b" + _COLOR_TOKEN + r"\b", p)]
    colors = [c for c in colors if color_for_name(c)]
    if not colors:
        return None
    target = colors[-1]
    tgt_hex = color_for_name(target)
    source = colors[0] if len(colors) >= 2 and colors[0] != target else None
    src_hex = color_for_name(source) if source else None
    phrase = _recolor_object_phrase(p, target)
    select = {
        "op": "selection.samText",
        "params": {"text": phrase},
    }
    if src_hex:
        select["params"]["fallbackColor"] = src_hex
        select["params"]["fallbackFuzziness"] = 40
    if source and src_hex and _is_chromatic(tgt_hex):
        delta = _hue_delta(src_hex, tgt_hex)
        src_hue = int(round(_hue_degrees(src_hex) or 0))
        return {
            "reply": "Selected the %s and shifted its hue toward %s "
                     "(shading kept)." % (phrase, target),
            "planner": "deterministic",
            "steps": [
                select,
                {"op": "adjustments.applyMasked",
                 "params": {"hue": delta, "saturation": 0,
                            "mode": "hue-saturation",
                            "sourceHue": src_hue,
                            "source": src_hex, "target": tgt_hex}},
            ],
        }
    instruction = ("Recolour the %s to %s, preserving its shading, highlights "
                   "and reflections." % (phrase, target))
    return {
        "reply": "Recolouring the %s with the image model." % phrase,
        "planner": "deterministic",
        "steps": [
            select,
            {"op": "ai_edit_image",
             "params": {"op": "inpaint", "mode": "masked",
                        "prompt": instruction, "steps": 8}},
        ],
    }


def structural_plan(prompt: str) -> Optional[Dict[str, Any]]:
    """Deterministic plan for structural editor ops, or None.

    Covers fill/flood, text.edit, colour-overlay layer style, layer align,
    layer/document rotate+flip, selection copy/paste, flatten and merge-down.
    Also dispatches named reference looks and targeted recolours.
    Returns ``{"reply", "planner": "deterministic", "steps": [...]}``.
    """
    look = look_plan(prompt)
    if look is not None:
        return look
    recolor = recolor_plan(prompt)
    if recolor is not None:
        return recolor
    p = str(prompt or "").lower()
    steps: List[Dict[str, Any]] = []
    labels: List[str] = []

    fm = (re.search(r"\bfill\b[^.]*?\bwith\s+" + _COLOR_TOKEN + r"\b", p)
          or re.search(r"\bfill\b[^.]*?\b" + _COLOR_TOKEN + r"\b", p))
    if fm:
        color = color_for_name(fm.group(1))
        if color:
            if re.search(r"\bselection\b|\bselected\b", p):
                steps.append({"op": "selection.fill", "params": {"color": color}})
                labels.append("filled the selection")
            else:
                steps.append({"op": "fill.flood",
                              "params": {"color": color, "x": 0, "y": 0}})
                labels.append("flood-filled the layer")

    if re.search(r"\b(text|title|caption|font|label)\b", p) and not re.search(
            r"\b(add|insert|create|write|put|place)\b", p):
        tparams: Dict[str, Any] = {}
        if re.search(r"\b(bigger|larger|increase)\b", p):
            tparams["size"] = 96
        elif re.search(r"\b(smaller|reduce|decrease)\b", p):
            tparams["size"] = 28
        cm = re.search(r"\b" + _COLOR_TOKEN + r"\b", p)
        if cm:
            c = color_for_name(cm.group(1))
            if c:
                tparams["color"] = c
        if tparams:
            steps.append({"op": "text.edit", "params": tparams})
            labels.append("updated the text layer")

    if re.search(r"\bcolo?ur?\s*overlay\b", p):
        steps.append({"op": "layers.addStyle",
                      "params": {"type": "color-overlay", "params": {}}})
        labels.append("added a colour-overlay layer style")

    am = re.search(
        r"\balign\b[^.]*?\b(centre|center|left|right|top|middle|bottom)\b", p)
    if am:
        mode = "center" if am.group(1) in ("centre", "center") else am.group(1)
        steps.append({"op": "layers.align", "params": {"mode": mode}})
        labels.append("aligned to the %s" % mode)

    whole = bool(re.search(
        r"\b(?:whole|entire|full)\s+(?:image|canvas|photo|picture|document)\b", p)) \
        or bool(re.search(r"\b(?:canvas|document)\b", p)
                  and re.search(r"\b(?:rotate|flip|mirror|turn)\b", p))
    if re.search(r"\b(?:flip|mirror)\b", p):
        axis = "v" if re.search(r"\b(?:vertical(?:ly)?|v)\b", p) else "h"
        if whole:
            steps.append({"op": "document.flip", "params": {"axis": axis}})
            labels.append("flipped the whole image")
        else:
            steps.append({"op": "layer.flip", "params": {"axis": axis}})
            labels.append("flipped the layer")
    # Rotation needs an explicit angle/quarter-turn or the verb "rotate"; a bare
    # "turn ... into <style>" (e.g. "turn this into an anime") is a restyle, not
    # a rotation.
    rotate_deg = None
    rm = re.search(r"\b(?:rotate|turn)\b[^\d]*?\b(90|180|270)\b", p)
    if rm:
        rotate_deg = int(rm.group(1))
    elif re.search(r"\brotate\b", p):
        rotate_deg = 90
    if rotate_deg is not None:
        degrees = rotate_deg
        if whole:
            steps.append({"op": "document.rotate", "params": {"degrees": degrees}})
            labels.append("rotated the whole image %d\u00b0" % degrees)
        else:
            steps.append({"op": "layer.rotate", "params": {"degrees": degrees}})
            labels.append("rotated the layer %d\u00b0" % degrees)

    if re.search(r"\bcopy\b", p) and re.search(r"\bselection\b|\bselected\b", p):
        steps.append({"op": "selection.copy", "params": {}})
        labels.append("copied the selection")
    if re.search(r"\bpaste\b", p) and not re.search(r"\bflatten\b|\bmerge\b", p):
        steps.append({"op": "selection.paste", "params": {}})
        labels.append("pasted as a new layer")

    if re.search(r"\bflatten\b", p):
        steps.append({"op": "layers.flatten", "params": {}})
        labels.append("flattened the image")
    elif re.search(r"\bmerge\b", p) and re.search(r"\bdown\b|\blayer\b", p):
        steps.append({"op": "layers.mergeDown", "params": {}})
        labels.append("merged the layer down")

    if not steps:
        return None
    return {
        "reply": "Done \u2014 I " + ", ".join(labels) + ".",
        "planner": "deterministic",
        "steps": steps,
    }


def _is_global_lift(step: Any) -> bool:
    if not isinstance(step, dict):
        return False
    op = step.get("op")
    params = step.get("params") or {}
    if op == "photo.autoTone":
        return True
    if op != "adjustments.apply":
        return False
    kind = params.get("type")
    if kind not in GLOBAL_LIFT_TYPES:
        return False
    inner = params.get("params") or {}
    if kind == "brightness-contrast":
        return float(inner.get("brightness") or 0) > 0
    if kind == "exposure":
        return float(inner.get("exposure") or 0) > 0
    return True


def _is_shadows_highlights(step: Any) -> bool:
    if not isinstance(step, dict) or step.get("op") != "adjustments.apply":
        return False
    params = step.get("params") or {}
    return params.get("type") == "shadows-highlights"


def _is_masked_recolor(step: Any) -> bool:
    """True for a masked hue-saturation recolour step.

    Such a step already targets the object's own colour, so no whole-image
    global adjustment may follow it (it would double-process the photo and
    undo the "only this object" intent).
    """
    if not isinstance(step, dict) or step.get("op") != "adjustments.applyMasked":
        return False
    params = step.get("params") or {}
    if params.get("mode") == "hue-saturation":
        return True
    hue = params.get("hue")
    return hue not in (None, 0, 0.0)


def _is_whole_image_adjustment(step: Any) -> bool:
    """True for a whole-image tone/colour adjustment (never selection-scoped).

    ``adjustments.apply`` acts on the entire layer and ``photo.autoTone`` on the
    whole photo, so after a masked recolour either one re-grades everything and
    undoes the "only this object" intent — drop both regardless of type
    (a non-lift grade such as vibrance/saturation would otherwise survive the
    low-key guard).
    """
    if not isinstance(step, dict):
        return False
    return step.get("op") in ("photo.autoTone", "adjustments.apply")


def collapse_global_lifts(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enforce the low-key rule: at most one global lift, never a stack.

    A single auto-tone pass (``photo.autoTone``) is the preferred global lift;
    it may be paired with ONE shadow-lifting, highlight-protecting adjustment.
    Any further global lift (a second auto tone, a brightness/exposure lift, or
    an extra shadows-highlights) is dropped. When there is no auto tone, two or
    more global lifts collapse to a single safe shadows-highlights step and a
    lone brightness/exposure lift is clamped so it cannot blow out highlights.
    """
    if not steps:
        return steps
    lifts = [i for i, step in enumerate(steps) if _is_global_lift(step)]
    auto = [i for i in lifts if steps[i].get("op") == "photo.autoTone"]
    if auto:
        other = [i for i in lifts if i not in auto]
        keep_other = other[0] if len(other) == 1 and _is_shadows_highlights(steps[other[0]]) else None
        drop = set(auto[1:]) | (set(other) - ({keep_other} if keep_other is not None else set()))
        if drop:
            return [step for i, step in enumerate(steps) if i not in drop]
        return steps
    if len(lifts) >= 2:
        drop = set(lifts)
        out = [step for i, step in enumerate(steps) if i not in drop]
        insert_at = min(lifts[0], len(out))
        out.insert(insert_at, json.loads(json.dumps(_SAFE_LOWKEY_LIFT)))
        return out
    if len(lifts) == 1:
        step = steps[lifts[0]]
        params = step.get("params") or {}
        if step.get("op") == "adjustments.apply":
            inner = params.get("params") or {}
            if params.get("type") == "brightness-contrast":
                inner["brightness"] = min(float(inner.get("brightness") or 0), 25.0)
                params["params"] = inner
                step["params"] = params
            elif params.get("type") == "exposure":
                inner["exposure"] = min(float(inner.get("exposure") or 0), 30.0)
                params["params"] = inner
                step["params"] = params
    return steps


def _clone(step: Any) -> Any:
    return json.loads(json.dumps(step))


def enforce_guards(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply every planner-side safety rule so the client rarely has to undo.

    * at most one global lift, never a stack (``collapse_global_lifts``);
    * never a global brightness lift AFTER ``retouch.skin`` (the client's
      "never brighten skin" guard), which also removes a trailing autoTone.
    """
    steps = collapse_global_lifts(steps)
    out: List[Dict[str, Any]] = []
    skin_seen = False
    for step in steps:
        op = step.get("op") if isinstance(step, dict) else None
        if op == "retouch.skin":
            skin_seen = True
            out.append(step)
            continue
        if skin_seen and _is_global_lift(step):
            continue
        out.append(step)
    # Never append a global adjustment after a masked recolour: the recolour is
    # already scoped to the object, so a following whole-image lift would undo
    # the intent and double-process the image.
    recolored = False
    guarded: List[Dict[str, Any]] = []
    for step in out:
        if _is_masked_recolor(step):
            recolored = True
            guarded.append(step)
            continue
        if recolored and (_is_global_lift(step) or _is_whole_image_adjustment(step)):
            continue
        guarded.append(step)
    return guarded


def strengthen(prompt: str, steps: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Promote a timid model plan to the guide recipe for the matched intent.

    The sampling LLM frequently returns a single mild ``adjustments.apply`` for
    generic intents (make it pretty / pop / professional / film look) and for
    skin/restore, which grade as imperceptible. When the plan is shorter than
    the recipe's ``min_steps`` the deterministic recipe is used instead.
    """
    steps = list(steps or [])
    route = match(prompt)
    if not route:
        return steps
    recipe = route.get("steps") or []
    try:
        min_steps = int(route.get("min_steps") or len(recipe))
    except (TypeError, ValueError):
        min_steps = len(recipe)
    # The guide is authoritative for a matched intent: keep the model plan only
    # when it already contains every recipe op (with multiplicity) and is at
    # least ``min_steps`` long. Otherwise the model substituted weaker/different
    # ops (e.g. three global adjustments with no photo.autoTone/white-balance for
    # "restore"), so the calibrated recipe is used. The signature distinguishes
    # adjustments by type and filters by id, so a plain brightness-contrast does
    # not masquerade as the recipe's white-balance.
    recipe_ops = Counter(_step_signature(step) for step in recipe)
    plan_ops = Counter(_step_signature(step) for step in steps)
    if len(steps) < min_steps or (plan_ops & recipe_ops) != recipe_ops:
        return [_clone(step) for step in recipe]
    return steps


def _step_signature(step: Any) -> str:
    if not isinstance(step, dict):
        return ""
    op = str(step.get("op") or "")
    params = step.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    if op in ("adjustments.apply", "adjustments.applyMasked"):
        return op + ":" + str(params.get("type") or "")
    if op in ("filters.apply", "filters.applyMasked"):
        return op + ":" + str(params.get("id") or "")
    return op


def guidance() -> str:
    """The routing/style block injected into every planner system prompt."""
    ops = "; ".join(
        "{op} {params}".format(op=o["op"], params=json.dumps(o["params"]))
        for o in NEW_OPS
    )
    return (
        "Style guide v" + str(STYLE_GUIDE_VERSION) + " (single source of truth; "
        "the browser fallback reads the same rules). New ops with canonical params: "
        + ops + ". Clarity and vignette are filters.apply ids "
        "(filters.apply {id:'clarity',amount,radius} and {id:'vignette',amount,feather}). "
        "Generic 'make it look good' ('make it pretty', 'make it pop', 'make it look "
        "professional', 'film look', 'instagram ready'): a decisive 3-4 step grade, "
        "never one mild adjustment: photo.autoTone -> adjustments.apply vibrance "
        "{vibrance:20-30} -> filters.apply {id:'clarity'} -> optional filters.apply "
        "{id:'vignette'}. "
        "Portrait / look-better ('fix the skin', 'make me look better', 'fix my "
        "teeth'): do NOT lead with photo.autoTone — a whole-image auto tone washes out "
        "the face. Go straight to selection.face (or selection.subject "
        "{label:'person'}) -> retouch.skin {smooth<=14, opacity<=0.45} -> "
        "selection.deselect (teeth: selection.face -> retouch.teeth). Keep the retouch "
        "gentle and never brighten the face; only add a global photo.autoTone when the "
        "photo is genuinely under- or over-exposed, and NEVER a global brightness lift "
        "AFTER retouch.skin. "
        "'restore this old photo': photo.autoTone -> brightness-contrast "
        "{brightness:0,contrast:~18} -> white-balance/tint -> vibrance {~18} -> "
        "sharpen; recover contrast + colour from the faded baseline and NEVER apply a "
        "large exposure lift. "
        "Low-key / 'fix the lighting' on a dark image: photo.autoTone PLUS at most "
        "ONE adjustments.apply shadows-highlights {shadows:30,highlights:-15} that "
        "protects highlights; NEVER stack a second autoTone/brightness/exposure/"
        "levels/shadows-highlights, and keep highlights <= 0. "
        "Temperature sign: moody/cooler/cinematic/teal -> white-balance temperature "
        "NEGATIVE; warmer/golden/summer/sunset -> POSITIVE. "
        "Background softer / 'blur the background' -> selection.background then "
        "filters.applyMasked {id:'blur-gaussian'}; never a plain global blur. "
        "GENERATIVE CONTENT: Qwen-Image-Edit is an instruction model, so most "
        "generative edits use ai_edit_image mode='instruction' on the WHOLE image "
        "with NO mask and a prompt that NAMES what must stay unchanged (e.g. "
        "'replace the background with a sunny beach, keep the woman exactly the "
        "same'). Use instruction mode for: replacing/changing the background or "
        "scene ('put her on a beach', 'change the background to night'), changing "
        "the season or weather ('make it snowy', 'make it autumn', 'make it rainy', "
        "'add fog'), adding an object or element to an existing photo ('add a full "
        "moon in the sky', 'add wildflowers', 'add sunglasses to her', 'add a "
        "rainbow', 'add clouds'), an explicit 'generative fill' ('generative fill "
        "the sky'), and removing diffuse distractions ('remove the "
        "distractions'). These are CONTENT changes, never adjustments.apply/filters. "
        "Use 8 sampler steps for these generative content edits. Use ai_edit_image "
        "mode='masked' ONLY for an explicit "
        "regional removal of a clearly identified object ('remove the person on "
        "the left') — select that object first so the mask is tight, then inpaint; "
        "if the mask comes back tiny or empty, fall back to instruction mode. "
        "generate_image is ONLY for creating a brand-new image ('create a new "
        "image of ...', 'make a logo') — NEVER use it to add content to an "
        "existing photo, and never place its output as an opaque full layer over "
        "a photo. "
        "Canvas format ('instagram post', '1080x1350', '4:5', 'square', '9:16', '16:9'): "
        "document.crop {x,y,width,height} to the target aspect, then document.resizeCanvas "
        "{width,height,anchor:'center'}; never just export/resize and claim success. To "
        "EXTEND ('extend the canvas to 16:9', 'make it wider'): ai.generativeExpand "
        "{width,height,anchor:'center',prompt,mode:'instruction',feather:true} when an "
        "image provider is configured — pad the canvas, keep the ORIGINAL pixels opaque on "
        "top, inpaint ONLY the new border with a prompt that continues the scene ('extend "
        "the sky and clouds'), and feather the seam; else document.resizeCanvas "
        "{width,height,anchor:'center'} and say honestly that the new border was extended "
        "with edge/transparent pixels. "
        "Text ('add a title TOKYO', 'add a caption', 'with a drop shadow'): text.add "
        "{text,x,y,color,size} plus layers.addStyle {type:'drop-shadow'}; text and layer "
        "styles are NOT generative and must never return the needs-provider message. "
        "'save as PSD' / 'layered PSD': document.savePsd. "
        "Structural ops (use the exact op, never a generic adjustment/transform): "
        "'fill ... with <colour>' -> selection.fill {color} when a selection is named, "
        "else fill.flood {color,x:0,y:0}; 'make the text bigger/smaller' or 'make the "
        "title <colour>' -> text.edit {size,color}; a 'colour overlay' -> layers.addStyle "
        "{type:'color-overlay'}; 'align ... left/centre/right/top/middle/bottom' -> "
        "layers.align {mode}; 'rotate/flip this layer' -> layer.rotate {degrees} / "
        "layer.flip {axis}, but 'rotate/flip the whole image/canvas/photo' -> "
        "document.rotate {degrees} / document.flip {axis}; 'copy the selection' -> "
        "selection.copy, 'paste' -> selection.paste; 'flatten the image' -> "
        "layers.flatten; 'merge the layer down' -> layers.mergeDown. "
        "Targeted recolour ('make only the red car blue', 'recolour the red rock to "
        "teal', 'change the yellow car to red'): select the OBJECT by description with "
        "selection.samText {text:'red car'} (carry fallbackColor {source hex} so the "
        "client falls back to a colour-range selection only if the mask is empty), then "
        "recolour the masked region with adjustments.applyMasked {hue:<source->target "
        "hue delta in degrees>, saturation:0, mode:'hue-saturation'} so shading, "
        "highlights and reflections are preserved. NEVER selection.fill (a flat fill "
        "destroys shading) and NEVER a whole-image adjustments.apply tint. If the "
        "target cannot be reached by a hue rotation (white/black/grey) or no source "
        "colour is named ('make the flowers bright pink'), select the object the same "
        "way and recolour the masked region with ai_edit_image mode='masked'. "
        "Named reference look ('make it look like a Wes Anderson film', 'film noir', "
        "'vintage Kodachrome'): apply the matching look recipe — Wes Anderson = pastel "
        "+ warm highlights + soft contrast; film noir = high-contrast black and white "
        "with deep shadows + a vignette; Kodachrome = warm saturated colour + contrast "
        "+ a light vignette. Strong styles ('cyberpunk', 'Studio Ghibli') use "
        "ai_edit_image mode='instruction' with a prompt that restyles the scene and "
        "keeps the subject; never a generic single adjustment. "
        "A vague prompt with no concrete defect ('clean this up') -> return "
        "{\"reply\": <question>, \"steps\": [], \"ask_user\": {\"question\": <q>, "
        "\"options\": [{\"label\":..,\"description\":..}, ...]}} with 2-3 concrete "
        "options (auto tone / brighten / cool) instead of guessing."
    )


def as_json() -> Dict[str, Any]:
    """The serialisable guide served to the browser client."""
    return {
        "version": STYLE_GUIDE_VERSION,
        "new_ops": NEW_OPS,
        "intents": [
            {"id": r["id"], "keywords": r["keywords"], "steps": r["steps"],
             "reply": r["reply"]}
            for r in INTENT_ROUTES
        ],
        "looks": [
            {"id": lk["id"], "keywords": lk["keywords"],
             "mode": lk.get("mode"), "reply": lk["reply"],
             **({"steps": lk["steps"]} if lk.get("steps") else {}),
             **({"prompt": lk["prompt"]} if lk.get("prompt") else {})}
            for lk in LOOKS
        ],
        "ambiguous_options": [dict(o) for o in _AMBIGUOUS_OPTIONS],
        "rules": {
            "low_key": "At most one global lift; prefer shadows-highlights; "
                       "never stack; protect highlights.",
            "temperature_sign": "cool/moody/cinematic -> negative; "
                                "warm/golden/summer -> positive.",
            "portrait": "No leading photo.autoTone (only if the photo is actually "
                        "under/over-exposed): selection.face -> retouch.skin "
                        "{smooth<=14, opacity<=0.45} -> selection.deselect; teeth: "
                        "selection.face -> retouch.teeth; never brighten the face, "
                        "never a global brightness lift AFTER retouch.skin.",
            "generic_look": "make it pretty/pop/professional/film -> decisive 3-4 "
                            "steps: photo.autoTone -> vibrance 20-30 -> clarity -> "
                            "optional vignette.",
            "restore": "old/faded photo -> photo.autoTone + contrast (brightness 0) "
                       "+ white-balance/tint + vibrance + slight sharpen; never a "
                       "large exposure lift.",
            "format": "aspect request -> document.crop to the aspect then "
                      "document.resizeCanvas; extend -> pad canvas, keep the "
                      "original opaque on top, ai.generativeExpand (mode "
                      "instruction, feather the seam) inpaints ONLY the new border "
                      "when a provider exists else document.resizeCanvas (honest "
                      "edge fill).",
            "generative_content": "Most generative edits use ai_edit_image "
                      "mode='instruction' on the WHOLE image (no mask, 8 steps) with a "
                      "prompt naming what stays unchanged: background/scene replace, "
                      "season/weather change (snowy/autumn/rain/fog), adding an "
                      "object/element (wildflowers, sunglasses, rainbow, clouds), an "
                      "explicit 'generative fill', removing diffuse distractions. "
                      "mode='masked' ONLY for explicit regional "
                      "removal of a clearly identified object ('remove the person on "
                      "the left') — select it first, tight mask, fall back to "
                      "instruction if the mask is tiny/empty. generate_image ONLY for "
                      "creating a brand-new image; never use it to add content to a "
                      "photo or place it as an opaque full layer.",
            "text": "text.add (+ layers.addStyle drop-shadow); never generative, never "
                    "the needs-provider message.",
            "psd": "'save as PSD' -> document.savePsd.",
            "structural": "Exact ops, never a generic adjustment: fill -> selection.fill "
                    "(selection) or fill.flood {color,x,y}; existing text size/colour -> "
                    "text.edit; colour overlay -> layers.addStyle {type:'color-overlay'}; "
                    "align -> layers.align {mode}; rotate/flip the active layer -> "
                    "layer.rotate/layer.flip, the whole image/canvas -> document.rotate/"
                    "document.flip; copy/paste the selection -> selection.copy/paste; "
                    "flatten -> layers.flatten; merge down -> layers.mergeDown.",
            "recolor": "targeted recolour -> selection.samText {text:<object>, "
                    "fallbackColor:<source hex>} then adjustments.applyMasked "
                    "{hue:<source->target hue delta>, saturation:0, "
                    "mode:'hue-saturation'} (preserves shading; colour-range fallback "
                    "only if the mask is empty); white/black/grey or no source colour "
                    "-> ai_edit_image mode:'masked'. Never selection.fill; never a "
                    "whole-image adjustments.apply tint.",
            "looks": "named look -> recipe (Wes Anderson pastel/warm/soft; film noir "
                    "high-contrast B&W + vignette; Kodachrome warm saturated + vignette) "
                    "or ai_edit_image instruction (cyberpunk, Studio Ghibli); never a "
                    "generic single adjustment.",
            "ambiguous": "ask_user with 2-3 concrete options.",
        },
        "guidance": guidance(),
    }
