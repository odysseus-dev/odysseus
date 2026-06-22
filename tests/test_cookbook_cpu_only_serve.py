"""Regression guard for issue #1291 — CPU-only serve still emitted GPU-only flags.

The llama.cpp serve command builder (static/js/cookbook.js) added
`--flash-attn on` and exported `GGML_CUDA_ENABLE_UNIFIED_MEMORY=1` from
independent toggles, so a CPU-only config (`-ngl 0`, often with flash-attn left
on by an Auto profile) produced a command that mixes "zero GPU layers" with
CUDA/flash-attn and fails to start. The builder now drops those GPU-only flags
when ngl == 0, per the maintainer's guidance.

cookbook.js pulls in browser globals so it can't run under node; guard the fix
at the source level: a `_cpuOnly` gate exists and is applied to flash-attn and
the CUDA unified-memory env.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/cookbook.js"
SERVE_SRC = Path(__file__).resolve().parent.parent / "static/js/cookbookServe.js"
ROUTES_SRC = Path(__file__).resolve().parent.parent / "routes/cookbook_routes.py"


def test_cpu_only_drops_gpu_only_flags():
    text = SRC.read_text(encoding="utf-8")
    # A CPU-only flag derived from ngl == 0.
    assert re.search(r"_cpuOnly\s*=\s*String\(f\.ngl\)\.trim\(\)\s*===\s*'0'", text), \
        "expected a _cpuOnly gate derived from ngl==0"
    # flash-attn must be suppressed for CPU-only.
    assert re.search(r"if\s*\(\s*f\.flash_attn\s*&&\s*!_cpuOnly\s*\)", text), \
        "flash-attn must be gated on !_cpuOnly"
    # The CUDA unified-memory env must be suppressed for CPU-only too.
    assert "f.unified_mem && !_cpuOnly" in text, \
        "GGML_CUDA_ENABLE_UNIFIED_MEMORY must be gated on !_cpuOnly"


def test_diffusers_is_not_blocked_on_windows_dependencies_panel():
    text = SRC.read_text(encoding="utf-8")

    assert "const _winUnsupported = new Set(['hf_transfer', 'vllm', 'rembg', 'gfpgan']);" in text
    assert "new Set(['diffusers'" not in text


def test_diffusers_is_available_only_on_local_windows_serve_panel():
    text = SERVE_SRC.read_text(encoding="utf-8")

    assert "function _remoteWindowsDiffusersUnsupported(target)" in text
    assert "return !!(target?.host && target?.platform === 'windows');" in text
    assert "if (_remoteWindowsDiffusersUnsupported(target)) return [['llamacpp','llama.cpp']];" in text
    assert "return [['llamacpp','llama.cpp'],['diffusers','Diffusers']];" in text
    assert "Diffusers serving is not supported on remote Windows servers yet." in text


def test_windows_diffusers_uses_python_not_python3():
    text = SRC.read_text(encoding="utf-8")

    assert "const diffusersPy = _isWindows() ? 'python' : _py3Bin;" in text
    assert "cmd += `${diffusersPy} scripts/diffusion_server.py" in text
    assert "cmd += `python3 scripts/diffusion_server.py" not in text


def test_vllm_blank_swap_omits_swap_space_flag():
    text = SRC.read_text(encoding="utf-8")

    assert "const _swapRaw = (f.swap ?? '').toString().trim().toLowerCase();" in text
    assert "['0', 'off', 'none', 'false'].includes(_swapRaw)" in text
    assert "if (_swapRaw && !['0', 'off', 'none', 'false'].includes(_swapRaw)) cmd += ` --swap-space ${_swapRaw}`;" in text


def test_serve_preflight_uses_selected_server_not_stale_env_host():
    text = SERVE_SRC.read_text(encoding="utf-8")

    assert "function _selectedServeTarget(panel) {" in text
    assert "const _hostStr = launchTarget.host || '';" in text
    assert "(t.remoteHost || '') === _hostStr" in text
    assert "const _probeHost = (launchTarget.host || '').trim();" in text
    assert "const _portHost = (launchTarget.host || '').trim();" in text


def test_vllm_route_strips_swap_space_when_runtime_rejects_it():
    text = ROUTES_SRC.read_text(encoding="utf-8")

    assert "Setting vLLM --swap-space 0 so the runtime does not reserve CPU swap per GPU." in text
    assert "vLLM serve does not expose --swap-space; removing the flag and patching the runtime default to 0." in text
    assert "ODYSSEUS_VLLM_HELP_CMD" in text
    assert "print(shlex.join(parts[:serve_i + 1] + [\"--help\"]))" in text
    assert "eval \"$ODYSSEUS_VLLM_HELP_CMD\" 2>&1 | grep -q -- \"--swap-space\"" in text
    assert "eval \"$ODYSSEUS_SERVE_CMD\"" in text
