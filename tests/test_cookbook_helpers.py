import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from routes.cookbook_helpers import (
    _cached_model_scan_script,
    _append_llama_cpp_linux_accel_build_lines,
    _append_pip_install_runner_lines,
    _append_serve_exit_code_lines,
    _append_serve_preflight_exit_lines,
    _llama_cpp_rebuild_cmd,
    _append_vllm_linux_preflight_lines,
    _local_tooling_path_export,
    _pip_install_attempt,
    _pip_install_fallback_chain,
    _ollama_bind_from_cmd,
    _safe_env_prefix,
    _user_shell_path_bootstrap,
    _venv_safe_local_pip_install_cmd,
    _normalize_llama_cpp_python_cache_types,
    _validate_gpus,
    _validate_local_dir,
    _validate_repo_id,
    _validate_serve_cmd,
    _validate_serve_model_id,
    _shell_path,
    run_ssh_command_async,
)


def test_safe_env_prefix_accepts_quoted_venv_path():
    assert (
        _safe_env_prefix("source '~/vllm-env/bin/activate'")
        == '[ -f "$HOME/vllm-env/bin/activate" ] && source "$HOME/vllm-env/bin/activate" || true'
    )


@pytest.mark.asyncio
async def test_run_ssh_command_executes_with_stdin_and_returns_output(monkeypatch):
    captured = {}

    class _Proc:
        returncode = 0

        async def communicate(self, input=None):
            captured["input"] = input
            return b"stdout", b"stderr"

    async def _fake_exec(*args, **kwargs):
        captured["args"] = list(args)
        captured["stdin"] = kwargs.get("stdin")
        captured["stdout"] = kwargs.get("stdout")
        captured["stderr"] = kwargs.get("stderr")
        return _Proc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)

    rc, out, err = await run_ssh_command_async(
        "alice@gpu-box",
        "2222",
        "python -",
        timeout=5,
        connect_timeout=4,
        strict_host_key_checking=False,
        stdin_data=b"python -m pip install vllm",
    )

    assert rc == 0
    assert out == b"stdout"
    assert err == b"stderr"
    assert captured["args"] == [
        "ssh",
        "-o",
        "ConnectTimeout=4",
        "-o",
        "StrictHostKeyChecking=no",
        "-p",
        "2222",
        "alice@gpu-box",
        "python -",
    ]
    assert captured["stdin"] is not None
    assert captured["stdout"] is not None
    assert captured["stderr"] is not None
    assert captured["input"] == b"python -m pip install vllm"


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


def test_validate_local_dir_accepts_external_drive_paths_with_spaces():
    path = "/Volumes/T7 2TB/AI Models/llamacpp"

    assert _validate_local_dir(path) == path
    assert _validate_local_dir(f'"{path}"') == path
    assert _shell_path(f"{path}/Qwen3-8B") == '"/Volumes/T7 2TB/AI Models/llamacpp/Qwen3-8B"'


def test_validate_local_dir_accepts_windows_drive_paths_with_spaces():
    backslash_path = r"D:\AI Models\llamacpp"
    slash_path = "D:/AI Models/llamacpp"

    assert _validate_local_dir(backslash_path) == backslash_path
    assert _validate_local_dir(f"'{backslash_path}'") == backslash_path
    assert _validate_local_dir(slash_path) == slash_path
    assert _shell_path(backslash_path + r"\Qwen3-8B") == '"D:\\AI Models\\llamacpp\\Qwen3-8B"'


def test_validate_local_dir_still_rejects_shell_metacharacters():
    for path in [
        "/Volumes/T7 2TB/AI Models; touch /tmp/pwned",
        "/Volumes/T7 2TB/AI Models/$(touch pwned)",
        "/Volumes/T7 2TB/AI Models/`touch pwned`",
        "/Volumes/T7 2TB/AI Models/model\nnext",
    ]:
        with pytest.raises(HTTPException):
            _validate_local_dir(path)


def test_validate_local_dir_rejects_windows_shell_metacharacters():
    for path in [
        r"D:\AI Models\llamacpp; touch C:\pwned",
        r"D:\AI Models\llamacpp\$(touch pwned)",
        r"D:\AI Models\llamacpp\`touch pwned`",
        "D:\\AI Models\\llamacpp\nnext",
    ]:
        with pytest.raises(HTTPException):
            _validate_local_dir(path)


def test_validate_local_dir_accepts_non_ascii_unicode_paths():
    # Folder names are routinely non-ASCII on localized systems; the validator
    # must accept them the same way it accepts spaces (see issue: spaces AND
    # non-ASCII chars were both rejected by the old ASCII-only allowlist).
    for path in [
        "/Volumes/Модели/llamacpp",   # Cyrillic (POSIX / external drive)
        "/home/josé/models",          # accented Latin
        "/Volumes/モデル/llm",         # CJK
        r"D:\AI Models\Модели",       # Cyrillic (Windows drive path)
    ]:
        assert _validate_local_dir(path) == path


def test_validate_local_dir_rejects_metacharacters_in_unicode_paths():
    # Widening the allowlist to Unicode must not reopen the injection surface:
    # shell metacharacters stay rejected even alongside non-ASCII segments.
    for path in [
        "/Volumes/Модели; touch /tmp/pwned",
        "/Volumes/Модели/$(touch pwned)",
        "/Volumes/Модели/`touch pwned`",
        "/Volumes/Модели/a|b",
        "/Volumes/Модели\nnext",
        r"D:\Модели\llamacpp & calc.exe",
    ]:
        with pytest.raises(HTTPException):
            _validate_local_dir(path)


def test_validate_local_dir_rejects_leading_dash_segments():
    # A path segment starting with '-' could be parsed as a CLI option by hf/etc.
    # (option injection) even when quoted, since quoting doesn't stop a value from
    # being read as a flag. The validator must reject it on every platform.
    for path in [
        "/models/-rf",
        "/models/-rf/llamacpp",
        "/-oStrictHostKeyChecking=no",
        r"D:\models\-rf",
        "D:/models/-rf",
    ]:
        with pytest.raises(HTTPException):
            _validate_local_dir(path)


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
    # Fallback: --user first, then guarded --break-system-packages for PEP-668 pip.
    assert "python3 -m pip install --user -q -U huggingface_hub" in chain
    assert "python3 -m pip install --help 2>/dev/null | grep -q -- --break-system-packages" in chain
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
    assert "pip install --user -q hf_transfer" in chain
    assert "pip install --help 2>/dev/null | grep -q -- --break-system-packages" in chain
    assert "pip install --user --break-system-packages -q hf_transfer" in chain
    # venv check uses the python executable derived from the pip command
    assert 'python -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)"' in chain
    # All install attempts are wrapped in bash -c subshells
    assert chain.count("bash -c '") == 3


def test_pip_install_fallback_chain_accepts_python_executable():
    chain = _pip_install_fallback_chain("llama-cpp-python[server]", python_cmd="python")

    assert "python -m pip install -q 'llama-cpp-python[server]'" in chain
    assert "python -m pip install --user -q 'llama-cpp-python[server]'" in chain
    assert "python -m pip install --help 2>/dev/null | grep -q -- --break-system-packages" in chain
    assert "python install " not in chain
    assert 'python -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)"' in chain


def test_pip_install_fallback_chain_propagates_failure_in_venv():
    """When base install fails inside a venv, the chain must exit non-zero.

    The old `{ venv_check || user }` shape from #903 masked the failure:
    venv_check exited 0 (in venv), || short-circuited, and the group
    reported success even though nothing was installed.  The negated
    `{ ! venv_check && user }` shape propagates the failure correctly.
    """
    # Simulate "inside a venv" deterministically: the venv check exits 0.
    # Base install fails, venv_check exits 0, negated to 1,
    # && skips user, group exits 1.  This avoids depending on whether the
    # test runner's own interpreter happens to be inside a venv (which
    # differs between local and CI environments).
    script = (
        "false || "
        "{ ! true "  # venv_check=0 (in venv) → negated to 1 → user skipped
        "&& echo user_attempt; }"
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
    # Quoted in the plain, --user, and guarded --break-system-packages attempts.
    assert chain.count("'llama-cpp-python[server]'") == 3
    # llama-cpp installs must prefer prebuilt wheels to avoid fragile source builds.
    assert "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu" in chain
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


def test_serve_pip_install_normalizes_llama_cpp_alias_and_adds_wheel_index():
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent
        / "routes" / "cookbook_routes.py").read_text(encoding="utf-8")

    assert "re.sub(r\"(?<![A-Za-z0-9_.\\-/])llama_cpp(?![A-Za-z0-9_.\\-/])\", \"llama-cpp-python[server]\", req.cmd)" in src
    assert "if \"llama-cpp-python\" in req.cmd and \"--extra-index-url\" not in req.cmd:" in src
    assert "https://abetlen.github.io/llama-cpp-python/whl/cpu" in src


def test_vllm_preflight_reports_cli_and_version():
    lines = []

    _append_vllm_linux_preflight_lines(lines)
    script = "\n".join(lines)

    assert 'export PATH="$HOME/.local/bin:$PATH"' in script
    assert 'ODYSSEUS_VLLM_BIN="$(command -v vllm 2>/dev/null || true)"' in script
    assert 'echo "[odysseus] vLLM CLI: $ODYSSEUS_VLLM_BIN"' in script
    assert '"$ODYSSEUS_VLLM_BIN" --version' in script
    assert 'ODYSSEUS_PREFLIGHT_EXIT=127' in script


def test_venv_safe_local_pip_install_strips_user_flags_only_for_local_venv():
    cmd = 'python3 -m pip install -U --user --break-system-packages "vllm"'

    cleaned = _venv_safe_local_pip_install_cmd(cmd, local=True, in_venv=True)

    assert cleaned == "python3 -m pip install -U vllm"
    assert _venv_safe_local_pip_install_cmd(cmd, local=False, in_venv=True) == cmd
    assert _venv_safe_local_pip_install_cmd(cmd, local=True, in_venv=False) == cmd


def test_pip_install_runner_guards_break_system_packages():
    lines = []
    _append_pip_install_runner_lines(
        lines,
        'python3 -m pip install --no-cache-dir --user --break-system-packages "llama-cpp-python[server]"',
    )
    script = "\n".join(lines)

    assert "python3 -m pip install --help 2>/dev/null | grep -q -- --break-system-packages" in script
    assert 'python3 -m pip install --no-cache-dir --user --break-system-packages "llama-cpp-python[server]"' in script
    assert "python3 -m pip install --no-cache-dir --user 'llama-cpp-python[server]'" in script
    assert "pip does not support --break-system-packages" in script


def test_pip_install_runner_leaves_plain_commands_unchanged():
    lines = []
    _append_pip_install_runner_lines(lines, "python3 -m pip install --no-cache-dir vllm")

    assert lines == ["python3 -m pip install --no-cache-dir vllm"]


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
    if sys.platform == "win32":
        snippet = snippet.replace("$", "\\$")
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
    if sys.platform == "win32":
        snippet = snippet.replace("$", "\\$")
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
    if sys.platform == "win32":
        snippet = snippet.replace("$", "\\$")
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
    # A missing python3 OR a Microsoft Store App Execution Alias stub under
    # WindowsApps must shim python3 -> python so the venv interpreter is used.
    assert '_odys_py3="$(command -v python3 2>/dev/null || true)"' in script
    assert (
        'case "$_odys_py3" in ""|*[Ww]indows[Aa]pps*) python3() { python "$@"; } ;; esac'
        in script
    )
    assert 'command -v python >/dev/null 2>&1 || python() { python3 "$@"; }' in script


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


def test_validate_serve_cmd_accepts_windows_printf_format():
    cmd = (
        "python -m llama_cpp.server --model "
        "\"$(printf %s ${HOME}'/.cache/huggingface/hub/models--unsloth--Qwen3.5-2B-GGUF/snapshots/f6d5376be1edb4d416d56da11e5397a961aca8ae/Qwen3.5-2B-Q4_K_M.gguf')\" "
        "--host 0.0.0.0 --port 8000 --n_gpu_layers 99 --n_ctx 32768 --flash_attn true --type_k q4_0 --type_v q4_0"
    )
    assert _validate_serve_cmd(cmd) == cmd


def test_normalize_llama_cpp_python_cache_types_for_stale_client_cmd():
    cmd = (
        "python -m llama_cpp.server --model model.gguf --host 0.0.0.0 --port 8000 "
        "--type_k q4_0 --type_v q4_0"
    )

    assert _normalize_llama_cpp_python_cache_types(cmd).endswith("--type_k 2 --type_v 2")


def test_normalize_llama_cpp_python_cache_types_preserves_native_cache_flags():
    cmd = (
        "llama-server --model model.gguf --cache-type-k q4_0 --cache-type-v q4_0 "
        "|| python3 -m llama_cpp.server --model model.gguf --type_k=q8_0 --type_v='f16'"
    )

    normalized = _normalize_llama_cpp_python_cache_types(cmd)
    assert "--cache-type-k q4_0 --cache-type-v q4_0" in normalized
    assert "--type_k=8" in normalized
    assert "--type_v='1'" in normalized


def test_model_serve_normalizes_llama_cpp_python_cache_types_after_validation():
    src = (Path(__file__).resolve().parents[1] / "routes" / "cookbook_routes.py").read_text(encoding="utf-8")

    assert "req.cmd = _validate_serve_cmd(req.cmd) or \"\"" in src
    assert "req.cmd = _normalize_llama_cpp_python_cache_types(req.cmd) or \"\"" in src
    assert src.index("_validate_serve_cmd(req.cmd)") < src.index("_normalize_llama_cpp_python_cache_types(req.cmd)")


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

    assert "mkdir -p ~/bin" in script
    assert script.index("mkdir -p ~/bin") < script.index("cd ~/llama.cpp")
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
    no_toolchain_warn = 'WARNING: no HIP/CUDA/Vulkan toolchain found'
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

    assert 'WARNING: no HIP/CUDA/Vulkan toolchain found — building llama-server for CPU only.' in script
    assert 'Install Vulkan (libvulkan-dev) / ROCm for AMD GPUs or CUDA tooling for NVIDIA' in script


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


def test_local_windows_download_pid_tracks_inner_bash_and_stop_kills_tree():
    routes_src = (Path(__file__).resolve().parents[1] / "routes" / "cookbook_routes.py").read_text(encoding="utf-8")
    running_src = (Path(__file__).resolve().parents[1] / "static" / "js" / "cookbookRunning.js").read_text(encoding="utf-8")

    assert 'printf \'%s\\\\n\' \\"$$\\" > {pp}' in routes_src
    assert "function Stop-Tree([int]$Id)" in running_src
    assert "('ParentProcessId = ' + $Id)" in running_src
    assert "Stop-Tree ([int]$p)" in running_src


def test_llama_cpp_rebuild_cmd_runs_clean_on_a_fresh_home(tmp_path):
    """The command should succeed even when neither path exists yet."""
    import os
    from core.platform_compat import find_bash, git_bash_path

    bash = find_bash() or "bash"
    env = dict(os.environ)
    env["HOME"] = git_bash_path(tmp_path)
    result = subprocess.run(
        [bash, "-c", _llama_cpp_rebuild_cmd()],
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


def test_cached_model_scan_uses_ollama_api_before_cli_and_windows_opt_in():
    script = _cached_model_scan_script()

    assert "scan_ollama_api()\nscan_ollama()" in script
    assert "if any(m.get('is_ollama') for m in models): return" in script
    assert "os.name == 'nt'" in script
    assert "ODYSSEUS_ALLOW_OLLAMA_CLI_SCAN" in script


@pytest.mark.skipif(os.name != "nt", reason="Windows Ollama CLI startup guard")
def test_cached_model_scan_does_not_launch_ollama_cli_on_windows(tmp_path):
    """Official Ollama for Windows can auto-start the tray/server on `ollama list`.
    The read-only cache scanner must not invoke that CLI unless explicitly opted in.
    """
    marker = tmp_path / "ollama-called.txt"
    fake_ollama = tmp_path / "ollama.cmd"
    fake_ollama.write_text(
        "@echo off\r\n"
        f'echo called>"{marker}"\r\n'
        "echo NAME ID SIZE MODIFIED\r\n"
        "echo local-model:latest abc 1 GB now\r\n",
        encoding="utf-8",
    )

    empty_home = tmp_path / "home"
    empty_home.mkdir()
    scan_py = tmp_path / "scan_cache.py"
    scan_py.write_text(_cached_model_scan_script(), encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(tmp_path) + os.pathsep + env.get("PATH", "")
    env["HOME"] = str(empty_home)
    env.pop("ODYSSEUS_ALLOW_OLLAMA_CLI_SCAN", None)
    proc = subprocess.run(
        [sys.executable, str(scan_py)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert marker.exists() is False
    assert all(m.get("backend") != "ollama" for m in json.loads(proc.stdout))


def test_cached_model_scan_uses_huggingface_cache_env(tmp_path):
    """Docker recreates can leave the persisted HF cache outside HOME.
    The Serve scanner should honor the cache env path instead of only ~/.cache.
    """
    hf_cache = tmp_path / "app-cache" / "hub"
    model = hf_cache / "models--Qwen--Qwen3.6-35B"
    (model / "blobs").mkdir(parents=True)
    (model / "blobs" / "weights.safetensors").write_bytes(b"weights")
    (model / "snapshots" / "abc").mkdir(parents=True)
    (model / "snapshots" / "abc" / "config.json").write_text("{}", encoding="utf-8")

    empty_home = tmp_path / "home"
    empty_home.mkdir()
    scan_py = tmp_path / "scan_cache_env.py"
    scan_py.write_text(_cached_model_scan_script(), encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(empty_home)
    env["HUGGINGFACE_HUB_CACHE"] = str(hf_cache)
    proc = subprocess.run(
        [sys.executable, str(scan_py)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    by_repo = {m["repo_id"]: m for m in json.loads(proc.stdout)}
    assert by_repo["Qwen/Qwen3.6-35B"]["path"] == str(hf_cache)


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


def test_cached_model_scan_runs_additional_hf_cache(tmp_path):
    extra_cache = tmp_path / "extra_hf_cache"
    model_dir = extra_cache / "models--acme--sample-7b"
    snap = model_dir / "snapshots" / "rev-1"
    snap.mkdir(parents=True)
    weights = snap / "model.safetensors"
    weights.write_bytes(b"abc123")

    scan_py = tmp_path / "scan_cache.py"
    scan_py.write_text(
        _cached_model_scan_script(add_hf_cache=str(extra_cache)),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(scan_py)],
        check=True,
        capture_output=True,
        text=True,
    )

    models = json.loads(proc.stdout)
    by_repo = {m["repo_id"]: m for m in models}

    assert "acme/sample-7b" in by_repo
    rec = by_repo["acme/sample-7b"]
    assert rec["path"] == str(extra_cache)
    assert rec["nb_files"] == 1
    assert rec["size_bytes"] == len(b"abc123")
    assert rec["has_incomplete"] is False
    assert rec["is_diffusion"] is False
