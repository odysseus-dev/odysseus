from pathlib import Path

from tests.helpers.stylesheets import app_css


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


def test_running_tab_render_disposes_card_owned_work_before_rebuild():
    source = _read("static/js/cookbookRunning.js")

    assert "function _disposeTaskCard(el) {" in source
    assert "el._abort.abort()" in source
    assert "clearInterval(el._uptimeInterval)" in source
    assert "body.querySelectorAll('.cookbook-task').forEach(_disposeTaskCard);" in source


def test_existing_download_card_uses_finished_label_for_done_and_completed():
    source = _read("static/js/cookbookRunning.js")

    assert "const isDoneDl = _isFinishedDownload(task.status, task.type);" in source
    assert source.count("const isDoneDl = _isFinishedDownload(task.status, task.type);") == 1


def test_background_monitor_requests_have_timeouts():
    source = _read("static/js/cookbookRunning.js")

    assert "function _fetchWithTimeout(input, init = {}, timeoutMs = 15000)" in source
    assert "const res = await _fetchWithTimeout('/api/cookbook/tasks/status'" in source
    assert "_fetchWithTimeout('/api/cookbook/state'" in source
    assert "_fetchWithTimeout('/api/model-endpoints/probe-local'" in source


def test_stale_task_self_heal_is_single_flight_and_bounded():
    source = _read("static/js/cookbookRunning.js")

    assert "let _selfHealInFlight = null;" in source
    assert "async function _selfHealStaleTasksImpl(opts = {})" in source
    assert "if (_selfHealInFlight) return _selfHealInFlight;" in source
    assert "_fetchWithTimeout('/api/shell/exec'" in source


def test_cookbook_state_sync_is_single_flight():
    source = _read("static/js/cookbookRunning.js")

    assert "let _syncFromServerInFlight = null;" in source
    assert "async function _syncFromServerImpl()" in source
    assert "if (_syncFromServerInFlight) return _syncFromServerInFlight;" in source
    assert "pending.then(clearPending, clearPending);" in source
    assert "const tracked = run.finally(() =>" in source


def test_cookbook_state_sync_requests_are_bounded():
    source = _read("static/js/cookbookRunning.js")

    assert "_fetchWithTimeout('/api/cookbook/state', { credentials: 'same-origin' }, 15000)" in source
    assert "_fetchWithTimeout('/api/cookbook/state', {" in source
    assert "}, 15000);" in source
    assert source.count("fetch(") == 1  # the helper's own underlying request


def test_dependency_probe_cache_is_scoped_and_invalidated_after_install():
    source = _read("routes/shell_routes.py")

    assert "_PACKAGE_STATUS_CACHE" in source
    assert "package_cache_key = (" in source
    assert "(host or \"\").strip()" in source
    assert "(venv or \"\").strip()" in source
    assert "_PACKAGE_STATUS_CACHE[package_cache_key]" in source
    assert "_PACKAGE_STATUS_CACHE_MAX = 64" in source
    assert "oldest_key = min(_PACKAGE_STATUS_CACHE" in source
    assert "_PACKAGE_STATUS_CACHE.clear()" in source


def test_terminal_download_status_skips_remote_cache_probes():
    source = _read("routes/cookbook_routes.py")

    assert "_persisted_terminal = _task_status in" in source
    assert "or (not _persisted_terminal and _download_cache_incomplete(" in source
    assert "and not _persisted_terminal\n                    and not download_has_incomplete_evidence" in source


def test_download_start_has_concurrent_click_guard():
    source = _read("static/js/cookbookDownload.js")

    assert "const _downloadStartsInFlight = new Set();" in source
    assert "_downloadStartsInFlight.has(startKey)" in source
    assert "_downloadStartsInFlight.add(startKey);" in source
    assert "_downloadStartsInFlight.delete(startKey);" in source


def test_dependency_scan_only_latest_response_can_render():
    source = _read("static/js/cookbook.js")

    assert "let _dependenciesFetchId = 0;" in source
    assert "const fetchId = ++_dependenciesFetchId;" in source
    assert "if (fetchId !== _dependenciesFetchId) return;" in source
    assert source.count("if (fetchId !== _dependenciesFetchId) return;") >= 2


def test_dependency_refresh_preserves_same_target_rows():
    source = _read("static/js/cookbook.js")

    assert "const preserveRows = !!list.querySelector('.cookbook-dep-row')" in source
    assert "list.dataset.cookbookDepsScanSig === scanSig" in source
    assert "Refreshing packages…" in source
    assert "list.prepend(loading);" in source
    assert "list.replaceChildren(loading);" in source


def test_dependency_probe_has_bounded_http_request_and_status_error():
    source = _read("static/js/cookbook.js")

    assert "let _dependenciesRequestController = null;" in source
    assert "_dependenciesRequestController?.abort();" in source
    assert "const depController = new AbortController();" in source
    assert "setTimeout(() => depController.abort(), 20000)" in source
    assert "signal: depController.signal" in source
    assert "if (!resp.ok) throw new Error(`HTTP ${resp.status}" in source
    assert "} finally {\n    clearTimeout(depTimer);" in source
    assert "_dependenciesRequestController === depController" in source


def test_server_color_picker_cleanup_precedes_settings_row_rebuild():
    picker = _read("static/js/cookbook-hwfit.js")
    render = _read("static/js/cookbook.js")

    assert "const onDocumentClick = () => close();" in picker
    assert "document.removeEventListener('click', onDocumentClick);" in picker
    assert "entry._cleanupColorPicker = () =>" in picker
    assert "body.querySelectorAll('.cookbook-server-entry').forEach(entry =>" in render
    assert "entry._cleanupColorPicker?.();" in render


def test_hwfit_custom_picker_listeners_are_cleaned_before_render():
    picker = _read("static/js/cookbook-hwfit.js")
    render = _read("static/js/cookbook.js")

    assert "wrap._cleanupUsecasePicker = () =>" in picker
    assert "wrap._cleanupEnginePicker = () =>" in picker
    assert "document.removeEventListener('keydown', onDocumentKeydown);" in picker
    assert "body.querySelectorAll('.hwfit-usecase-wrap').forEach(wrap =>" in render
    assert "body.querySelectorAll('.hwfit-engine-wrap').forEach(wrap =>" in render


def test_hwfit_scan_controls_are_idempotently_bound():
    source = _read("static/js/cookbook-hwfit.js")

    assert source.count("dataset.hwfitScanBound") >= 5
    assert "if (uc && !uc.dataset.hwfitScanBound)" in source
    assert "if (sort && !sort.dataset.hwfitScanBound)" in source
    assert "if (qpref && !qpref.dataset.hwfitScanBound)" in source
    assert "if (engine && !engine.dataset.hwfitScanBound)" in source
    assert "if (search && !search.dataset.hwfitScanBound)" in source


def test_cookbook_close_cancels_active_scans():
    cookbook = _read("static/js/cookbook.js")
    hwfit = _read("static/js/cookbook-hwfit.js")
    serve = _read("static/js/cookbookServe.js")

    assert "export function _cancelHwfitRequests()" in hwfit
    assert "export function _cancelCachedModelScan()" in serve
    assert "_cancelHwfitRequests();" in cookbook
    assert "_cancelCachedModelScan();" in cookbook


def test_cookbook_close_cancels_dependency_refresh():
    source = _read("static/js/cookbook.js")
    close_start = source.index("function _doClose()")
    close_end = source.index("  const modal =", close_start)
    close_block = source[close_start:close_end]
    assert "_dependenciesFetchId++;" in close_block
    assert "_dependenciesRequestController?.abort();" in close_block
    assert "_dependenciesRequestController = null;" in close_block


def test_launch_dependency_preflight_preserves_backend_and_model_context():
    source = _read("static/js/cookbook-hwfit.js")

    assert "params.set('backend', runBackend);" in source
    assert "params.set('model_hint', modelName);" in source
    assert "if (!r.ok) return true; // An unavailable probe must not falsely block launch." in source


def test_dependency_refresh_passes_cached_models_without_family_filtering():
    source = _read("static/js/cookbook.js")

    assert "Array.from(_cachedModelIds)" in source
    assert ".filter(id => /krea/i.test" not in source
    assert "_pkgParams.set('model_hint', _hint);" in source


def test_dependency_api_does_not_hide_image_capabilities_by_model_name():
    source = _read("routes/shell_routes.py")

    assert "has_krea_model" not in source
    assert "has_lama_mlx_model" not in source
    assert "has_ddcolor_mlx_model" not in source
    assert 'p.get("name") not in {"krea_diffusers", "transformers"}' not in source


def test_dependency_status_cache_is_not_fragmented_by_unused_model_hint():
    source = _read("routes/shell_routes.py")
    start = source.index("package_cache_key = (")
    end = source.index(")\n        cached_status", start)
    assert "model_hint" not in source[start:end]


def test_model_lists_fade_after_complete_rows_are_painted():
    hwfit = _read("static/js/cookbook-hwfit.js")
    cookbook = _read("static/js/cookbook.js")
    style = app_css()

    assert "cookbook-model-list-fade" in hwfit
    assert "cookbook-model-list-fade" in cookbook
    assert "@keyframes cookbook-model-list-fade-in" in style
    assert "cookbook-hwfit-models-just-loaded" in hwfit
    assert "cookbook-hwfit-models-just-loaded" in style
    assert "prefers-reduced-motion" in style


def test_dependency_panel_has_stable_categories_and_mobile_build_action():
    cookbook = _read("static/js/cookbook.js")
    style = app_css()

    assert "const _depCategoryOrder = ['System', 'Tools', 'LLM', 'Image'" in cookbook
    assert "const _orderedDepCategories = (byCat)" in cookbook
    assert "String(p.name || '').startsWith('mlx_')" in cookbook
    assert "cookbook-dep-build-deps" in cookbook
    assert ".cookbook-dep-build-deps" in style
    assert "white-space: normal" in style
    assert ".cookbook-dep-row > .cookbook-dep-info" in style
    assert ".cookbook-dep-row > .cookbook-dep-tag" in style
    assert "transform: translateY(2px)" in style
    assert ".cookbook-dep-row > .cookbook-dep-installed-btn" in style
    assert ".cookbook-dep-row > .cookbook-dep-install" in style


def test_direct_download_auto_fold_requires_deliberate_boundary_gesture():
    source = _read("static/js/cookbook.js")
    assert "y > prev + 12" in source
    assert "e.deltaY < -8" in source
    assert "endY - _touchStartY > 24" in source


def test_launch_command_box_uses_accent_focus_glow():
    style = app_css()
    assert ".hwfit-serve-cmd-details[open] .hwfit-serve-cmd" in style
    assert "box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent" in style


def test_server_settings_color_picker_uses_dot_trigger():
    cookbook = _read("static/js/cookbook.js")
    hwfit = _read("static/js/cookbook-hwfit.js")
    style = app_css()
    assert 'aria-label="Change server color (currently ${esc(selectedColorLabel)})"' in cookbook
    assert "btn.setAttribute('aria-label', `Change server color (currently ${label})`)" in hwfit
    assert ".cookbook-server-row .cookbook-srv-color-dot" in style
    assert "width: 12px" in style
    assert "background: transparent" in style
    assert ".cookbook-server-row .cookbook-srv-color-btn:hover" in style


def test_default_server_matches_exclusive_model_directory_selector():
    cookbook = _read("static/js/cookbook.js")
    hwfit = _read("static/js/cookbook-hwfit.js")
    style = app_css()
    assert "const icon = active ? _MODELDIR_CHECK_ON : _MODELDIR_CHECK_OFF;" in cookbook
    assert "_envState.defaultServer = key;" in hwfit
    assert "Toggle off if it's already the default" not in hwfit
    assert ".cookbook-srv-default-icon svg" in style


def test_trending_models_use_headroom_when_filtering_vram():
    source = _read("routes/cookbook_routes.py")
    assert "usable_vram = vram_gb * 0.90" in source
    assert "needed_vram > usable_vram" in source


def test_dependency_deeplink_preserves_the_failed_model_hint():
    cookbook = _read("static/js/cookbook.js")
    diagnosis = _read("static/js/cookbook-diagnosis.js")

    assert "let _dependenciesModelHint = '';" in cookbook
    assert "_dependenciesModelHint," in cookbook
    assert "dependencyModel: opts.model || ''" in diagnosis
    assert "_dependenciesModelHint = String(opts?.dependencyModel || '').trim();" in cookbook


def test_services_exports_are_lazy_for_cookbook_startup():
    source = _read("services/__init__.py")

    assert "from importlib import import_module" in source
    assert "_LAZY_EXPORTS" in source
    assert "def __getattr__(name):" in source
    assert "from .search import" not in source
    assert "from .docs import" not in source
    assert "from .research import" not in source
    assert "from .memory import" not in source
    assert "from .shell import" not in source


def test_image_catalog_uses_persistent_stale_cache():
    source = _read("services/hwfit/image_models.py")

    assert "_IMAGE_COLLECTION_DISK_CACHE" in source
    assert "_IMAGE_COLLECTION_DISK_TTL" in source
    assert "cached_models" in source
    assert "tmp.replace(_IMAGE_COLLECTION_DISK_CACHE)" in source
    assert "Preserve stale results if the network is unavailable" in source


def test_schedule_api_calls_are_bounded():
    source = _read("static/js/cookbookSchedule.js")

    assert "function fetchWithTimeout(input, init = {}, timeoutMs = 20000)" in source
    assert "return fetch(input, { ...init, signal: controller.signal }).finally" in source
    assert source.count("fetchWithTimeout(") >= 5
    assert "await fetch(\"/api/tasks\"" not in source
    assert "await fetch(\"/api/calendar/calendars\"" not in source
    assert "await fetch(\"/api/calendar/events\"" not in source


def test_diagnosis_quick_commands_have_a_bounded_request():
    source = _read("static/js/cookbook-diagnosis.js")

    assert "function _diagFetchWithTimeout(input, init = {}, timeoutMs = 75000)" in source
    assert "const timer = setTimeout(() => controller.abort(), timeoutMs);" in source
    assert "await _diagFetchWithTimeout('/api/shell/exec'" in source


def test_serve_scans_have_timeouts_and_do_not_overlap_gpu_polls():
    source = _read("static/js/cookbookServe.js")

    assert "function _fetchCookbookWithTimeout(input, init = {}, timeoutMs = 20000)" in source
    assert "setTimeout(() => controller.abort(), timeoutMs)" in source
    assert "_fetchCookbookWithTimeout(`/api/model/cached${params}`" in source
    assert "_fetchCookbookWithTimeout('/api/cookbook/gpus'" in source
    assert "let _vramMonitorInFlight = false;" in source
    assert "if (_vramMonitorInFlight) return true;" in source
    assert "_vramMonitorInFlight = false;" in source


def test_cached_model_scans_are_single_flight_per_target():
    source = _read("static/js/cookbookServe.js")

    assert "async function _fetchCachedModelsImpl(fresh = false, opts = {})" in source
    assert "const _cachedModelsInFlight = new Map();" in source
    assert "const existing = _cachedModelsInFlight.get(key);" in source
    assert "if (existing) return existing;" in source
    assert "pending.then(clearPending, clearPending);" in source


def test_superseded_cached_model_scans_are_cancelled():
    source = _read("static/js/cookbookServe.js")

    assert "let _cachedModelsRequestController = null;" in source
    assert "_cachedModelsRequestController?.abort();" in source
    assert "const _requestController = new AbortController();" in source
    assert "signal: _requestController.signal" in source


def test_hwfit_network_actions_have_bounded_requests():
    source = _read("static/js/cookbook-hwfit.js")

    assert "function _fetchHwfitWithTimeout(input, init = {}, timeoutMs = 20000)" in source
    assert "setTimeout(() => controller.abort(), timeoutMs)" in source
    assert "_fetchHwfitWithTimeout(endpoint, { signal: _requestController.signal }, 30000)" in source
    assert "const fetchToken = ++_cachedModelIdsFetchToken;" in source
    assert "_fetchHwfitWithTimeout(`/api/model/cached?${params}`" in source
    assert "fetchToken !== _cachedModelIdsFetchToken" in source
    assert "_fetchHwfitWithTimeout('/api/model/serve'" in source
    assert "_fetchHwfitWithTimeout('/api/cookbook/test-ssh'" in source
    assert "await fetch(" not in source


def test_serve_gpu_and_context_actions_use_bounded_requests():
    source = _read("static/js/cookbookServe.js")

    assert "_fetchCookbookWithTimeout(`/api/hwfit/profiles?${params}`, {}, 30000)" in source
    assert "_fetchCookbookWithTimeout(url, { credentials: 'same-origin' }, 10000)" in source
    assert source.count("_fetchCookbookWithTimeout('/api/cookbook/gpus'") >= 3
    assert source.count("_fetchCookbookWithTimeout('/api/cookbook/kill-pid'") >= 3
    assert "await fetch(" not in source


def test_download_control_requests_are_bounded_but_sse_stream_remains_cancellable():
    source = _read("static/js/cookbookDownload.js")

    assert "function _fetchDownloadControlWithTimeout(input, init = {}, timeoutMs = 20000)" in source
    assert "setTimeout(() => controller.abort(), timeoutMs)" in source
    assert "_fetchDownloadControlWithTimeout('/api/shell/exec'" in source
    assert "_fetchDownloadControlWithTimeout('/api/model/download'" in source
    assert "const res = await fetch('/api/shell/stream'" in source
    assert "signal: controller.signal" in source


def test_main_cookbook_catalog_lookups_surface_http_failures_and_timeout():
    source = _read("static/js/cookbook.js")

    assert "function _fetchCookbookUiWithTimeout(input, init = {}, timeoutMs = 20000)" in source
    assert "_fetchCookbookUiWithTimeout(`/api/hwfit/system?${qp}`, {}, 20000)" in source
    assert "_fetchCookbookUiWithTimeout(`/api/cookbook/hf-latest?${params}`, {}, 30000)" in source
    assert "_fetchCookbookUiWithTimeout(`/api/cookbook/ollama/library${refresh ? '?refresh=1' : ''}`, {}, 30000)" in source
    assert "if (!res.ok) throw new Error(`HTTP ${res.status}`);" in source


def test_hwfit_superseded_scans_are_cancelled_and_cannot_paint_errors():
    source = _read("static/js/cookbook-hwfit.js")

    assert "let _hwfitRequestController = null;" in source
    assert "_hwfitRequestController?.abort();" in source
    assert "const _requestController = new AbortController();" in source
    assert "signal: _requestController.signal" in source
    assert source.count("_tk !== _hwfitFetchToken") >= 4


def test_ollama_catalog_lookup_is_single_flight_and_checks_http_status():
    source = _read("static/js/cookbook-hwfit.js")

    assert "let _ollamaLibInFlight = null;" in source
    assert "if (_ollamaLibInFlight) return _ollamaLibInFlight;" in source
    assert "if (!res.ok) throw new Error(`HTTP ${res.status}`);" in source
    assert "pending.then(clearPending, clearPending);" in source


def test_bulk_task_clear_uses_shared_card_disposer():
    source = _read("static/js/cookbookRunning.js")

    assert "toRemove.forEach(t => {\n        const el = document.querySelector(`.cookbook-task[data-task-id=\"${t.sessionId}\"]`);\n        if (el) {\n          _disposeTaskCard(el);" in source
    assert "_disposeTaskCard(el);\n            el.remove();" in source


def test_task_card_disposer_cancels_mobile_long_press():
    source = _read("static/js/cookbookRunning.js")

    assert "if (el._longPressTimer)" in source
    assert "el._longPressTimer = setTimeout" in source
    assert "el._longPressTimer = null;" in source


def test_cached_model_rerender_cancels_mobile_long_press():
    source = _read("static/js/cookbookServe.js")

    assert "list.querySelectorAll('.memory-item').forEach(item =>" in source
    assert "item._longPressTimer" in source
    assert "clearTimeout(item._longPressTimer);" in source


def test_main_cookbook_control_actions_use_bounded_requests():
    source = _read("static/js/cookbook.js")

    assert source.count("_fetchCookbookUiWithTimeout('/api/model/serve'") >= 3
    assert "_fetchCookbookUiWithTimeout('/api/cookbook/install-system-deps'" in source
    assert "_fetchCookbookUiWithTimeout('/api/cookbook/rebuild-engine'" in source
    assert "_fetchCookbookUiWithTimeout(`/api/cookbook/hf-gguf-files?repo_id=" in source


def test_cookbook_opens_from_cached_state_before_background_sync():
    source = _read("static/js/cookbook.js")

    assert "const _stateSync = _syncFromServer();" in source
    assert source.index("_renderCookbookShell();") < source.index("const _stateSync = _syncFromServer();")
    assert "modal.classList.remove('hidden');" in source
    assert "_stateSync.then((synced) =>" in source
    assert "never replace a form while the" in source
    assert "let _cookbookOpenGeneration = 0;" in source
    assert "_openGeneration !== _cookbookOpenGeneration" in source
    assert "if (activeTab === 'Running' || activeTab === 'Serve') return;" in source
    assert "const _initialStateSignature = _cookbookStateSignature();" in source
    assert "if (_cookbookStateSignature() === _initialStateSignature) return;" in source


def test_advanced_scan_dismissal_is_single_document_binding():
    source = _read("static/js/cookbook.js")

    assert "document._cookbookAdvancedDismissWired" in source
    assert "const currentPanel = document.getElementById('hwfit-advanced-panel');" in source
    assert "document.addEventListener('click', () => setAdvancedOpen(false));" not in source
    assert "document.addEventListener('keydown', (ev) => {\n      if (ev.key === 'Escape') setAdvancedOpen(false);" not in source


def test_serve_panel_removes_global_handlers_when_collapsed():
    source = _read("static/js/cookbookServe.js")
    cookbook_source = _read("static/js/cookbook.js")

    assert "let _cleanupBackendDismiss = () => {};" in source
    assert "panel._cleanupServePanel = () => {" in source
    assert "_cleanupBackendDismiss?.();" in source
    assert "document.removeEventListener('keydown', _onEscClose, true);" in source
    assert "existingPanel?._cleanupServePanel?.();" in source
    assert "openPanel?._cleanupServePanel?.();" in source
    assert "clearInterval(_vramTimer);" in source
    assert "expandedPanel?._cleanupServePanel?.();" in cookbook_source


def test_gpu_probe_popup_removes_deferred_outside_listener_on_close():
    source = _read("static/js/cookbookServe.js")

    assert "panel._gpuProbe.outsideAttachTimer" in source
    assert "panel._gpuProbe.outsideHandler" in source
    assert "document.removeEventListener('mousedown', panel._gpuProbe.outsideHandler, true);" in source
    assert "_closeProbePopup();" in source


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
