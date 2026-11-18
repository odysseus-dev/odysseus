"""Static checks for amplitude-reactive TTS wave bars."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TTS_JS = ROOT / "static" / "js" / "tts-ai.js"
CHAT_CSS = ROOT / "static" / "css" / "03-agent-chat.css"


def test_tts_manager_wires_web_audio_analyser():
    source = TTS_JS.read_text(encoding="utf-8")

    assert "window.AudioContext || window.webkitAudioContext" in source
    assert "this._getAudioContext()" in source
    assert "this._resumeAudioContext()" in source
    assert "ctx.createMediaElementSource(audio)" in source
    assert "ctx.createAnalyser()" in source
    assert "analyser.fftSize = 64" in source
    assert "source.connect(analyser)" in source
    assert "analyser.connect(ctx.destination)" in source
    assert "getByteFrequencyData(data)" in source
    assert "requestAnimationFrame(tick)" in source
    assert "cancelAnimationFrame(this._analyserRaf)" in source
    assert "bars[i].style.transform = 'scaleY('" in source
    assert "const floor = 0.25;" in source
    assert "const ceiling = 1.4;" in source
    assert "classList.add('analyser')" in source
    assert "classList.remove('analyser')" in source


def test_tts_analyser_teardown_covers_playback_lifecycle():
    source = TTS_JS.read_text(encoding="utf-8")

    queue_item = source[source.index("async _playQueueItem(item)"):source.index("// ── Streaming TTS")]
    assert "audio.onended = () =>" in queue_item
    assert "audio.onerror = (e) =>" in queue_item
    assert "audio.onpause = () =>" in queue_item
    assert queue_item.count("_teardownAnalyser(audio)") >= 3

    stop = source[source.index("    stop() {"):source.index("    /**\n     * Enqueue a message")]
    assert "_teardownAnalyser()" in stop


def test_saved_and_streaming_tts_share_the_analyser_button_path():
    source = TTS_JS.read_text(encoding="utf-8")

    assert "const activeButton = item.button || button;" in source
    assert "this._startAnalyser(audio, item.button || button);" in source
    attach = source[source.index("streamingAttachButton(button, resetFn)"):source.index("streamingEnd(finalText)")]
    assert "this._bindAnalyserButton(button);" in attach


def test_tts_analyser_respects_reduced_motion_and_css_fallback():
    source = TTS_JS.read_text(encoding="utf-8")
    css = CHAT_CSS.read_text(encoding="utf-8")

    assert "prefers-reduced-motion: reduce" in source
    assert "if (this._prefersReducedMotion()) return false;" in source
    assert ".ai-tts-button.analyser.playing .tts-wave-bar { animation: none; }" in css
    assert "@keyframes tts-wave-bounce" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".ai-tts-button.playing .tts-wave-bar { animation: none; }" in css


def test_tts_analyser_cache_bust_is_wired_through_consumers():
    version = "20261105ttsscroll1"
    for relative in (
        "static/index.html",
        "static/app.js",
        "static/sw.js",
        "static/js/chat.js",
        "static/js/chatRenderer.js",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert f"tts-ai.js?v={version}" in source, relative


@pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")
def test_tts_js_parses_with_node():
    subprocess.run(
        ["node", "--check", str(TTS_JS)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
