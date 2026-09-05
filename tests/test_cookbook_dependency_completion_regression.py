import os
import subprocess
from pathlib import Path
import pytest
from core.platform_compat import find_bash, git_bash_path
from routes.cookbook_helpers import (
    ODYSSEUS_LLAMA_SHIM_MARKER,
    _append_llama_server_shim_definition_lines,
    _append_llama_server_shim_reconciliation_lines,
    _append_llama_server_finalization_lines,
)


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_backend_status_treats_download_exit_zero_as_completed():
    source = _read("routes/cookbook_routes.py")

    assert "exit_match = re.search(r\"=== process exited with code\\s+(-?\\d+)\"" in source
    assert "elif has_exit and task_type == \"download\":" in source
    assert "status = \"completed\" if exit_code == 0 else \"error\"" in source


def test_background_status_poll_reconciles_into_local_tasks():
    source = _read("static/js/cookbookRunning.js")

    assert "const statusById = new Map(tasks.map(t => [t.session_id, t]));" in source
    assert "const completedByOutput = depDone || downloadDone;" in source
    assert "const nextStatus = completedByOutput" in source
    assert "live.status === 'completed'" in source
    assert "? 'done'" in source
    assert ": (live.status === 'error'" in source
    assert "? 'error'" in source
    assert "_saveTasks(localTasks);" in source
    assert "completedDeps.forEach(t => _refreshDepsAfterInstall(t));" in source


def test_windows_session_commands_use_shared_powershell_wrapper_and_local_log_dir():
    source = _read("static/js/cookbookRunning.js")

    assert "const host = task.remoteHost;" in source
    assert "host ? '$env:TEMP\\\\odysseus-sessions' : '$env:TEMP\\\\odysseus-tmux'" in source
    assert "function _winPowerShellCmd(task, ps)" in source
    assert "const command = `powershell -Command \"${ps}\"`;" in source
    assert "if (!host) return command;" in source
    assert "return `ssh ${_sshPrefix(_getPort(task))}${host} ${_shQuote(command)}`;" in source


def test_dep_install_success_recognized_from_exit_sentinel():
    """A pip dependency install reports success via the runner's exit-0
    sentinel / pip's "Successfully installed" line, not the HuggingFace
    download markers. The shared helper must key off those, so an install
    whose tmux pane is gone isn't misread as crashed."""
    source = _read("static/js/cookbookRunning.js")

    assert "function _depInstallSucceeded(output) {" in source
    assert "=== Process exited with code" in source
    assert "Successfully installed" in source


def test_session_gone_heuristic_honors_dep_install_success():
    """The reconnect loop's session-gone branch (download tasks need an HF
    marker to look successful) must also accept a finished dependency install,
    otherwise a clean pip install with no HF markers is marked crashed."""
    source = _read("static/js/cookbookRunning.js")

    assert "const depInstallSucceeded = !!task.payload?._dep && _depInstallSucceeded(lastOutput);" in source
    # Whitespace-normalized so the check survives line-wrapping/formatting while
    # still proving the invariant: a finished dependency install short-circuits
    # looksSuccessful ahead of the download/serve branch.
    normalized = " ".join(source.split())
    assert (
        "const looksSuccessful = depInstallSucceeded "
        "|| (task.type === 'download'"
    ) in normalized


def test_background_poll_recovers_done_for_stopped_dependency_install():
    """When the backend reports a finished dependency install as "stopped"
    (its pip package is never in the HF cache the dead-session check inspects),
    the reconciler must recover "done" from the retained output instead of
    downgrading the card to crashed."""
    source = _read("static/js/cookbookRunning.js")

    assert "const combinedOutput = `${task.output || ''}\\n${live.output_tail || ''}`;" in source
    assert "const depDone = !!task.payload?._dep && _depInstallSucceeded(combinedOutput);" in source
    assert "(depDone || downloadDone) ? 'done' : (task.type === 'download' ? 'crashed' : 'stopped')" in source


def test_background_poll_recovers_done_for_completed_download():
    """When the backend reports a finished model download as "stopped" (its
    tmux pane is gone after DOWNLOAD_OK, so the dead-session check can miss the
    landed snapshot), the reconciler must recover "done" from the terminal
    DOWNLOAD_OK sentinel instead of downgrading the card to crashed. The
    background poll keys off DOWNLOAD_OK only (not the "/snapshots/" path, which
    can appear mid-stream for multi-file downloads)."""
    source = _read("static/js/cookbookRunning.js")

    normalized = " ".join(source.split())
    assert (
        "const downloadDone = task.type === 'download' "
        "&& String(combinedOutput || '').includes('DOWNLOAD_OK');"
    ) in normalized


def test_dependency_install_payload_keeps_env_path_for_refresh():
    source = _read("static/js/cookbook.js")

    assert "env_path: targetEnvPath || ''" in source


def test_local_dependency_probe_refreshes_user_site_visibility():
    source = _read("routes/shell_routes.py")

    assert "importlib.invalidate_caches()" in source
    assert "user_site = site.getusersitepackages()" in source
    # addsitedir (not a bare sys.path.append) so user-site `.pth` hooks are
    # replayed when a package is installed into an already-running process —
    # otherwise setuptools' distutils shim never activates and basicsr-based
    # deps (realesrgan) probe as not-installed until a restart. See #4810.
    assert "if user_site and os.path.isdir(user_site):" in source
    assert "site.addsitedir(user_site)" in source


def test_llama_runner_path_preserves_standard_user_bin_order():
    """The runner PATH must preserve standard user $HOME/bin precedence while
    relying on ownership-aware reconciliation to retire Odysseus shims when
    native builds exist."""
    source = _read("routes/cookbook_routes.py")

    assert (
        'export PATH="$HOME/.local/bin:$HOME/bin:$HOME/llama.cpp/build/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"'
        in source
    )


def _run_shell(tmp_path, lines, cwd=None, path=None, extra_env=None):
    from core.platform_compat import find_bash, git_bash_path
    bash = find_bash() or "bash"
    env = dict(os.environ, HOME=git_bash_path(tmp_path))
    if path is not None:
        env["PATH"] = path
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [bash, "-c", "\n".join(lines)],
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
    )


def _write_odysseus_shim(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!/usr/bin/env bash\n{ODYSSEUS_LLAMA_SHIM_MARKER}\nexec python3 -m llama_cpp.server \"$@\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_mock_native(path: Path, succeeds: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if succeeds:
        content = '#!/usr/bin/env bash\nif [ "$1" = "--version" ]; then echo "version: 0.1.0-dev"; exit 0; fi\necho "RUN_NATIVE: $@"\nexit 0\n'
    else:
        content = '#!/usr/bin/env bash\necho "error: missing libllama.so" >&2\nexit 127\n'
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _write_mock_python3(path: Path, succeeds: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env bash\nexit {0 if succeeds else 1}\n", encoding="utf-8")
    path.chmod(0o755)


def test_odysseus_create_llama_shim_never_overwrites_user_regular_file(tmp_path):
    """Creation boundary: never overwrite a non-executable user-owned regular file."""
    user_file = tmp_path / "bin/llama-server"
    user_file.parent.mkdir(parents=True, exist_ok=True)
    original_content = "user configuration or binary data without marker"
    user_file.write_text(original_content, encoding="utf-8")

    lines = []
    _append_llama_server_shim_definition_lines(lines)
    lines.append('_odysseus_create_llama_shim')

    res = _run_shell(tmp_path, lines)
    assert res.returncode != 0, "Creation should return non-zero when target is a user file"
    assert user_file.read_text(encoding="utf-8") == original_content, "User file must remain byte-for-byte unchanged"


def test_odysseus_create_llama_shim_never_overwrites_user_symlink(tmp_path):
    """Creation boundary: never follow or overwrite a user-owned symlink."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target_file = tmp_path / "user_target.txt"
    target_file.write_text("precious user target content", encoding="utf-8")
    user_symlink = bin_dir / "llama-server"
    user_symlink.symlink_to(target_file)

    lines = []
    _append_llama_server_shim_definition_lines(lines)
    lines.append('_odysseus_create_llama_shim')

    res = _run_shell(tmp_path, lines)
    assert res.returncode != 0, "Creation should return non-zero when target is a user symlink"
    assert user_symlink.is_symlink(), "Symlink must remain intact"
    assert target_file.read_text(encoding="utf-8") == "precious user target content", "Target file must not be modified"


def test_odysseus_create_llama_shim_never_overwrites_dangling_symlink(tmp_path):
    """Creation boundary: never overwrite a dangling user-owned symlink."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    dangling_symlink = bin_dir / "llama-server"
    dangling_symlink.symlink_to(tmp_path / "nonexistent_target")

    lines = []
    _append_llama_server_shim_definition_lines(lines)
    lines.append('_odysseus_create_llama_shim')

    res = _run_shell(tmp_path, lines)
    assert res.returncode != 0, "Creation should return non-zero when target is a dangling symlink"
    assert dangling_symlink.is_symlink(), "Dangling symlink must remain intact"


def test_odysseus_create_llama_shim_safely_replaces_existing_odysseus_shim(tmp_path):
    """Creation boundary: safely replace an existing Odysseus-owned compatibility shim."""
    shim_file = tmp_path / "bin/llama-server"
    shim_file.parent.mkdir(parents=True, exist_ok=True)
    shim_file.write_text(f"#!/bin/bash\n{ODYSSEUS_LLAMA_SHIM_MARKER}\n# old shim body\n", encoding="utf-8")

    lines = []
    _append_llama_server_shim_definition_lines(lines)
    lines.append('_odysseus_create_llama_shim')

    res = _run_shell(tmp_path, lines)
    assert res.returncode == 0
    new_content = shim_file.read_text(encoding="utf-8")
    assert ODYSSEUS_LLAMA_SHIM_MARKER in new_content
    assert "exec python3 -m llama_cpp.server" in new_content


def test_odysseus_create_llama_shim_secure_temp_prevents_symlink_attack(tmp_path):
    """Creation boundary: secure mktemp prevents predictable $$ temp symlink attacks.

    Given:
    - A victim file at ~/victim containing DO_NOT_TOUCH
    - A pre-created symlink at ~/bin/.llama-server.odysseus.tmp.$$ pointing to ~/victim
    - _odysseus_create_llama_shim executed within that exact same bash PID ($$)

    Prove:
    - ~/victim remains unmodified
    - Pre-existing crafted symlink is NOT followed or overwritten
    - Legitimate shim is successfully created at ~/bin/llama-server via secure temp path
    """
    lines = [
        'victim="$HOME/victim"',
        'printf "DO_NOT_TOUCH\\n" > "$victim"',
        'mkdir -p "$HOME/bin"',
        'old_tmp="$HOME/bin/.llama-server.odysseus.tmp.$$";',
        'ln -s "$victim" "$old_tmp"',
    ]
    _append_llama_server_shim_definition_lines(lines)
    lines.append('_odysseus_create_llama_shim')
    lines.append('res=$?')
    lines.append('echo "CREATION_RES=$res"')

    res = _run_shell(tmp_path, lines)
    assert res.returncode == 0
    assert "CREATION_RES=0" in res.stdout

    victim_file = tmp_path / "victim"
    assert victim_file.exists()
    assert victim_file.read_text(encoding="utf-8") == "DO_NOT_TOUCH\n", "Victim file must remain untouched"

    target_shim = tmp_path / "bin/llama-server"
    assert target_shim.exists()
    assert ODYSSEUS_LLAMA_SHIM_MARKER in target_shim.read_text(encoding="utf-8")
    assert target_shim.stat().st_mode & 0o111 != 0, "Shim must be executable"


def test_stale_odysseus_shim_removed_when_python_server_unusable(tmp_path):
    """Scenario 1: Stale Odysseus shim + missing Python server -> owned shim removed."""
    shim_file = tmp_path / "bin/llama-server"
    _write_odysseus_shim(shim_file)

    lines = []
    _append_llama_server_shim_reconciliation_lines(lines)

    res = _run_shell(tmp_path, lines)
    assert res.returncode == 0
    assert not shim_file.exists(), "Stale Odysseus shim must be removed when python server is unavailable"


def test_healthy_odysseus_shim_retained_when_no_native_server(tmp_path):
    """Scenario 2: Healthy Odysseus shim + no native server -> shim preserved."""
    shim_file = tmp_path / "bin/llama-server"
    _write_odysseus_shim(shim_file)
    mock_py_dir = tmp_path / "mock_bin"
    _write_mock_python3(mock_py_dir / "python3", succeeds=True)

    lines = []
    _append_llama_server_shim_reconciliation_lines(lines)

    res = _run_shell(tmp_path, lines, path=f"{git_bash_path(mock_py_dir)}:{os.environ.get('PATH', '')}")
    assert res.returncode == 0
    assert shim_file.exists(), "Healthy Odysseus shim must be preserved when no native server is present"


def test_reconciliation_does_not_retire_healthy_shim_for_broken_native_binary(tmp_path):
    """Reconciliation health check: a broken native candidate executable must NOT retire
    a healthy Odysseus Python shim."""
    shim_file = tmp_path / "bin/llama-server"
    _write_odysseus_shim(shim_file)
    _write_mock_native(tmp_path / "llama.cpp/build/bin/llama-server", succeeds=False)
    mock_py_dir = tmp_path / "mock_bin"
    _write_mock_python3(mock_py_dir / "python3", succeeds=True)

    lines = [f'export PATH="$HOME/llama.cpp/build/bin:{git_bash_path(mock_py_dir)}:$PATH"']
    _append_llama_server_shim_reconciliation_lines(lines)

    res = _run_shell(tmp_path, lines)
    assert res.returncode == 0
    assert shim_file.exists(), "Healthy Odysseus shim must NOT be retired when native binary is broken"


def test_odysseus_shim_retired_when_native_server_healthy(tmp_path):
    """Scenario 3: Odysseus shim + healthy native server available -> native server wins."""
    shim_file = tmp_path / "bin/llama-server"
    _write_odysseus_shim(shim_file)
    _write_mock_native(tmp_path / "llama.cpp/build/bin/llama-server", succeeds=True)

    lines = ['export PATH="$HOME/bin:$HOME/llama.cpp/build/bin:$PATH"']
    _append_llama_server_shim_reconciliation_lines(lines)

    res = _run_shell(tmp_path, lines)
    assert res.returncode == 0
    assert not shim_file.exists(), "Odysseus shim must be retired when healthy native server is available"


def test_user_owned_bin_llama_server_never_touched(tmp_path):
    """Scenario 4: Arbitrary user ~/bin/llama-server -> untouched and not deprioritized."""
    user_bin = tmp_path / "bin/llama-server"
    user_bin.parent.mkdir(parents=True, exist_ok=True)
    user_bin.write_text("#!/bin/bash\n# Custom user build script\nexec /opt/custom/llama-server\n", encoding="utf-8")
    user_bin.chmod(0o755)

    _write_mock_native(tmp_path / "llama.cpp/build/bin/llama-server", succeeds=True)

    lines = ['export PATH="$HOME/bin:$HOME/llama.cpp/build/bin:$PATH"']
    _append_llama_server_shim_reconciliation_lines(lines)

    res = _run_shell(tmp_path, lines)
    assert res.returncode == 0
    assert user_bin.exists(), "User-owned ~/bin/llama-server must never be deleted"
    assert "Custom user build script" in user_bin.read_text(encoding="utf-8")


def test_reconciliation_ignores_native_binary_outside_effective_path(tmp_path):
    """Native candidate outside effective PATH must NOT be considered."""
    shim_file = tmp_path / "bin/llama-server"
    _write_odysseus_shim(shim_file)
    # Native binary placed in /usr_custom/bin which is NOT in PATH
    _write_mock_native(tmp_path / "usr_custom/bin/llama-server", succeeds=True)
    mock_py_dir = tmp_path / "mock_bin"
    _write_mock_python3(mock_py_dir / "python3", succeeds=True)

    lines = []
    _append_llama_server_shim_reconciliation_lines(lines)

    # PATH only contains ~/bin and mock_py_dir — usr_custom is omitted
    path = f"{git_bash_path(tmp_path / 'bin')}:{git_bash_path(mock_py_dir)}"
    res = _run_shell(tmp_path, lines, path=path)
    assert res.returncode == 0
    assert shim_file.exists(), "Shim must NOT be retired by native binary outside effective PATH"


def test_reconciliation_skips_home_bin_with_or_without_trailing_slash(tmp_path):
    """Reconciliation must exclude $HOME/bin and $HOME/bin/ from native candidate discovery."""
    shim_file = tmp_path / "bin/llama-server"
    _write_odysseus_shim(shim_file)
    mock_py_dir = tmp_path / "mock_bin"
    _write_mock_python3(mock_py_dir / "python3", succeeds=True)

    lines = []
    _append_llama_server_shim_reconciliation_lines(lines)

    # PATH has ~/bin/ with trailing slash
    path = f"{git_bash_path(tmp_path / 'bin')}/:{git_bash_path(mock_py_dir)}"
    res = _run_shell(tmp_path, lines, path=path)
    assert res.returncode == 0
    assert shim_file.exists(), "Shim must NOT rediscover itself when PATH has trailing slash in $HOME/bin/"


def test_reconciliation_does_not_discover_cwd_executable_when_path_omits_dot(tmp_path):
    """Reconciliation must NOT synthesize an empty/trailing PATH element that searches cwd.

    Given:
    - Current working directory contains an executable `llama-server`
    - Healthy Odysseus Python shim in ~/bin/llama-server
    - PATH does NOT contain '.' or cwd
    - No other healthy native binary on PATH

    Prove:
    - Reconciliation does not discover ./llama-server
    - The healthy Odysseus shim in ~/bin/llama-server is preserved
    """
    shim_file = tmp_path / "bin/llama-server"
    _write_odysseus_shim(shim_file)
    mock_py_dir = tmp_path / "mock_bin"
    _write_mock_python3(mock_py_dir / "python3", succeeds=True)

    cwd_dir = tmp_path / "workdir"
    _write_mock_native(cwd_dir / "llama-server", succeeds=True)

    lines = []
    _append_llama_server_shim_reconciliation_lines(lines)

    path = f"{git_bash_path(tmp_path / 'bin')}:{git_bash_path(mock_py_dir)}:/usr/bin:/bin"
    res = _run_shell(tmp_path, lines, cwd=cwd_dir, path=path)
    assert res.returncode == 0
    assert shim_file.exists(), "Healthy Odysseus shim must NOT be retired by finding ./llama-server in cwd"


def test_reconciliation_discovers_healthy_native_on_explicit_other_path_entry(tmp_path):
    """Reconciliation must discover healthy native binaries placed on explicit PATH entries."""
    shim_file = tmp_path / "bin/llama-server"
    _write_odysseus_shim(shim_file)
    custom_opt_bin = tmp_path / "custom_opt/bin"
    _write_mock_native(custom_opt_bin / "llama-server", succeeds=True)

    lines = []
    _append_llama_server_shim_reconciliation_lines(lines)

    path = f"{git_bash_path(tmp_path / 'bin')}:{git_bash_path(custom_opt_bin)}:/usr/bin:/bin"
    res = _run_shell(tmp_path, lines, path=path)
    assert res.returncode == 0
    assert not shim_file.exists(), "Odysseus shim must be retired when healthy native server is on explicit PATH entry"


def test_reconciliation_safely_iterates_path_with_glob_metacharacters(tmp_path):
    """Reconciliation must not perform pathname expansion / globbing on PATH components.

    Given:
    - A directory path containing shell glob characters: ~/custom[dir]/bin
    - A native binary ~/custom[dir]/bin/llama-server
    - A working directory containing matching glob entries
    - An existing Odysseus shim in ~/bin/llama-server

    Prove:
    - Reconciliation correctly parses the literal path without expanding [dir]
    - Discovers the healthy native server and retires the Odysseus shim
    """
    shim_file = tmp_path / "bin/llama-server"
    _write_odysseus_shim(shim_file)

    glob_dir = tmp_path / "custom[dir]/bin"
    _write_mock_native(glob_dir / "llama-server", succeeds=True)

    cwd_dir = tmp_path / "cwd"
    cwd_dir.mkdir(parents=True, exist_ok=True)
    (cwd_dir / "customd").mkdir(parents=True, exist_ok=True)
    (cwd_dir / "customi").mkdir(parents=True, exist_ok=True)

    lines = []
    _append_llama_server_shim_reconciliation_lines(lines)

    path = f"{git_bash_path(tmp_path / 'bin')}:{git_bash_path(glob_dir)}:/usr/bin:/bin"
    res = _run_shell(tmp_path, lines, cwd=cwd_dir, path=path)
    assert res.returncode == 0
    assert not shim_file.exists(), "Reconciliation must discover literal bracketed PATH without globbing"


def test_fallback_install_success_creates_usable_launcher(tmp_path):
    """Scenario 5: No native + no Python server -> fallback install succeeds
    -> usable compatibility launcher exists and resolves command -v llama-server."""
    mock_py_dir = tmp_path / "mock_bin"
    _write_mock_python3(mock_py_dir / "python3", succeeds=True)

    lines = [f'export PATH="$HOME/bin:{git_bash_path(mock_py_dir)}:$PATH"']
    _append_llama_server_shim_definition_lines(lines)
    _append_llama_server_finalization_lines(lines)
    lines.append('command -v llama-server')

    res = _run_shell(tmp_path, lines)
    assert res.returncode == 0
    created_shim = tmp_path / "bin/llama-server"
    assert created_shim.exists()
    assert ODYSSEUS_LLAMA_SHIM_MARKER in created_shim.read_text(encoding="utf-8")
    assert str(tmp_path / "bin/llama-server") in res.stdout


def test_failed_fallback_sets_preflight_exit_127(tmp_path):
    """Scenario 6: No native + failed fallback installation -> ODYSSEUS_PREFLIGHT_EXIT=127."""
    lines = [
        'ODYSSEUS_PREFLIGHT_EXIT=""',
        'export PATH="$HOME/bin:$PATH"',
    ]
    _append_llama_server_shim_definition_lines(lines)
    _append_llama_server_finalization_lines(lines)
    lines.append('echo "EXIT=$ODYSSEUS_PREFLIGHT_EXIT"')

    res = _run_shell(tmp_path, lines)
    assert res.returncode == 0
    assert "EXIT=127" in res.stdout


def test_python_server_readiness_requires_llama_cpp_server_module():
    """Scenario 7 & 8: Python server readiness requires `import llama_cpp.server`,
    while GPU offload capability checks still use base `import llama_cpp`."""
    routes_source = _read("routes/cookbook_routes.py")
    helpers_source = _read("routes/cookbook_helpers.py")

    # Windows fallback readiness check requires .server
    assert 'try { python -c "import llama_cpp.server" 2>$null } catch {}' in routes_source
    # Termux fallback readiness check requires .server
    assert 'if ! python3 -c "import llama_cpp.server" 2>/dev/null; then' in routes_source
    # Build-failure fallback pip check requires .server
    assert 'if ! command -v llama-server &>/dev/null && ! python3 -c "import llama_cpp.server" 2>/dev/null; then' in routes_source
    # Shim reconciliation and post-install finalization require .server
    assert 'if ! python3 -c "import llama_cpp.server" 2>/dev/null; then' in helpers_source
    assert 'if ! command -v llama-server >/dev/null 2>&1 && python3 -c "import llama_cpp.server" 2>/dev/null; then' in helpers_source

    # GPU offload support checks still intentionally inspect base llama_cpp
    assert 'llama_cpp.llama_supports_gpu_offload()' in routes_source


def test_darwin_native_build_does_not_force_write_bin_symlink():
    """Darwin source build must not overwrite ~/bin/llama-server with a forced symlink."""
    source = _read("routes/cookbook_routes.py")

    assert "&& ln -sf ~/llama.cpp/build/bin/llama-server ~/bin/llama-server" not in source
    assert 'cmake --build build -j"$NPROC" --target llama-server' in source


@pytest.mark.asyncio
async def test_generated_runner_preflight_retires_stale_shim_before_backend_selection(monkeypatch, tmp_path):
    """Integration regression: The actual generated Cookbook runner must execute
    ownership reconciliation BEFORE evaluating the backend selection (`if Termux ... elif ! command -v llama-server`).

    Given:
    - Executable stale Odysseus shim in ~/bin/llama-server (with canonical marker)
    - Healthy native llama-server in ~/llama.cpp/build/bin/llama-server
    - ~/bin before ~/llama.cpp/build/bin on PATH

    Prove:
    1. Generated runner contains reconciliation shell lines strictly before the backend branching
    2. Executing the generated runner script retires the stale ~/bin/llama-server shim
    3. Resolves to the healthy native binary without attempting source build or pip fallback.
    """
    from unittest.mock import AsyncMock, MagicMock
    from routes.cookbook_helpers import ServeRequest
    import routes.cookbook_routes as cookbook_routes
    from starlette.requests import Request

    fake_home = tmp_path / "fake_home"
    stale_shim = fake_home / "bin/llama-server"
    _write_odysseus_shim(stale_shim)
    native_bin = fake_home / "llama.cpp/build/bin/llama-server"
    _write_mock_native(native_bin, succeeds=True)

    # Mock environment to generate the real runner file with isolated DB
    tmux_log_dir = tmp_path / "tmux_logs"
    tmux_log_dir.mkdir(parents=True)
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmux_log_dir)
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(cookbook_routes, "_binary_available", AsyncMock(return_value=True))
    monkeypatch.setattr(cookbook_routes, "load_stored_hf_token", lambda **kwargs: "")
    monkeypatch.setattr("core.database.SessionLocal", MagicMock())

    class _Proc:
        returncode = 0
        stderr = None
        async def wait(self):
            return 0
    monkeypatch.setattr(cookbook_routes.asyncio, "create_subprocess_shell", AsyncMock(return_value=_Proc()))

    router = cookbook_routes.setup_cookbook_routes()
    endpoint = None
    for route in router.routes:
        if route.path == "/api/model/serve" and "POST" in route.methods:
            endpoint = route.endpoint
            break
    assert endpoint is not None

    http_req = Request({"type": "http", "method": "POST", "path": "/api/model/serve", "headers": []})
    http_req.state.current_user = "admin"

    req = ServeRequest(
        repo_id="test/repo",
        cmd="llama-server -m /dummy/model.gguf --port 8085",
        remote_host=None,
    )

    resp = await endpoint(http_req, req)
    assert resp["ok"] is True
    session_id = resp["session_id"]
    runner_file = tmux_log_dir / f"{session_id}_run.sh"
    assert runner_file.exists()
    runner_script = runner_file.read_text(encoding="utf-8")

    # Assert structural ordering in the generated script:
    # Reconciliation must appear strictly BEFORE the Termux/backend check
    recon_idx = runner_script.find("Native llama-server detected — retiring Odysseus compatibility shim in ~/bin")
    termux_idx = runner_script.find("if [ -d /data/data/com.termux ]; then")
    backend_idx = runner_script.find("elif ! command -v llama-server &>/dev/null; then")
    assert recon_idx != -1, "Reconciliation block missing from generated runner"
    assert termux_idx != -1, "Termux check missing from generated runner"
    assert backend_idx != -1, "Backend selection missing from generated runner"
    assert recon_idx < termux_idx < backend_idx, "Reconciliation must execute strictly before backend selection"

    # Execute the generated runner preflight in a real subshell
    res = _run_shell(fake_home, [str(runner_file)], path=f"{git_bash_path(fake_home / 'bin')}:{git_bash_path(fake_home / 'llama.cpp/build/bin')}:/usr/bin:/bin")
    assert res.returncode == 0
    assert not stale_shim.exists(), "Stale ~/bin/llama-server must have been retired by the generated preflight"
    assert "RUN_NATIVE: -m /dummy/model.gguf --port 8085" in res.stdout
    assert "Native llama-server not found — building from source" not in res.stdout
