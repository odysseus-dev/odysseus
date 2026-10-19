"""Regression guard for issue #500 - Qwen3.x multimodal models fail to serve
when the Cookbook's pip install of vLLM resolves flash-attn to the v4 rewrite.

flash-attn 4.x moves all kernels under ``flash_attn.cute.*`` and drops the
legacy ``flash_attn.ops`` API that vLLM's Qwen3.x / Gemma vision code imports
from ``vllm/model_executor/layers/rotary_embedding/common.py``
(``from flash_attn.ops.triton.rotary import apply_rotary``). On hosts where pip
resolves flash-attn into the 4.x lineage (e.g. ``flash_attn_4 4.0.0b15`` in a
fresh Docker/WSL CUDA image), the wheel installs cleanly but serving any
Qwen3.x or Gemma multimodal model crashes at engine init with
``ModuleNotFoundError: No module named 'flash_attn.ops'``.

vLLM also runs fine without flash-attn at all (native Linux hosts fall back to
the TRITON_ATTN backend), so the compatible fix is to keep flash-attn on the
2.x/3.x line pip would otherwise skip, and to make both serve-output
diagnosis mirrors (Python agent path + cookbook-diagnosis.js browser UI)
recognize the traceback and offer the downgrade repair.
"""
import re
from pathlib import Path

from routes.cookbook_helpers import (
    VLLM_FLASH_ATTN_PIN,
    _append_vllm_flash_attn_pin,
    _diagnose_serve_output,
)

ROOT = Path(__file__).resolve().parent.parent
DIAGNOSIS_SRC = ROOT / "static/js/cookbook-diagnosis.js"
RECIPES_SRC = ROOT / "static/js/cookbook-deps-recipes.js"
ROUTES_SRC = ROOT / "routes/cookbook_routes.py"


def test_pin_asks_pip_for_the_compatible_flash_attn_line():
    # flash-attn 4.x is the incompatible rewrite; anything >=2 and <4 ships the
    # legacy flash_attn.ops.triton.rotary API that vLLM's Qwen vision code
    # imports. The ceiling must cover the whole 4.x lineage including
    # prereleases like 4.0.0b15, which pip would otherwise pick.
    assert re.fullmatch(r"flash-attn[><=!~.,0-9a-zA-Z*]+", VLLM_FLASH_ATTN_PIN)
    assert "<4" in VLLM_FLASH_ATTN_PIN, "pin must exclude the entire 4.x lineage"
    assert "2" in VLLM_FLASH_ATTN_PIN, "pin should stay on the v2/v3 line"


def test_plain_vllm_install_gets_flash_attn_ceiling():
    cmd = "python3 -m pip install --user --break-system-packages --no-cache-dir vllm"
    fixed = _append_vllm_flash_attn_pin(cmd)
    assert "flash-attn" in fixed
    assert fixed.startswith(cmd)


def test_forced_reinstall_and_update_paths_get_the_ceiling_too():
    assert "flash-attn" in _append_vllm_flash_attn_pin(
        "python3 -m pip install --force-reinstall vllm"
    )
    assert "flash-attn" in _append_vllm_flash_attn_pin(
        "python3 -m pip install -U vllm transformers"
    )


def test_uv_install_and_extra_extras_vllm_get_the_ceiling():
    assert "flash-attn" in _append_vllm_flash_attn_pin(
        "uv pip install -U vllm --torch-backend auto"
    )
    assert "flash-attn" in _append_vllm_flash_attn_pin(
        "python3 -m pip install 'vllm[fronti]'"
    )


def test_existing_flash_attn_reference_is_left_alone():
    # The diagnosis downgrade repair (and a user's own pin) must win.
    cmd = 'python3 -m pip install --user --break-system-packages "flash-attn<4"'
    assert _append_vllm_flash_attn_pin(cmd) == cmd
    cmd2 = "python3 -m pip install flash_attn_origin"
    assert _append_vllm_flash_attn_pin(cmd2) == cmd2


def test_non_install_commands_are_untouched():
    serve = "vllm serve Qwen/Qwen3-Omni-30B-A3B-Instruct-AWQ --port 8000"
    assert _append_vllm_flash_attn_pin(serve) == serve
    ports = "vllm-serve --model X"  # not a pip command
    assert _append_vllm_flash_attn_pin(ports) == ports
    assert _append_vllm_flash_attn_pin("") == ""


def test_non_vllm_pip_installs_are_untouched():
    cmd = "python3 -m pip install llama-cpp-python[server]"
    assert _append_vllm_flash_attn_pin(cmd) == cmd
    cmd2 = "pip install vllm-serve-helper"  # different distribution
    assert _append_vllm_flash_attn_pin(cmd2) == cmd2


def test_flash_attn_ops_traceback_is_diagnosed():
    out = (
        "ERROR: Engine initialization failed\n"
        "  File 'vllm/model_executor/layers/rotary_embedding/common.py', line 139\n"
        "ModuleNotFoundError: No module named 'flash_attn.ops'\n"
    )
    result = _diagnose_serve_output(out)
    assert result is not None
    message = result["message"].lower()
    assert "flash-attn" in message
    assert any(str(s.get("package", "")).replace('"', "") == "flash-attn<4"
               for s in result["suggestions"]) or any(
        "flash-attn<4" in str(s) for s in result["suggestions"])


def test_serve_route_applies_the_pin_helper():
    text = ROUTES_SRC.read_text(encoding="utf-8")
    assert "_append_vllm_flash_attn_pin" in text, (
        "the /api/model/serve pip-install path must pin flash-attn for vllm"
    )
    # The threaded diagnosis mirror must recognize the traceback too (the
    # route file duplicates the helper's pattern list).
    assert "flash_attn" in text


def test_browser_diagnosis_offers_flash_attn_downgrade():
    text = DIAGNOSIS_SRC.read_text(encoding="utf-8")
    assert "flash_attn" in text, "browser diagnosis must match the flash_attn.ops traceback"
    assert re.search(
        r'pip install --user --break-system-packages "flash-attn<4"', text
    ), "the one-click browser fix must downgrade flash-attn below 4"


def test_install_recipe_commands_carry_the_ceiling():
    text = RECIPES_SRC.read_text(encoding="utf-8")
    matched = re.findall(r"uv pip install -U vllm[^']*--torch-backend auto", text)
    assert matched, "expected vllm recipe commands"
    assert all("flash-attn" in m for m in matched)

