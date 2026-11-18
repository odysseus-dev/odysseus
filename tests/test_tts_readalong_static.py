"""Static and unit checks for in-place TTS read-along word highlighting."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TTS_JS = ROOT / "static" / "js" / "tts-ai.js"
CHAT_CSS = ROOT / "static" / "css" / "03-agent-chat.css"
TTS_VERSION = "20261105ttsscroll1"
CSS_VERSION = "20261105followupleft"


def test_readalong_highlights_the_existing_answer_in_place():
    source = TTS_JS.read_text(encoding="utf-8")

    # No floating copy of the answer above the composer.
    assert "el.className = 'tts-readalong';" not in source
    assert "document.querySelector('.chat-input-bar')" not in source
    assert "tts-readalong__scroller" not in source

    # Resolve the existing assistant message body instead of building a panel.
    assert "_resolveReadalongBody(messageElement)" in source
    assert "messageElement.closest('.msg')" in source
    assert "const body = msg.querySelector('.body');" in source
    assert "if (!body.isConnected) return null;" in source

    # Wrap visible text nodes only, preserving markdown/link markup.
    assert "document.createTreeWalker(body, 4" in source
    assert "parent.closest(excluded)" in source
    assert "span.className = 'tts-word';" in source
    assert "parent.replaceChild(frag, node)" in source


def test_readalong_excludes_non_answer_dom():
    source = TTS_JS.read_text(encoding="utf-8")

    for selector in (
        ".thinking-section",
        ".thinking-content",
        ".thinking-header",
        ".sources-section",
        ".chat-images",
        ".followup-suggestions",
        ".msg-footer",
        "pre",
        "code",
    ):
        assert repr(selector) in source or f"'{selector}'" in source, selector


def test_word_map_aligns_with_cleaned_tts_text():
    source = TTS_JS.read_text(encoding="utf-8")

    assert "function normalizeReadalongToken(token)" in source
    assert "function alignReadalongWords(ttsWords, bodyTokens)" in source
    assert "alignReadalongWords(words, bodyTokens);" in source
    assert "this._buildReadalong(plain, bodyTokens);" in source
    assert "const plainText = this.extractPlainText(text);" in source
    assert "this._startReadalong(plainText, 'audio', audio, messageElement);" in source

    assert "utterance.onboundary = (event) =>" in source
    assert "event.charIndex" in source
    assert "event.name === 'word'" in source
    assert "this._setReadalongCharIndex(event.charIndex)" in source
    assert "if (pos < word.start)" in source
    assert "this._setReadalongWord(index, 1)" in source


def test_audio_tts_estimates_char_weighted_timing():
    source = TTS_JS.read_text(encoding="utf-8")

    assert "audio.currentTime / duration" in source
    assert "this._highlightReadalongWeight(progress * this._readalongTotalWeight)" in source
    assert "weightStart: totalWeight" in source
    assert "requestAnimationFrame(tick)" in source
    assert "addEventListener('timeupdate'" in source


def test_readalong_notifies_the_scroll_button_after_wrapping():
    source = TTS_JS.read_text(encoding="utf-8")
    start = source[source.index("    _startReadalong(text, provider, audio, messageElement) {"):source.index("    _stopReadalongTimer() {")]
    assert "window.dispatchEvent(new CustomEvent('odysseus-tts-state-change'))" in start
    chat = (ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    assert "window.aiTTSManager.streamingStart(holder)" in chat


def test_readalong_scrolls_the_chat_and_respects_user_scroll_follow():
    source = TTS_JS.read_text(encoding="utf-8")

    assert "el.closest('.chat-history') || document.getElementById('chat-history')" in source
    assert "typeof ui.getAutoScroll === 'function' && !ui.getAutoScroll()" in source
    assert "ui.beginProgrammaticScroll?.(ms)" in source
    assert "ui?.endProgrammaticScroll?.(200)" in source
    assert "wordRect.top >= scrollRect.top + pad" in source
    assert "scroller.scrollTo({ top: target, behavior: 'smooth' })" in source
    assert "scroller.scrollTop = target;" in source


def test_readalong_lifecycle_covers_stop_pause_end_error_and_restore():
    source = TTS_JS.read_text(encoding="utf-8")

    set_word = source[source.index("    _setReadalongWord(index, fraction) {"):source.index("    _markReadalongScroll(")]
    assert "classList.remove('tts-word-active')" in set_word
    assert "classList.add('tts-word-active')" in set_word
    assert "this._stopReadalong();" in set_word

    stop = source[source.index("    _stopReadalong() {"):source.index("    async play(text) {")]
    assert "this._stopReadalongTimer();" in stop
    assert "this._unbindReadalongAudio();" in stop
    assert "this._endReadalongScroll();" in stop
    assert "parent.replaceChild(document.createTextNode(span.textContent || ''), span)" in stop
    assert "parent.normalize()" in stop
    assert "this._readalongWordSpans = [];" in stop
    assert "this._readalongWords = [];" in stop
    assert "this._readalongBody = null;" in stop

    queue = source[source.index("async _playQueueItem(item)"):source.index("// ── Streaming TTS")]
    assert queue.count("this._stopReadalong();") >= 5

    browser = source[source.index("_playBrowser(plainText, messageElement) {"):source.index("    stop() {")]
    assert "utterance.onend = () =>" in browser
    assert "utterance.onerror = (e) =>" in browser
    assert browser.count("this._stopReadalong();") >= 3


def test_readalong_css_is_in_place_themed_and_reduced_motion_safe():
    css = CHAT_CSS.read_text(encoding="utf-8")

    assert ".tts-readalong" not in css
    assert ".tts-word {" in css
    assert ".tts-word-active {" in css
    assert "var(--accent, var(--red))" in css
    assert "text-decoration: underline;" in css
    assert "var(--tts-word-progress, 1)" in css
    assert "@media (max-width: 600px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".tts-word { transition: none; }" in css


def test_readalong_cache_bust_is_wired_through_consumers():
    for relative in (
        "static/index.html",
        "static/app.js",
        "static/sw.js",
        "static/js/chat.js",
        "static/js/chatRenderer.js",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert f"tts-ai.js?v={TTS_VERSION}" in source, relative

    for relative in ("static/index.html", "static/sw.js"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert f"03-agent-chat.css?v={CSS_VERSION}" in source, relative


def test_scroll_button_uses_reading_position_visibility():
    tts = TTS_JS.read_text(encoding="utf-8")
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")

    assert "isReadingPositionVisible()" in tts
    assert "window.aiTTSManager.isReadingPositionVisible" in index
    assert "mode = readingVisible ? 'pause' : 'down'" in index
    assert "mode = readingVisible ? 'play' : 'down'" in index
    assert "mode === 'down' && (ttsState === 'playing' || ttsState === 'paused')" in index


@pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")
def test_readalong_alignment_skips_markdown_remnants_and_unmatched_tokens():
    script = textwrap.dedent(
        r"""
        import fs from 'node:fs';

        const source = fs.readFileSync('./static/js/tts-ai.js', 'utf8');
        function extractFunction(name) {
          const marker = `function ${name}(`;
          const start = source.indexOf(marker);
          if (start < 0) throw new Error(`${name} not found`);
          const brace = source.indexOf('{', start);
          let depth = 0;
          for (let i = brace; i < source.length; i += 1) {
            if (source[i] === '{') depth += 1;
            else if (source[i] === '}') {
              depth -= 1;
              if (depth === 0) return source.slice(start, i + 1);
            }
          }
          throw new Error(`${name} body not closed`);
        }

        const normalize = new Function(
          extractFunction('normalizeReadalongToken') + '; return normalizeReadalongToken;'
        )();
        const align = new Function(
          extractFunction('alignReadalongWords') + '; return alignReadalongWords;'
        )();
        const spoken = ['#', '-', 'Hello,', 'world.', '—', '**Extra**', 'again']
          .map(token => ({ token, norm: normalize(token), el: null }));
        const visible = ['Hello,', 'world.', 'again']
          .map((token, id) => ({ token, norm: normalize(token), el: { id } }));
        align(spoken, visible);

        const longSpoken = ['Hello', 'world']
          .concat(Array.from({ length: 30 }, (_, i) => `code${i}`))
          .concat(['Again'])
          .map(token => ({ token, norm: normalize(token), el: null }));
        const longVisible = ['Hello', 'world', 'Again']
          .map((token, id) => ({ token, norm: normalize(token), el: { id } }));
        align(longSpoken, longVisible);

        console.log(JSON.stringify({
          norms: spoken.map(word => word.norm),
          mapped: spoken.map(word => word.el ? word.el.id : null),
          longMapped: longSpoken[longSpoken.length - 1].el ? longSpoken[longSpoken.length - 1].el.id : null,
        }));
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    values = json.loads(result.stdout)
    assert values["norms"] == ["", "", "hello", "world", "", "extra", "again"]
    assert values["mapped"] == [None, None, 0, 1, None, None, 2]
    assert values["longMapped"] == 2


@pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")
def test_tts_js_parses_with_node():
    subprocess.run(
        ["node", "--check", str(TTS_JS)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
