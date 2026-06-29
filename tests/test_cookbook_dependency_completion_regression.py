from pathlib import Path


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
    assert "const nextStatus = live.status === 'completed'" in source
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
    assert "if (!task.remoteHost) return command;" in source
    assert "return `ssh ${_sshPrefix(_getPort(task))}${task.remoteHost} ${_shQuote(command)}`;" in source


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

    assert "const depDone = !!task.payload?._dep && _depInstallSucceeded(task.output);" in source
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
        "&& String(task.output || '').includes('DOWNLOAD_OK');"
    ) in normalized


def test_dependency_install_payload_keeps_env_path_for_refresh():
    source = _read("static/js/cookbook.js")

    assert "env_path: _envState.envPath || ''" in source


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
