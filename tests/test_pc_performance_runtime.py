from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_JS = (ROOT / "main.js").read_text(encoding="utf-8")
LAUNCH_PS1 = (ROOT / "launch-windows.ps1").read_text(encoding="utf-8")
THEME_JS = (ROOT / "static" / "js" / "theme.js").read_text(encoding="utf-8")


def test_standalone_electron_prefers_gpu_backed_rendering():
    assert "app.commandLine.appendSwitch('ignore-gpu-blocklist');" in MAIN_JS
    assert "app.commandLine.appendSwitch('enable-gpu-rasterization');" in MAIN_JS
    assert "app.commandLine.appendSwitch('enable-zero-copy');" in MAIN_JS
    assert "app.commandLine.appendSwitch('enable-accelerated-video-decode');" in MAIN_JS
    assert "CanvasOopRasterization" in MAIN_JS


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
