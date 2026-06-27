"""Regression guard for issue #1291 - CPU-only serve still emitted GPU-only flags.

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
ROOT = SRC.parent.parent.parent
ROUTES_SRC = ROOT / "routes/cookbook_routes.py"

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


def test_local_windows_platform_comes_from_backend_host_state():
    text = SRC.read_text(encoding="utf-8")
    routes = ROUTES_SRC.read_text(encoding="utf-8")
    running = (SRC.parent / "cookbookRunning.js").read_text(encoding="utf-8")

    assert "hostPlatform" in text
    assert "navigator.platform" not in text
    assert "hostOrTask === 'local'" in text
    assert "if (hostOrTask === 'local') return _envState.hostPlatform || '';" in text
    assert "return _envState.hostPlatform || _envState.platform || ''" not in text
    assert "s.platform = _envState.hostPlatform || '';" in text
    assert "platform: _envState.hostPlatform || ''" in text
    assert "s.platform = _envState.hostPlatform || _envState.platform || '';" not in text
    assert "platform: _envState.hostPlatform || _envState.platform || ''" not in text
    assert 'return "windows" if IS_WINDOWS else ""' in routes
    assert 'env["hostPlatform"] = _client_host_platform()' in routes
    assert "return _state_for_client({})" in routes
    assert 'env.pop("hostPlatform", None)' in routes
    assert "delete env.hostPlatform;" in running


def test_local_serve_payload_ignores_stale_env_platform():
    serve = SERVE_SRC.read_text(encoding="utf-8")
    running = (SRC.parent / "cookbookRunning.js").read_text(encoding="utf-8")

    assert "platform: host ? (server?.platform || '') : (_envState.hostPlatform || '')," in serve
    assert "platform: server?.platform || _envState.platform || ''" not in serve
    assert "const _hplatform = _host ? (_hsrv.platform || '') : (_envState.hostPlatform || '');" in running
    assert "const _hplatform = _host ? (_hsrv.platform || '') : (_envState.platform || '');" not in running


def test_local_windows_llamacpp_prefers_native_llama_server():
    text = SRC.read_text(encoding="utf-8")
    helpers = (ROOT / "routes/cookbook_helpers.py").read_text(encoding="utf-8")

    assert "Object.prototype.hasOwnProperty.call(f, 'host')" in text
    assert "const _isWin = _targetHost ? _isWindows(_targetHost) : _isWindows('local');" in text
    assert "const _localWindows = _isWin && !_targetHost;" in text
    assert "const _curHost = _targetHost;" in text
    assert "const _localWindows = _isWin && !_envState.remoteHost;" not in text
    assert "const gpuId = (f.gpus || f.gpu_id || '').toString().trim();" in text
    assert "const _lcServer = `${lcPrefix}llama-server --model" in text
    assert "if (_localWindows) {" in text
    assert "cmd += _lcServer;" in text
    assert '"llama-server.exe"' in helpers



def test_serve_command_preview_uses_selected_target_host():
    text = SERVE_SRC.read_text(encoding="utf-8")

    assert "const buildTarget = _selectedServeTarget(panel);" in text
    assert "f.host = buildTarget.host || '';" in text
    assert "f.platform = buildTarget.platform || '';" in text
    assert "const hostField = panel.querySelector('[data-field=\"host\"]');" in text
    assert "if (hostField) hostField.value = f.host;" in text


def test_local_windows_llama_server_skips_source_bootstrap():
    routes = ROUTES_SRC.read_text(encoding="utf-8")

    assert 'local_windows_llama_cmd = local_windows and ("llama_cpp" in req.cmd or "llama-server" in req.cmd)' in routes
    assert 'if ("llama_cpp" in req.cmd or "llama-server" in req.cmd) and not local_windows_llama_cmd:' in routes


def test_local_windows_llama_server_path_includes_user_wrapper_and_cuda_builds():
    routes = (ROOT / "routes/cookbook_routes.py").read_text(encoding="utf-8")

    assert 'if local_windows:' in routes
    assert (
        'export PATH="$HOME/bin:$HOME/llama.cpp/build-cuda/bin/Release:'
        '$HOME/llama.cpp/build/bin/Release:$HOME/llama.cpp/build/bin/Debug:'
        '$HOME/llama.cpp/build/bin:$PATH"'
    ) in routes


def test_serve_panel_keeps_row_markup_and_launch_cmd_assignment_executable():
    text = SERVE_SRC.read_text(encoding="utf-8").replace("\r\n", "\n")

    assert '// Row 1: Engine + Server + Env      panelHtml +=' not in text
    assert "px';        panel._cmd = cmd;" not in text
    assert '// Row 1: Engine + Server + Env\n      panelHtml += `<div class="hwfit-serve-row">`;' in text
    assert "px';\n        panel._cmd = cmd;" in text


def test_llamacpp_vision_uses_scanned_projector_instead_of_runtime_find():
    text = SERVE_SRC.read_text(encoding="utf-8")

    assert "function _projectorGgufFiles(model)" in text
    assert "const selectedProjector = _projectorGgufFiles(m)[0];" in text
    assert "f._mmproj_path = selectedProjector ? _selectedGgufExpr(m, repo, selectedProjector.rel_path) : '';" in text
    assert "const missingVisionProjector = backend === 'llamacpp' && !!f.vision && !f._mmproj_path;" in text
    assert "hwfit-serve-vision-warn" in text
    assert "!/(?:^|\\s)(?:--mmproj|--clip_model_path)\\b/.test(launchCmd)" in text
    assert "no mmproj projector is in the launch command" in text
    assert "find ${_vsearchdir} -iname 'mmproj*.gguf'" not in text
