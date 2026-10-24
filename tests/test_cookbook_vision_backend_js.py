"""Behavioral tests for vision (image-text-to-text) model detection.

Background: the catalogs carry 260 entries tagged `image-text-to-text`, but no
branch of the serve logic looked at that tag. `_detectBackend` matched
`mlx-community/` on the name and routed them to `mlx`, so `_buildServeCmd`
launched them with `python -m mlx_lm.server` — a text-only server. The model
came up and looked healthy while there was no way to hand it an image.

Detection needs two signals because each is insufficient alone, measured over
the 1553 real catalog records:

  * `pipeline_tag` covers the catalogs but is absent for models scanned off
    disk (the common case: serving something already downloaded), and is wrong
    on a handful of entries (`LFM2-VL-450M-6bit` is tagged text-generation).
  * the name marker has near-perfect precision but only 32.3% recall (84/260) —
    most VLMs carry no marker (`gemma-3-27b-it-qat-4bit`, `diffusiongemma-26B`).

So the rule is their OR, minus an explicit `skip-vision` opt-out.

Driven through `node --input-type=module` so the real module runs, same idiom
as test_cookbook_port_parsing_js.py. cookbookVision.js is dependency-free for
exactly that reason — cookbook.js itself pulls in browser globals and cannot be
imported under node.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MODULE = _REPO / "static" / "js" / "cookbookVision.js"
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _run(expr):
    js = (
        f"import {{ isVisionModel, VISION_PIPELINE_TAG }} from '{_MODULE.as_posix()}';"
        f"console.log(JSON.stringify({expr}));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def _vision(model):
    return _run(f"isVisionModel({json.dumps(model)})")


def test_pipeline_tag_is_the_primary_signal():
    assert _vision({"name": "mlx-community/gemma-3-27b-it-qat-4bit",
                    "pipeline_tag": "image-text-to-text"}) is True
    assert _vision({"name": "mlx-community/Qwen3-8B-4bit",
                    "pipeline_tag": "text-generation"}) is False


def test_normalized_modality_field_is_honored():
    # analyze_model() returns an explicit field whitelist that drops
    # pipeline_tag, so the catalog hands the UI a derived `modality` instead.
    assert _vision({"name": "mlx-community/whatever-4bit", "modality": "vision"}) is True
    assert _vision({"name": "mlx-community/whatever-4bit", "modality": "text"}) is False


def test_name_marker_covers_models_scanned_off_disk():
    # No pipeline_tag at all: this is what a locally downloaded model looks
    # like when it comes back from the cached-models scan.
    for name in (
        "mlx-community/Qwen2.5-VL-3B-Instruct-4bit",
        "mlx-community/llava-1.5-7b-4bit",
        "mlx-community/moondream2-mlx",
        "OpenGVLab/InternVL-Chat-V1-5",
        "mistral-community/pixtral-12b",
        "HuggingFaceTB/SmolVLM-Instruct",
        "google/paligemma-3b-mix-224",
        "HuggingFaceM4/idefics2-8b",
        "openbmb/MiniCPM-V-2_6",
        "some/model-VLM-7b",
    ):
        assert _vision({"name": name}) is True, name


def test_name_marker_does_not_fire_on_text_models():
    for name in (
        "mlx-community/Qwen3-8B-4bit",
        "mlx-community/Llama-3.3-70B-Instruct-4bit",
        "lmsys/vicuna-13b-v1.5",
        "mlx-community/DeepSeek-V4-Flash-4bit",
        "some/navlm-7b",
        "some/revlon-3b",
    ):
        assert _vision({"name": name}) is False, name


def test_skip_vision_is_an_explicit_opt_out():
    # mlx-community/Molmo-7B-D-0924-8bit-skip-vision is a real catalog entry:
    # the vision tower is stripped, so it is a text model and must keep going
    # to mlx_lm. The opt-out wins over both signals.
    assert _vision({"name": "mlx-community/Molmo-7B-D-0924-8bit-skip-vision"}) is False
    assert _vision({"name": "mlx-community/Molmo-7B-D-0924-8bit-skip-vision",
                    "pipeline_tag": "image-text-to-text"}) is False


def test_repo_id_is_inspected_too():
    # Cached/local rows identify the model by repo_id, not name.
    assert _vision({"repo_id": "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"}) is True


def test_the_containing_directory_is_not_the_model():
    # `path` never carries the model's identity: the HF-cache scan sets it to
    # the cache root, the local-directory scan to the PARENT of the model
    # folder (the folder's own name is repo_id), Ollama rows to the literal
    # "ollama", and catalog rows have no such field. Reading the marker there
    # turns a user's folder naming into model metadata, in both directions —
    # and the suppressing direction sends a genuine VLM back to the text-only
    # server, which is the bug this module exists to prevent.
    assert _vision({"repo_id": "Qwen3-8B-4bit", "path": "/Users/x/vision-models"}) is False
    assert _vision({"repo_id": "Llama-3.3-70B-Instruct-4bit", "path": "/mnt/vl/models"}) is False
    assert _vision({"repo_id": "Qwen2.5-VL-3B-Instruct-4bit",
                    "path": "/Users/x/skip-vision-archive"}) is True
    # An explicit opt-out in the model's own id still wins.
    assert _vision({"repo_id": "mlx-community/Molmo-7B-D-0924-8bit-skip-vision",
                    "path": "/Users/x/models"}) is False


def test_malformed_input_is_not_vision():
    assert _vision(None) is False
    assert _vision({}) is False
    assert _run("isVisionModel('a string')") is False
    assert _vision({"name": None, "pipeline_tag": None}) is False


def test_pipeline_tag_constant_is_exported():
    assert _run("VISION_PIPELINE_TAG") == "image-text-to-text"
