"""Guard the PXA recipes surfaced in Cookbook -> Dependencies.

PXQ files (github.com/poisonxa16/pxa) cannot be loaded by stock llama.cpp or
read by vLLM. The llama_cpp and vllm rows therefore carry a model-specific
recipe for PXQ models, next to the generic one: the PXA engine tarball for a
PXQ GGUF, and the PXA vLLM sidecar image for a converted PXQ checkpoint.

These lock what a copy-paste user depends on: PXQ model ids pick the PXA
recipes and nothing else does, the tarball recipe checks its host floors
before the 1.3 GB download and verifies the sha256 before unpacking, and the
images are pinned to the release tag rather than a floating tag.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RECIPES_JS = ROOT / "static" / "js" / "cookbook-deps-recipes.js"

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _source():
    return RECIPES_JS.read_text(encoding="utf-8")


def _node_eval(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _pick(backend, model_id):
    return _node_eval(
        f"""
        import {{ pickRecipe, recipeCommands }} from './static/js/cookbook-deps-recipes.js';
        const r = pickRecipe({json.dumps(backend)}, {json.dumps(model_id)});
        console.log(JSON.stringify({{
          label: r.label,
          pip: recipeCommands(r, 'pip'),
          docker: recipeCommands(r, 'docker'),
        }}));
        """
    )


def test_pxa_images_are_pinned_to_the_release_tag():
    """The engine image and the sidecar images are published per release tag
    (pxa:<tag>). The vLLM sidecar images are tagged per feature release
    (pxa-vllm:sm60-v2026.10, pxa-vllm:sm70-v2026.10) and not rebuilt for patch
    releases, so that recipe pins the feature tag instead of the latest tag."""
    source = _source()

    assert 'docker pull "ghcr.io/poisonxa16/pxa:$tag"' in source
    assert 'docker pull "ghcr.io/poisonxa16/pxa-vllm:$arch-v2026.10"' in source
    assert "poisonxa16/pxa:latest" not in source
    assert "releases/latest" in source, "Resolve the tag at install time, not in this file."


@needs_node
@pytest.mark.parametrize("model_id", [
    "poisonxa/Ornith-1.5-35B-A3B-PXQ4-GGUF",
    "mistrjirka/Qwen3.8-27B-PXQ-GGUF",
])
def test_pxq_gguf_picks_the_pxa_engine_recipe(model_id):
    recipe = _pick("llama_cpp", model_id)

    assert recipe["label"].startswith("PXQ GGUF (PXA engine")
    assert any("sha256sum -c" in c for c in recipe["pip"])
    assert any("ghcr.io/poisonxa16/pxa:" in c for c in recipe["docker"])


@needs_node
def test_pxq_model_on_vllm_picks_the_sidecar_recipe():
    recipe = _pick("vllm", "poisonxa/PXA-Coder-35B-PXQ4")

    assert recipe["label"].startswith("PXQ model (PXA vLLM sidecar")
    assert any("ghcr.io/poisonxa16/pxa-vllm:" in c for c in recipe["docker"])
    # There is no pip package for the sidecar; the pip view must not pretend.
    assert not any("pip install" in c for c in recipe["pip"])


@needs_node
@pytest.mark.parametrize("backend,model_id,label", [
    ("llama_cpp", "bartowski/Qwen_Qwen3-8B-GGUF", "Any GGUF model"),
    ("llama_cpp", "", "Any GGUF model"),
    ("vllm", "Qwen/Qwen3-8B", "Any vLLM model"),
    ("vllm", "", "Any vLLM model"),
])
def test_other_models_keep_the_generic_recipes(backend, model_id, label):
    assert _pick(backend, model_id)["label"] == label


@needs_node
def test_engine_tarball_recipe_checks_floors_before_download_and_verifies_before_unpack():
    """The tarball needs Linux x86_64, glibc >= 2.35 and compute capability
    6.0/6.1/7.0; each fails at exec with an error no flag fixes, so the recipe
    refuses before spending the download. A truncated download is the common
    failure, so the release's .sha256 is checked before anything is unpacked."""
    script = "\n".join(_pick("llama_cpp", "x/Model-PXQ4-GGUF")["pip"])
    download = script.index('curl -fL -o "$tgz"')

    for floor in ("uname -m", "getconf GNU_LIBC_VERSION", "--query-gpu=compute_cap"):
        assert script.index(floor) < download, f"{floor} must be checked before the download"
    assert download < script.index("sha256sum -c") < script.index("tar xzf")


@needs_node
@pytest.mark.skipif(not shutil.which("bash"), reason="bash not on PATH")
def test_pxa_recipe_commands_parse_as_bash():
    commands = _node_eval(
        """
        import { recipesForBackend, recipeCommands } from './static/js/cookbook-deps-recipes.js';
        const out = [];
        for (const b of ['llama_cpp', 'vllm']) {
          for (const r of recipesForBackend(b)) {
            if (!/^PXQ /.test(r.label)) continue;
            for (const v of ['pip', 'docker']) out.push(...recipeCommands(r, v));
          }
        }
        console.log(JSON.stringify(out));
        """
    )
    assert len(commands) == 4
    for cmd in commands:
        subprocess.run(["bash", "-n", "-c", cmd], check=True)
        if "<<'PXA'" in cmd:
            body = cmd.split("<<'PXA'\n", 1)[1].rsplit("\nPXA", 1)[0]
            subprocess.run(["bash", "-n", "-c", body], check=True)


@needs_node
@pytest.mark.parametrize("backend,model_id,uses_venv", [
    ("llama_cpp", "x/Model-PXQ4-GGUF", False),
    ("vllm", "x/Model-PXQ4", False),
    ("llama_cpp", "bartowski/Qwen_Qwen3-8B-GGUF", True),
    ("vllm", "Qwen/Qwen3-8B", True),
])
def test_venv_activate_line_only_for_python_installs(backend, model_id, uses_venv):
    """The panel puts the venv activate line above pip-variant commands. The
    PXA pip variants install no Python package, so they opt out; every other
    recipe keeps the line."""
    got = _node_eval(
        f"""
        import {{ pickRecipe, recipeUsesVenv }} from './static/js/cookbook-deps-recipes.js';
        const r = pickRecipe({json.dumps(backend)}, {json.dumps(model_id)});
        console.log(JSON.stringify(recipeUsesVenv(r, 'pip')));
        """
    )
    assert got is uses_venv


@needs_node
def test_engine_tarball_recipe_picks_only_engine_tarballs_by_glibc():
    """A release also carries libggml-pxqn-*.tar.gz library assets, and two
    engine tarballs (default = Ubuntu 24.04 build, glibc 2.38+; ubuntu22.04
    build, glibc 2.35+). Only pxa-v* engine tarballs may be picked."""
    script = "\n".join(_pick("llama_cpp", "x/Model-PXQ4-GGUF")["pip"])

    assert "/pxa-v[^/]*linux-x86_64" in script
    assert "-ubuntu22\\.04\\.tar\\.gz" in script
    assert "libggml" not in script
    assert "2.34" not in script


@needs_node
@pytest.mark.parametrize("backend", ["llama_cpp", "vllm"])
@pytest.mark.parametrize("variant", ["pip", "docker"])
def test_pxa_recipes_are_copy_only_generic_ones_stay_runnable(backend, variant):
    """The Run button posts to /api/model/serve, which rejects newlines, `&&`,
    `$(` and any first binary outside its allowlist, so the multi-step PXA
    installers must be Copy-only. The generic pip recipes keep working."""
    out = _node_eval(
        f"""
        import {{ pickRecipe, recipeRunnable }} from './static/js/cookbook-deps-recipes.js';
        console.log(JSON.stringify({{
          pxa: recipeRunnable(pickRecipe({json.dumps(backend)}, 'PXA/Qwen3-0.6B-PXQ4'), {json.dumps(variant)}),
          generic: recipeRunnable(pickRecipe({json.dumps(backend)}, ''), {json.dumps(variant)}),
        }}));
        """
    )
    assert out == {"pxa": False, "generic": True}
