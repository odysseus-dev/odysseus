import json
import subprocess
import sys

import pytest
from fastapi import HTTPException

from routes.cookbook_helpers import (
    _cached_model_scan_script,
    _append_llama_cpp_linux_accel_build_lines,
    _append_serve_exit_code_lines,
    _append_serve_preflight_exit_lines,
    _llama_cpp_rebuild_cmd,
    _local_tooling_path_export,
    _pip_install_attempt,
    _pip_install_fallback_chain,
    _ollama_bind_from_cmd,
    _safe_env_prefix,
    _user_shell_path_bootstrap,
    _venv_safe_local_pip_install_cmd,
    _validate_gpus,
    _validate_repo_id,
    _validate_serve_cmd,
    _validate_serve_model_id,
    _validate_ssh_port,
)


def test_safe_env_prefix_accepts_quoted_venv_path():
    assert (
        _safe_env_prefix("source '~/vllm-env/bin/activate'")
        == '[ -f "$HOME/vllm-env/bin/activate" ] && source "$HOME/vllm-env/bin/activate" || true'
    )


def test_safe_env_prefix_leaves_compound_conda_prefix_unchanged():
    prefix = 'eval "$(conda shell.bash hook)" && conda activate qwen35'
    assert _safe_env_prefix(prefix) == prefix


def test_safe_env_prefix_rejects_freeform_shell():
    with pytest.raises(HTTPException):
        _safe_env_prefix("echo ok; curl https://example.invalid")


def test_safe_env_prefix_accepts_powershell_activation_path():
    assert (
        _safe_env_prefix("& 'C:\\Users\\me\\venv\\Scripts\\Activate.ps1'")
        == "& 'C:\\Users\\me\\venv\\Scripts\\Activate.ps1'"
    )


def test_validate_ssh_port_rejects_shell_payload():
    with pytest.raises(HTTPException):
        _validate_ssh_port("22; touch /tmp/pwned")
    assert _validate_ssh_port("2222") == "2222"


def test_validate_gpus_accepts_indexes_only():
    assert _validate_gpus("0,1,2") == "0,1,2"
    with pytest.raises(HTTPException):
        _validate_gpus("0; rm -rf /")


def test_validate_repo_id_stays_strict_for_hf_downloads():
    assert _validate_repo_id("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    with pytest.raises(HTTPException):
        _validate_repo_id("DeepSeek-R1-UD-IQ4_XS")


def test_validate_serve_model_id_accepts_cached_local_model_names():
    assert _validate_serve_model_id("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    assert _validate_serve_model_id("DeepSeek-R1-UD-IQ4_XS") == "DeepSeek-R1-UD-IQ4_XS"
    with pytest.raises(HTTPException):
        _validate_serve_model_id("../escape")


def test_local_tooling_path_export_prepends_interpreter_bin():
    """The cookbook runners must see the venv's bin (where `hf`/`python` live)
    so tmux shells can find them without an activated venv."""
    assert (
        _local_tooling_path_export("/opt/venv/bin/python")
        == 'export PATH="/opt/venv/bin:$PATH"'
    )


def test_local_tooling_path_export_preserves_spaces_and_expands_path():
    line = _local_tooling_path_export("/Users/John Smith/.venv/bin/python3")
    assert line == 'export PATH="/Users/John Smith/.venv/bin:$PATH"'
    assert line.endswith(':$PATH"')  # $PATH stays expandable in double quotes


def test_pip_install_fallback_chain_prefers_venv_safe_install():
    chain = _pip_install_fallback_chain("huggingface_hub", upgrade=True)
    # First attempt: plain install, wrapped in status-preserving subshell
    assert chain.startswith("bash -c '")
    assert "python3 -m pip install -q -U huggingface_hub" in chain
    # Second attempt: --user --break-system-packages, also wrapped
    assert "--user --break-system-packages" in chain
    assert "python3 -m pip install --user --break-system-packages -q -U huggingface_hub" in chain
    # No bare `| tail` (which would mask pip's exit code)
    assert "| tail" not in chain
    # Negated venv check with && — so failure in a venv propagates instead of
    # being masked as success by the venv_check's exit-0.
    assert "! python3 -c" in chain
    # The group uses && (not ||) between venv check and user attempt
    assert "&&" in chain


def test_pip_install_fallback_chain_allows_custom_python_command():
    chain = _pip_install_fallback_chain("hf_transfer", python_cmd="pip", upgrade=False)
    assert "pip install -q hf_transfer" in chain
    assert "pip install --user --break-system-packages -q hf_transfer" in chain
    # venv check uses the python executable derived from the pip command
    assert 'python -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)"' in chain
    # Both attempts are wrapped in bash -c subshells
    assert chain.count("bash -c '") == 2


def test_pip_install_fallback_chain_propagates_failure_in_venv():
    """When base install fails inside a venv, the chain must exit non-zero.

    The old `{ venv_check || user }` shape from #903 masked the failure:
    venv_check exited 0 (in venv), || short-circuited, and the group
    reported success even though nothing was installed.  The negated
    `{ ! venv_check && user }` shape propagates the failure correctly.
    """
    import shlex
    py = shlex.quote(sys.executable)
    # Use the venv python so venv_check detects we're in a venv.
    # Base install fails, venv_check exits 0, negated to 1,
    # && skips user, group exits 1.
    script = (
        f"{py} -c 'import sys; sys.exit(1)' || "
        f"{{ ! {py} -c \"import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)\" "
        f"&& echo user_attempt; }}"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=10,
    )
    assert "user_attempt" not in result.stdout
    assert result.returncode != 0, "Chain should propagate failure when base fails in venv"


def test_pip_install_fallback_chain_tries_user_outside_venv():
    """When base install fails outside a venv, the chain should try --user."""
    # Force "not in venv" by making venv_check return 1 directly.
    script = (
        "bash -c '"
        "python3 -c \"import sys; sys.exit(1)\" || "
        "{ ! python3 -c \"import sys; sys.exit(1)\" "  # venv_check=1 → negated to 0 → user runs
        "&& echo user_attempt; }"
        "'"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=10,
    )
    assert "user_attempt" in result.stdout, "Chain should try --user when not in venv and base fails"


def test_pip_install_fallback_chain_quotes_extras_spec():
    """An extras spec like ``llama-cpp-python[server]`` must be shell-quoted so
    bash does not treat the brackets as a glob, and the ``[server]`` extra
    (which pulls in starlette_context for ``python -m llama_cpp.server``) is
    actually installed instead of a bare ``llama-cpp-python`` (issue #730)."""
    chain = _pip_install_fallback_chain("llama-cpp-python[server]", python_cmd="pip")
    # Quoted in both the plain and the --user attempt.
    assert chain.count("'llama-cpp-python[server]'") == 2
    # Never the unquoted form (bracket-glob risk).
    assert "install -q llama-cpp-python[server]" not in chain
    # A plain package name is still passed through unquoted (no regression).
    plain = _pip_install_fallback_chain("hf_transfer", python_cmd="pip")
    assert "install -q hf_transfer" in plain


def test_serve_runner_installs_llama_cpp_server_extra():
    """The llama.cpp serve auto-install must request the ``[server]`` extra in
    every path (issue #730): a bare ``llama-cpp-python`` passes the
    ``import llama_cpp`` guard, so ``python -m llama_cpp.server`` then crashes
    with ``ModuleNotFoundError: No module named 'starlette_context'`` and the
    extra is never reinstalled."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "routes" / "cookbook_routes.py").read_text(encoding="utf-8")
    # No serve path may install a bare (extra-less) llama-cpp-python.
    assert "pip install llama-cpp-python " not in src
    assert "_pip_install_fallback_chain('llama-cpp-python'" not in src
    # The [server] extra is requested in the build/fallback paths.
    assert "'llama-cpp-python[server]'" in src
    assert "_pip_install_fallback_chain('llama-cpp-python[server]'" in src


def test_venv_safe_local_pip_install_strips_user_flags_only_for_local_venv():
    cmd = 'python3 -m pip install -U --user --break-system-packages "vllm"'

    cleaned = _venv_safe_local_pip_install_cmd(cmd, local=True, in_venv=True)

    assert cleaned == "python3 -m pip install -U vllm"
    assert _venv_safe_local_pip_install_cmd(cmd, local=False, in_venv=True) == cmd
    assert _venv_safe_local_pip_install_cmd(cmd, local=True, in_venv=False) == cmd


def test_pip_install_attempt_wraps_in_status_preserving_subshell():
    """Each pip attempt must be a bash -c subshell that captures output,
    prints tail, cleans up, and exits with pip's real status — not tail's."""
    snippet = _pip_install_attempt("pip install -q huggingface_hub")
    assert snippet.startswith("bash -c '")
    assert "$(mktemp)" in snippet
    assert "_rc=$?" in snippet
    assert "tail -5" in snippet
    assert "rm -f" in snippet
    assert "exit $_rc" in snippet


def test_pip_install_attempt_no_bare_pipe_tail():
    """A bare `| tail` pipeline would mask pip's exit code — must not appear."""
    snippet = _pip_install_attempt("pip install -q huggingface_hub")
    assert "| tail" not in snippet


def test_pip_install_attempt_failure_propagates_real_exit_code():
    """Run the generated snippet against a deliberately broken pip install
    to confirm the subshell exits with pip's non-zero status."""
    snippet = _pip_install_attempt("python3 -m pip install __nonexistent_package_12345__")
    result = subprocess.run(
        ["bash", "-c", snippet],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0, "pip install of a nonexistent package should fail"


def test_pip_install_attempt_success_exits_zero():
    """When pip succeeds, the subshell should exit 0."""
    snippet = _pip_install_attempt("python3 -c 'pass'")
    result = subprocess.run(
        ["bash", "-c", snippet],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0


def test_pip_install_attempt_surfaces_stderr_on_failure():
    """On failure, the last 5 lines of pip output should appear in stdout."""
    snippet = _pip_install_attempt("python3 -m pip install __nonexistent_package_12345__")
    result = subprocess.run(
        ["bash", "-c", snippet],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # pip's error message should be visible in the output (not swallowed)
    combined = result.stdout + result.stderr
    assert "nonexistent" in combined.lower() or result.returncode != 0


def test_local_tooling_path_export_converts_windows_paths_for_bash():
    line = _local_tooling_path_export(r"C:\Users\Jane Dev\.venv\Scripts\python.exe")
    assert line == 'export PATH="/c/Users/Jane Dev/.venv/Scripts:$PATH"'
    assert "C:" not in line


def test_user_shell_path_bootstrap_falls_back_to_python_on_windows_bash():
    script = "\n".join(_user_shell_path_bootstrap())
    assert 'command -v python3 >/dev/null 2>&1 || python3() { python "$@"; }' in script


def test_serve_preflight_failure_keeps_tmux_pane_visible():
    """Dependency preflight failures should remain visible in tmux output.

    A bare `exit 127` kills the tmux pane before the browser/status poller can
    capture the helpful error, leaving users with a blank "crashed" card.
    """
    runner_lines = [
        'ODYSSEUS_PREFLIGHT_EXIT=""',
        'echo "ERROR: vLLM is not installed. Open Cookbook -> Dependencies and install vllm on this server, then launch again."',
        'ODYSSEUS_PREFLIGHT_EXIT=127',
    ]
    _append_serve_preflight_exit_lines(runner_lines, keep_shell_open=True)
    script = "\n".join(runner_lines)

    assert "ERROR: vLLM is not installed" in script
    assert 'ODYSSEUS_PREFLIGHT_EXIT=127' in script
    assert 'echo "=== Process exited with code $ODYSSEUS_PREFLIGHT_EXIT ==="' in script
    assert 'exec "${SHELL:-/bin/bash}"' in script
    assert "exit 127" not in script


def test_serve_runner_preserves_command_exit_code():
    """The serve wrapper must capture `$?` before any echo resets it."""
    runner_lines = ["vllm serve Qwen/Qwen3.6-35B-A3B-NVFP4 --host 0.0.0.0 --port 8000"]
    _append_serve_exit_code_lines(runner_lines, keep_shell_open=True)
    script = "\n".join(runner_lines)

    assert "ODYSSEUS_CMD_EXIT=$?" in script
    assert 'echo "=== Process exited with code $ODYSSEUS_CMD_EXIT ==="' in script
    assert 'echo "=== Process exited with code $? ==="' not in script


def test_pip_serve_runner_emits_download_ok_before_exit_marker():
    """Dependency installs run through the serve wrapper need the download marker."""
    runner_lines = ["python3 -m pip install llama-cpp-python"]
    _append_serve_exit_code_lines(runner_lines, keep_shell_open=False, is_pip_install=True)
    script = "\n".join(runner_lines)

    assert 'echo "DOWNLOAD_OK"' in script
    assert script.index('echo "DOWNLOAD_OK"') < script.index("=== Process exited with code")
    assert 'exit "$ODYSSEUS_CMD_EXIT"' in script


def test_validate_serve_cmd_accepts_vllm_kv_cache_dtype():
    cmd = (
        "CUDA_VISIBLE_DEVICES=0,1 vllm serve nvidia/Qwen3.6-35B-A3B-NVFP4 "
        "--host 0.0.0.0 --port 8000 --tensor-parallel-size 2 "
        "--max-model-len 4096 --dtype auto --kv-cache-dtype fp8"
    )

    assert _validate_serve_cmd(cmd) == cmd


def test_validate_serve_cmd_accepts_llama_advanced_controls():
    cmd = (
        "MODEL_FILE=$(printf %s ${HOME}'/.cache/huggingface/hub/models--Qwen--Qwen3-GGUF/snapshots/model.gguf') "
        '&& { [ -n "$MODEL_FILE" ] && [ -f "$MODEL_FILE" ]; } '
        '|| { echo "ERROR: No GGUF found on this host."; exit 1; } && '
        'GGML_CUDA_ENABLE_UNIFIED_MEMORY=1 CUDA_VISIBLE_DEVICES=0,1 llama-server '
        '--model "$MODEL_FILE" --host 0.0.0.0 --port 8000 -ngl 99 -c 131072 '
        '--n-cpu-moe 0 --cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on '
        '--fit off --split-mode tensor --tensor-split 50,50 --main-gpu 0 '
        '--parallel 1 --batch-size 2048 --ubatch-size 512 --no-mmap --no-warmup '
        '--spec-type draft-mtp --spec-draft-n-max 3 '
        '|| python3 -m llama_cpp.server --model "$MODEL_FILE" --host 0.0.0.0 --port 8000'
    )

    assert _validate_serve_cmd(cmd) == cmd


def test_ollama_serve_defaults_to_loopback_bind():
    assert _ollama_bind_from_cmd("ollama serve") == ("127.0.0.1", "11434")
    assert _ollama_bind_from_cmd("ollama run qwen2.5:0.5b") == ("127.0.0.1", "11434")


def test_ollama_serve_accepts_remote_reachable_default_bind():
    assert (
        _ollama_bind_from_cmd("ollama serve", default_host="0.0.0.0")
        == ("0.0.0.0", "11434")
    )


def test_ollama_serve_preserves_explicit_bind_opt_in():
    assert (
        _ollama_bind_from_cmd("OLLAMA_HOST=0.0.0.0:12345 ollama serve")
        == ("0.0.0.0", "12345")
    )
    assert (
        _ollama_bind_from_cmd("OLLAMA_HOST=[::1]:11435 ollama serve")
        == ("[::1]", "11435")
    )


def test_ollama_serve_rejects_unsafe_bind_values():
    assert (
        _ollama_bind_from_cmd("OLLAMA_HOST='$HOST:11434' ollama serve")
        == ("127.0.0.1", "11434")
    )
    assert (
        _ollama_bind_from_cmd("OLLAMA_HOST=127.0.0.1:99999 ollama serve")
        == ("127.0.0.1", "11434")
    )


def test_llama_cpp_linux_bootstrap_prefers_rocm_before_cuda():
    runner_lines = []
    _append_llama_cpp_linux_accel_build_lines(runner_lines)
    script = "\n".join(runner_lines)

    assert 'command -v hipconfig &>/dev/null || [ -d /opt/rocm ] || [ -n "$ROCM_PATH" ] || [ -n "$HIP_PATH" ]' in script
    assert 'cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_HIP=ON' in script
    assert 'cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON' in script
    assert script.index('DGGML_HIP=ON') < script.index('DGGML_CUDA=ON')
    assert 'ROCm/HIP detected — building llama-server with HIP support' in script


def test_llama_cpp_linux_bootstrap_checks_cudart_before_cuda_build():
    """cudart helper and all required paths must appear before the CUDA cmake command."""
    runner_lines = []
    _append_llama_cpp_linux_accel_build_lines(runner_lines)
    script = "\n".join(runner_lines)

    assert '_odysseus_has_cudart' in script
    assert "grep -q 'libcudart\\.so'" in script
    # lib64 and lib variants for CUDA_HOME and /usr/local/cuda
    assert '$_cuh/lib64/libcudart.so' in script
    assert '$_cuh/lib/libcudart.so' in script
    assert '/usr/local/cuda/lib64/libcudart.so' in script
    assert '/usr/local/cuda/lib/libcudart.so' in script
    # pip-installed nvidia runtime wheel sibling path
    assert 'cuda_runtime/lib/libcudart.so' in script
    # entire helper definition precedes the CUDA cmake invocation
    assert script.index('_odysseus_has_cudart') < script.index('DGGML_CUDA=ON')


def test_llama_cpp_linux_bootstrap_cuda_cmake_present_when_cudart_found():
    """The CUDA cmake command must still be present (inside the cudart-present branch)."""
    runner_lines = []
    _append_llama_cpp_linux_accel_build_lines(runner_lines)
    script = "\n".join(runner_lines)

    assert 'cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON' in script
    assert 'CUDA nvcc + cudart found' in script


def test_llama_cpp_linux_bootstrap_nvcc_without_cudart_warns_and_falls_back():
    """When nvcc exists but cudart is absent, the script must warn and use CPU-only cmake."""
    runner_lines = []
    _append_llama_cpp_linux_accel_build_lines(runner_lines)
    script = "\n".join(runner_lines)

    assert 'WARNING: nvcc found but CUDA runtime (libcudart.so) is not visible — building llama-server for CPU only.' in script
    assert 'GPU inference will not be available for this llama.cpp build.' in script
    assert 'libcudart is installed' in script
    # The CPU-only cmake fallback must appear inside the nvcc branch (before the
    # outer else that handles no-GPU-toolchain). Verify it appears at least once
    # before the outer "no HIP/CUDA toolchain" warning.
    cpu_cmake = 'cmake -B build -DCMAKE_BUILD_TYPE=Release &&'
    no_toolchain_warn = 'WARNING: no HIP/CUDA toolchain found'
    assert cpu_cmake in script
    assert script.index(cpu_cmake) < script.index(no_toolchain_warn)


def test_llama_cpp_linux_bootstrap_uses_single_shell_continuations():
    runner_lines = []
    _append_llama_cpp_linux_accel_build_lines(runner_lines)

    assert not any(line.endswith("\\\\") for line in runner_lines)


def test_llama_cpp_linux_bootstrap_keeps_cpu_fallback_when_no_gpu_toolchain():
    runner_lines = []
    _append_llama_cpp_linux_accel_build_lines(runner_lines)
    script = "\n".join(runner_lines)

    assert 'WARNING: no HIP/CUDA toolchain found — building llama-server for CPU only.' in script
    assert 'Install ROCm for AMD GPUs or vLLM/CUDA tooling for NVIDIA' in script


def test_llama_cpp_rebuild_cmd_clears_cached_build_paths():
    cmd = _llama_cpp_rebuild_cmd()

    # Must remove both the cached symlink and the build dir the serve bootstrap
    # links/creates, so the next serve recompiles from source.
    assert 'rm -f "$HOME/bin/llama-server"' in cmd
    assert 'rm -rf "$HOME/llama.cpp/build"' in cmd
    # Recreates ~/bin so a never-served host does not error on a missing dir.
    assert 'mkdir -p "$HOME/bin"' in cmd
    # Diagnosis-only on the destructive side: it must not install or fetch.
    assert 'pip install' not in cmd
    assert 'git clone' not in cmd
    assert 'curl' not in cmd and 'wget' not in cmd


def test_llama_cpp_rebuild_cmd_runs_clean_on_a_fresh_home(tmp_path):
    """The command should succeed even when neither path exists yet."""
    import os

    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    result = subprocess.run(
        ["bash", "-c", _llama_cpp_rebuild_cmd()],
        capture_output=True, text=True, env=env, timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "bin").is_dir()
    assert "Cleared the cached llama.cpp build" in result.stdout


def test_cached_model_scan_reports_plain_dir_gguf(tmp_path):
    """Custom download dirs may sit inside the HF hub cache and contain plain
    per-model folders. They must show up in Serve and keep the GGUF signal."""
    plain = tmp_path / "Qwen3.6-27B"
    plain.mkdir()
    (plain / "Qwen3.6-27B-Q4_K_M.gguf").write_bytes(b"gguf")
    (plain / "Qwen3.6-27B-Q5_K_M-00001-of-00003.gguf").write_bytes(b"part1")
    (plain / "Qwen3.6-27B-Q5_K_M-00002-of-00003.gguf").write_bytes(b"part2")
    (plain / "Qwen3.6-27B-Q5_K_M-00003-of-00003.gguf").write_bytes(b"part3")
    (plain / "Qwen3.6-27B-Q6_K_XL.gguf").write_bytes(b"ggufgguf")
    (plain / "mmproj-BF16.gguf").write_bytes(b"projector")

    hf_internal = tmp_path / "models--Qwen--Qwen3.6-27B"
    (hf_internal / "snapshots" / "abc").mkdir(parents=True)
    (hf_internal / "snapshots" / "abc" / "model.safetensors").write_bytes(b"safe")

    scan_py = tmp_path / "scan_cache.py"
    scan_py.write_text(_cached_model_scan_script([str(tmp_path)]), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(scan_py)],
        check=True,
        capture_output=True,
        text=True,
    )

    by_repo = {m["repo_id"]: m for m in json.loads(proc.stdout)}
    assert "models--Qwen--Qwen3.6-27B" not in by_repo
    assert by_repo["Qwen3.6-27B"]["is_local_dir"] is True
    assert by_repo["Qwen3.6-27B"]["is_gguf"] is True
    ggufs = by_repo["Qwen3.6-27B"]["gguf_files"]
    assert [f["rel_path"] for f in ggufs] == [
        "Qwen3.6-27B-Q4_K_M.gguf",
        "Qwen3.6-27B-Q5_K_M-00001-of-00003.gguf",
        "Qwen3.6-27B-Q6_K_XL.gguf",
        "mmproj-BF16.gguf",
    ]
    assert [f["role"] for f in ggufs] == ["model", "model", "model", "projector"]
    assert ggufs[0]["quant"] == "Q4_K_M"
    assert ggufs[1]["quant"] == "Q5_K_M"
    assert ggufs[1]["split"] is True
    assert ggufs[1]["parts"] == 3
    assert ggufs[1]["size_bytes"] == len(b"part1part2part3")
    assert ggufs[2]["quant"] == "Q6_K_XL"
    assert ggufs[3]["quant"] == "BF16"


# ── #1219 / #1459: keep big dependency wheel builds off the home pip cache ──

def test_pip_install_no_cache_injects_flag():
    from routes.cookbook_helpers import _pip_install_no_cache
    assert _pip_install_no_cache("python -m pip install vllm") == \
        "python -m pip install --no-cache-dir vllm"
    assert _pip_install_no_cache("pip install -q huggingface-hub") == \
        "pip install --no-cache-dir -q huggingface-hub"


def test_pip_install_no_cache_is_idempotent_and_scoped():
    from routes.cookbook_helpers import _pip_install_no_cache
    # already present -> unchanged
    already = "pip install --no-cache-dir vllm"
    assert _pip_install_no_cache(already) == already
    # not a pip install -> unchanged
    assert _pip_install_no_cache("vllm serve --model x") == "vllm serve --model x"
    assert _pip_install_no_cache("") == ""
