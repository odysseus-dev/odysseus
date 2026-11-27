"""Which models are vision (image-text-to-text) models.

Single source of truth on the Python side, used by both paths that produce
model rows for the Cookbook UI:

  * the hwfit catalogs, via models._normalize_model_entry()
  * models scanned off disk, via the /api/model/cached route

It matters because the serve logic has to route vision models to a server that
accepts images. `mlx_lm.server` does not, so a vision checkpoint sent there
comes up looking healthy with no way to pass it a picture.

Two signals, because each is insufficient alone — measured over the 1553
records in services/hwfit/data/:

  * `pipeline_tag` marks 260 entries, but it is absent for anything scanned off
    disk and wrong on a few rows (`LFM2-VL-450M-6bit` ships tagged
    text-generation while being a vision-language model).
  * the name marker has near-perfect precision (5 of the 6 rows it flags
    against the tag are genuine VLMs) but only 32.3% recall (84/260): most VLMs
    carry no marker in the name at all, e.g. `gemma-3-27b-it-qat-4bit`.

So the rule is their OR, minus an explicit opt-out for checkpoints published
with the vision tower stripped (`...-skip-vision`).

This states what the MODEL declares it can do. Whether this machine can
actually serve it is a separate, runtime question — it depends on mlx-vlm being
present in the launch interpreter — and is deliberately not recorded here.

The JS side keeps a mirror of VISION_NAME_MARKERS in static/js/cookbookVision.js
for model names typed into the serve form by hand, which reach the browser
without passing through either Python path. The two lists are pinned together
by tests/test_cookbook_vision_marker_parity.py.
"""

import json
import os
import re

VISION_PIPELINE_TAG = "image-text-to-text"

# Tokens that identify a vision-language model by name. Each must appear as a
# whole delimited token, so "navlm" or "revlon" do not match on "vl".
VISION_NAME_MARKERS = (
    "vl",
    "vlm",
    "vision",
    "llava",
    "moondream",
    "internvl",
    "idefics",
    "pixtral",
    "smolvlm",
    "paligemma",
    "minicpm-v",
)

_MARKER_RE = re.compile(
    r"(?:^|[-_./])(" + "|".join(VISION_NAME_MARKERS) + r")(?:$|[-_./0-9])",
    re.IGNORECASE,
)

# Published with the vision tower removed — a text model despite the name and,
# in the catalog, despite the tag.
_SKIP_RE = re.compile(r"skip[-_.]vision", re.IGNORECASE)


def _names(entry):
    """Every field a model row identifies ITSELF by.

    `path` is deliberately excluded. It never carries the model's identity: the
    HF-cache scan sets it to the cache root, the local-directory scan to the
    parent of the model folder (the folder's own name is repo_id), Ollama rows
    to the literal "ollama", and catalog rows have no such field. Matching the
    marker against it would read how someone named a directory as if it were
    model metadata — a folder called "vision-models" would flag every text
    model inside it, and one called "skip-vision-archive" would send a genuine
    VLM back to the text-only server, which is the bug this module prevents.
    """
    if not isinstance(entry, dict):
        return ""
    parts = [entry.get("name"), entry.get("repo_id"), entry.get("id")]
    return " ".join(str(p) for p in parts if p)


def is_vision_model(entry):
    """True when the model declares image input."""
    if not isinstance(entry, dict):
        return False
    names = _names(entry)
    if _SKIP_RE.search(names):
        return False
    modality = str(entry.get("modality") or "").strip().lower()
    if modality:
        return modality == "vision"
    tag = str(entry.get("pipeline_tag") or "").strip().lower()
    if tag == VISION_PIPELINE_TAG:
        return True
    return bool(_MARKER_RE.search(names))


def vision_modality(entry):
    """"vision" or "text" — the value that goes on the row as `modality`."""
    return "vision" if is_vision_model(entry) else "text"


_VERIFIED_CACHE = {"mtime": None, "models": {}}


def verified_vlm_models():
    """Checkpoints that have actually answered a question about an image here.

    Written only by scripts/smoke_mlx_vlm.py, after a real round trip. Declaring
    image input and surviving a load are not the same thing, and a server that
    came up healthy proves neither — so this stays separate from `modality`,
    which is what the model claims.

    Missing file means nothing has been verified yet, which is not an error.
    """
    try:
        from src.constants import DATA_DIR
        path = os.path.join(DATA_DIR, "mlx_vlm_verified.json")
        mtime = os.path.getmtime(path)
    except Exception:
        _VERIFIED_CACHE.update({"mtime": None, "models": {}})
        return {}
    if _VERIFIED_CACHE["mtime"] != mtime:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            models = data if isinstance(data, dict) else {}
        except Exception:
            models = {}
        _VERIFIED_CACHE.update({"mtime": mtime, "models": models})
    return _VERIFIED_CACHE["models"]
