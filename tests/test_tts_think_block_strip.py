"""Read-aloud TTS must never speak model reasoning.

``copyMessageText`` already strips thinking blocks for copied text. Live/streamed
TTS goes through ``AITTSManager.extractPlainText`` instead, so that path must
strip ``<think>`` markup, reasoning custom elements/classes, and unfinished
thinking blocks before reading or synthesizing text.
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _extract_plain_text(content: str) -> str:
    script = textwrap.dedent(
        r"""
        import fs from 'node:fs';

        function makeDocument() {
          return {
            createElement() {
              let html = '';
              return {
                set innerHTML(value) { html = String(value); },
                get innerHTML() { return html; },
                querySelectorAll(selector) {
                  const removers = [];
                  selector.split(',').map(s => s.trim()).filter(Boolean).forEach(sel => {
                    let re;
                    if (sel.startsWith('.')) {
                      const cls = sel.slice(1).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                      re = new RegExp('<([a-zA-Z0-9:-]+)\\b[^>]*class="[^"]*\\b' + cls + '\\b[^"]*"[^>]*>[\\s\\S]*?<\\/\\1>', 'gi');
                    } else {
                      const tag = sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                      re = new RegExp('<' + tag + '\\b[^>]*>[\\s\\S]*?<\\/' + tag + '>', 'gi');
                    }
                    let match;
                    while ((match = re.exec(html))) {
                      const matched = match[0];
                      removers.push({ remove() { html = html.replace(matched, ''); } });
                    }
                  });
                  return removers;
                },
                get textContent() { return html.replace(/<[^>]*>/g, ''); },
              };
            },
          };
        }

        const source = fs.readFileSync('./static/js/tts-ai.js', 'utf8');
        const marker = 'extractPlainText(content) {';
        const start = source.indexOf(marker);
        if (start < 0) throw new Error('extractPlainText not found');
        const brace = source.indexOf('{', start);
        let depth = 0;
        let end = -1;
        for (let i = brace; i < source.length; i += 1) {
          if (source[i] === '{') depth += 1;
          else if (source[i] === '}') {
            depth -= 1;
            if (depth === 0) { end = i; break; }
          }
        }
        if (end < 0) throw new Error('extractPlainText body not closed');
        const body = source.slice(brace, end + 1);
        const extract = new Function(
          'document',
          'return function extractPlainText(content) ' + body + ';'
        )(makeDocument());
        const input = JSON.parse(process.argv[1]);
        console.log(JSON.stringify({ out: extract(input) }));
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(content)],
        cwd=ROOT,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
    return json.loads(result.stdout)["out"]


def test_extract_plain_text_strips_closed_think_block_and_reasoning_elements():
    raw = (
        "<think>secret reasoning that must not be spoken</think>"
        '<div class="thinking-section">hidden section</div>'
        '<div class="thinking-content">hidden content</div>'
        '<div class="thinking-header">Thinking...</div>'
        "<p>Hello <strong>world</strong>.</p>"
    )
    assert _extract_plain_text(raw) == "Hello world."


def test_extract_plain_text_strips_unclosed_streaming_think_block():
    raw = "Visible answer. <think>still thinking and not finished"
    assert _extract_plain_text(raw) == "Visible answer."


def test_extract_plain_text_strips_raw_think_element_before_dom():
    raw = "<think>private</think><thinking>also private</thinking><p>Speak this.</p>"
    assert _extract_plain_text(raw) == "Speak this."


def test_tts_module_strips_thinking_before_textcontent_and_streaming_uses_it():
    source = (ROOT / "static/js/tts-ai.js").read_text(encoding="utf-8")

    assert "think, .thinking-section, .thinking-content, .thinking-header" in source
    assert "temp.querySelectorAll('think, .thinking-section" in source
    assert "temp.querySelectorAll('pre, code')" in source
    # The HTML removal must happen before textContent is read.
    assert source.index("temp.querySelectorAll('think, .thinking-section") < source.index("temp.textContent")
    # Both live streaming and final streaming paths call extractPlainText.
    streaming = source[source.index("_processStreamingSentences(accumulatedText)"):]
    assert "extractPlainText(text)" in streaming
    assert "extractPlainText(text)" in source[source.index("streamingEnd(finalText)"):]
