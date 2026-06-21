from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_JS = (ROOT / "main.js").read_text(encoding="utf-8")
LAUNCH_PS1 = (ROOT / "launch-windows.ps1").read_text(encoding="utf-8")
THEME_JS = (ROOT / "static" / "js" / "theme.js").read_text(encoding="utf-8")
INSTALLER_PY = (ROOT / "installer.py").read_text(encoding="utf-8")
PACKAGE_JSON = (ROOT / "package.json").read_text(encoding="utf-8")
STARTUP_PRELOAD_JS = (ROOT / "startup-preload.js").read_text(encoding="utf-8")


def test_standalone_electron_experimental_gpu_flags_are_opt_in():
    assert "ODYSSEUS_EXPERIMENTAL_CHROMIUM_GPU" in MAIN_JS
    assert "if (process.env.ODYSSEUS_EXPERIMENTAL_CHROMIUM_GPU === '1')" in MAIN_JS
    assert "app.commandLine.appendSwitch('ignore-gpu-blocklist');" in MAIN_JS
    assert "app.commandLine.appendSwitch('enable-gpu-rasterization');" in MAIN_JS
    assert "app.commandLine.appendSwitch('enable-zero-copy');" in MAIN_JS
    assert "app.commandLine.appendSwitch('enable-accelerated-video-decode');" in MAIN_JS
    assert "CanvasOopRasterization" in MAIN_JS
    assert "RawDraw" in MAIN_JS


def test_standalone_startup_uses_packaged_root_and_no_wait_on_runtime_dependency():
    assert "require('wait-on')" not in MAIN_JS
    assert "const net = require('net');" in MAIN_JS
    assert "const BACKEND_STARTUP_TIMEOUT_MS = 15 * 60 * 1000;" in MAIN_JS
    assert "function waitForBackend(timeoutMs = 60000)" in MAIN_JS
    assert "app.isPackaged ? path.dirname(process.resourcesPath) : __dirname" in MAIN_JS
    assert "backend-startup.log" in MAIN_JS
    assert "path.join(rootDir, 'launch-windows.ps1')" in MAIN_JS
    assert "['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', launcher]" in MAIN_JS
    assert "cwd: rootDir" in MAIN_JS
    assert "ODYSSEUS_DEFER_ADMIN_SETUP: '1'" in MAIN_JS
    assert "ODYSSEUS_SKIP_RUN_HINT: '1'" in MAIN_JS
    assert "env: backendEnv" in MAIN_JS
    assert "windowsHide: true" in MAIN_JS
    assert "waitForBackend(BACKEND_STARTUP_TIMEOUT_MS)" in MAIN_JS
    assert "showStartupFailure(" in MAIN_JS
    assert "http://${BACKEND_HOST}:${BACKEND_PORT}" in MAIN_JS
    assert "createWindow(startupPage(startupProgress));" in MAIN_JS


def test_standalone_startup_page_streams_install_progress():
    assert "const STARTUP_STEPS = [" in MAIN_JS
    assert "Install Process" in MAIN_JS
    assert "Live Output" in MAIN_JS
    assert "function updateStartupProgressFromOutput(message)" in MAIN_JS
    assert "window.__odysseusStartupUpdate" in MAIN_JS
    assert "mainWindow.webContents.executeJavaScript" in MAIN_JS
    assert "appendStartupLogLines(message)" in MAIN_JS
    assert "checking local image edit sidecars" in MAIN_JS
    assert "Dependencies already match requirements.txt" in MAIN_JS
    assert "class=\"copy-btn\"" in MAIN_JS
    assert "width: 22px; height: 22px;" in MAIN_JS
    assert "data-copy-target=\"startup-backend\"" in MAIN_JS
    assert "Copy data path" in MAIN_JS
    assert "Copy log path" in MAIN_JS
    assert "startup:copy-text" in MAIN_JS
    assert "startup-preload.js" in MAIN_JS
    assert "window.odysseusStartupClipboard" in MAIN_JS
    assert "startup-preload.js" in PACKAGE_JSON
    assert "contextBridge.exposeInMainWorld('odysseusStartupClipboard'" in STARTUP_PRELOAD_JS
    assert "let followLog = true;" in MAIN_JS
    assert "shouldFollow ? terminal.scrollHeight : previousScrollTop" in MAIN_JS


def test_standalone_startup_tiles_own_their_scrollbars():
    assert "body {\n      height: 100vh;" in MAIN_JS
    assert "overflow: hidden;" in MAIN_JS
    assert ".startup-shell { display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto;" in MAIN_JS
    assert ".startup-workbench { display: grid; grid-template-columns: minmax(0, 1fr) minmax(330px, 0.36fr); gap: 18px; align-items: stretch; min-height: 0; overflow: hidden; }" in MAIN_JS
    assert ".status-grid { display: grid; grid-template-columns: minmax(286px, 0.72fr) minmax(420px, 1.28fr); gap: 18px; align-items: stretch; min-width: 0; min-height: 0; overflow: hidden; }" in MAIN_JS
    assert ".startup-theme-body { flex: 1 1 auto; min-height: 0; overflow: auto;" in MAIN_JS
    assert ".steps { flex: 1 1 auto; list-style: none; margin: 0; padding: 7px 0; min-height: 0; overflow: auto;" in MAIN_JS
    assert ".terminal { flex: 1 1 auto; min-height: 0; overflow: auto;" in MAIN_JS


def test_standalone_startup_theme_studio_persists_into_app_theme():
    assert "Theme Studio" in MAIN_JS
    assert "startup-theme.json" in MAIN_JS
    assert "startup-theme-presets" in MAIN_JS
    assert "startup-theme-generate" in MAIN_JS
    assert "generateHarmonyColors" in MAIN_JS
    assert "startup:theme-load" in MAIN_JS
    assert "startup:theme-save" in MAIN_JS
    assert "applyStartupThemeToBackendPage" in MAIN_JS
    assert "localStorage.setItem('odysseus-theme', JSON.stringify(theme));" in MAIN_JS
    assert "fetch('/api/prefs/theme'" in MAIN_JS
    assert "contextBridge.exposeInMainWorld('odysseusStartupTheme'" in STARTUP_PRELOAD_JS


def test_standalone_install_preserves_user_data_outside_install_dir():
    assert "function persistentDataDir(rootDir)" in MAIN_JS
    assert "app.getPath('userData'), 'data'" in MAIN_JS
    assert "function migratePackagedData(rootDir, dataDir, logPath)" in MAIN_JS
    assert "copyMissingRecursive(legacyDataDir, dataDir)" in MAIN_JS
    assert "ODYSSEUS_DATA_DIR: dataDir" in MAIN_JS
    assert "backendEnv.DATABASE_URL = sqliteUrlForDataDir(dataDir);" in MAIN_JS


def test_simple_signal_extension_installer_preserves_user_data_outside_code_dir():
    assert 'return Path(app_data) / "odysseus" / "data"' in INSTALLER_PY
    assert 'env["ODYSSEUS_DATA_DIR"] = str(data_dir)' in INSTALLER_PY
    assert 'env["ODYSSEUS_DATA_DIR"] = data_dir' in INSTALLER_PY
    assert 'env["DATABASE_URL"] = sqlite_url_for_data_dir(data_dir)' in INSTALLER_PY
    assert '"sqlite:///" + os.path.join(data_dir, "app.db").replace("\\\\\\\\", "/")' in INSTALLER_PY


def test_windows_launcher_reuses_running_server_and_skips_unchanged_dependencies():
    assert "function Test-OdysseusHealth($port)" in LAUNCH_PS1
    assert "function Get-FileSha256($path)" in LAUNCH_PS1
    assert "http://127.0.0.1:{0}/api/health" in LAUNCH_PS1
    assert "Odysseus is already running at http://127.0.0.1:{0} - reusing it." in LAUNCH_PS1
    assert 'Join-Path $PSScriptRoot ".odysseus-launch.lock"' in LAUNCH_PS1
    assert "[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()" in LAUNCH_PS1
    assert 'Join-Path $PSScriptRoot "venv\\.requirements.sha256"' in LAUNCH_PS1
    assert "$requirementsHash = Get-FileSha256 $requirementsPath" in LAUNCH_PS1
    assert "Get-FileHash" not in LAUNCH_PS1
    assert "Dependencies already match requirements.txt - skipping pip install." in LAUNCH_PS1
    assert "Set-Content -Path $requirementsMarker -Value $requirementsHash -Encoding ASCII" in LAUNCH_PS1


def test_theme_canvas_effects_are_throttled_for_pc_runtime():
    assert "const BG_EFFECT_FRAME_MS = 1000 / 30;" in THEME_JS
    assert "desynchronized: true" in THEME_JS
    assert "function _scheduleBgEffectFrame(draw, state)" in THEME_JS
    assert "document.hidden" in THEME_JS
    assert "setTimeout(() => _scheduleBgEffectFrame(draw, state), 250);" in THEME_JS
    assert "requestAnimationFrame(draw);" not in THEME_JS
