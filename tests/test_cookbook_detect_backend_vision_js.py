"""`_detectBackend` must not send vision models to the text-only MLX server.

The bug: the catalogs carry 260 `image-text-to-text` entries and no branch of
the serve logic looked at that. `_detectBackend` matched `mlx-community/` on
the name, returned `mlx`, and `_buildServeCmd` launched
`python -m mlx_lm.server`, which accepts no images.

Of the 705 catalog rows that reach the MLX branch, 141 are vision models and
564 are text models, so this test pins both directions: vision diverts to
`mlx_vlm`, text stays on `mlx`. It also pins the two paths that were already
correct and must not move — AWQ/FP8 vision models keep going to vLLM (which
serves VLMs natively) and GGUF ones keep going to llama.cpp (which has a
projector path) — because "no regression on those" is the whole reason the
diversion is scoped to the MLX branch.

cookbook.js cannot be imported under node: it pulls in ui.js, spinner.js and a
dozen browser-coupled modules. So the test slices `_detectBackend` out of the
real file by brace matching and runs THAT text, with the real cookbookVision.js
imported and only `_hwfitCache` / `_isWindows` stubbed. It therefore executes
the shipped implementation rather than asserting on substrings.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_COOKBOOK = _REPO / "static" / "js" / "cookbook.js"
_VISION = _REPO / "static" / "js" / "cookbookVision.js"
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _slice_function(source, signature):
    """Return `signature` plus its body, matched by counting braces."""
    start = source.index(signature)
    i = source.index("{", start)
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _detect(model, *, sys_backend="metal", windows=False):
    src = _COOKBOOK.read_text(encoding="utf-8")
    fn = _slice_function(src, "export function _detectBackend(model) {")
    fn = fn.replace("export function", "function", 1)
    js = (
        f"import {{ isVisionModel }} from '{_VISION.as_posix()}';\n"
        f"const _hwfitCache = {{ system: {{ backend: {json.dumps(sys_backend)} }} }};\n"
        f"function _isWindows() {{ return {json.dumps(windows)}; }}\n"
        f"{fn}\n"
        f"console.log(JSON.stringify(_detectBackend({json.dumps(model)})));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_mlx_vision_model_diverts_to_mlx_vlm():
    got = _detect({
        "name": "mlx-community/gemma-3-27b-it-qat-4bit",
        "quant": "mlx-4bit",
        "modality": "vision",
    })
    assert got["backend"] == "mlx_vlm"
    assert got["label"] == "MLX VLM"


def test_mlx_text_model_still_goes_to_mlx():
    got = _detect({
        "name": "mlx-community/Qwen3-8B-4bit",
        "quant": "mlx-4bit",
        "modality": "text",
    })
    assert got["backend"] == "mlx"


def test_vision_model_scanned_off_disk_diverts_on_the_name_alone():
    # No modality, no pipeline_tag: a hand-typed or freshly scanned repo id.
    got = _detect({"name": "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"})
    assert got["backend"] == "mlx_vlm"


def test_skip_vision_checkpoint_stays_on_mlx():
    got = _detect({"name": "mlx-community/Molmo-7B-D-0924-8bit-skip-vision"})
    assert got["backend"] == "mlx"


def test_apple_silicon_fallback_also_diverts_vision():
    # Plain HF repo, no "mlx" in the name: on Metal this falls through to the
    # late Apple-Silicon branch, which also used to return mlx.
    got = _detect({"name": "google/gemma-3-27b-it", "modality": "vision"}, sys_backend="metal")
    assert got["backend"] == "mlx_vlm"
    got = _detect({"name": "google/gemma-3-27b-it", "modality": "text"}, sys_backend="metal")
    assert got["backend"] == "mlx"


def test_awq_and_fp8_vision_models_keep_going_to_vllm():
    # vLLM serves VLMs natively — these were never broken and must not move.
    for name, quant in (
        ("QuantTrio/Qwen3-VL-30B-A3B-Instruct-AWQ", "AWQ-4bit"),
        ("some/Qwen2.5-VL-72B-FP8", "FP8"),
    ):
        got = _detect({"name": name, "quant": quant, "modality": "vision"}, sys_backend="cuda")
        assert got["backend"] == "vllm", name


def test_gguf_vision_models_keep_going_to_llamacpp():
    # llama.cpp has a projector path (--mmproj), so these keep their engine.
    got = _detect({
        "name": "unsloth/gemma-4-12B-it-GGUF",
        "quant": "Q4_K_M",
        "modality": "vision",
        "is_gguf": True,
    }, sys_backend="cuda")
    assert got["backend"] == "llamacpp"


def test_ollama_and_image_generation_paths_are_untouched():
    assert _detect({"name": "qwen3.5:latest"})["backend"] == "ollama"
    img = _detect({"name": "mlx-community/FLUX.1-schnell-4bit", "is_image_gen": True})
    assert img["backend"] == "mlx_image"
