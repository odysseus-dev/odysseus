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
