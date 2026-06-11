"""Regression coverage for issue #3722 — the message copy button copied the
full raw model output (``dataset.raw``), which still contains the
``<think time="...">...</think>`` reasoning block that the renderer strips for
display. Pasting therefore leaked the model's thinking, and the first heading
after ``</think>`` lost its markdown formatting because it was glued to the
closing tag.

The fix adds chatRenderer.copyMessageText(), which mirrors the display
pipeline (``stripToolBlocks()`` then ``extractThinkingBlocks()``), and routes
both AI-message copy buttons (createMsgFooter and the slash-reply footer)
through it. extractThinkingBlocks() behavior is pinned here under node
(including on the payload from the issue report); the helper and handler
wiring are guarded at the source level because chatRenderer.js pulls in
browser globals and can't be imported under node (same approach as
test_new_chat_clears_input.py).
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _extract_thinking_blocks(text: str) -> dict:
    """Run markdown.js extractThinkingBlocks(text) under node."""
    script = textwrap.dedent(
        r"""
        import fs from 'node:fs';

        globalThis.window = { location: { origin: 'http://localhost' }, katex: null };
        globalThis.document = {
          readyState: 'loading',
          addEventListener() {},
          createElement(tag) {
            if (tag !== 'template') throw new Error(`unsupported element: ${tag}`);
            return {
              _html: '',
              content: { querySelectorAll() { return []; } },
              set innerHTML(value) { this._html = value; },
              get innerHTML() { return this._html; },
            };
          },
        };
        globalThis.MutationObserver = class { observe() {} };

        let source = fs.readFileSync('./static/js/markdown.js', 'utf8');
        source = source.replace(
          /import uiModule from ['"]\.\/ui\.js['"];/,
          ''
        );
        source = source.replace(
          /import \{ splitTableRow \} from ['"]\.\/markdown\/tableRow\.js['"];/,
          `function splitTableRow(row) {
            return (row || '').replace(/^\\s*\\|/, '').replace(/\\|\\s*$/, '').split('|').map(c => c.trim());
          }`
        );
        const emojiSource = fs.readFileSync('./static/js/emojiShortcodes.js', 'utf8')
          .replace(/^export default .*$/m, '')
          .replace(/export const /g, 'const ')
          .replace(/export function /g, 'function ');
        source = source.replace(
          /import \{ replaceEmojiShortcodes, hasEmojiShortcode \} from ['"]\.\/emojiShortcodes\.js['"];/,
          () => emojiSource
        );
        source = source.replace(
          /var escapeHtml = uiModule\.esc;/,
          `var escapeHtml = (value) => String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');`
        );

        const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
        const mod = await import(moduleUrl);
        const input = JSON.parse(process.argv[1]);
        console.log(JSON.stringify({ out: mod.extractThinkingBlocks(input) }));
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(text)],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
    return json.loads(result.stdout.splitlines()[-1])["out"]


def test_issue_payload_copy_text_excludes_thinking(node_available):
    # Shape reported in #3722: timed think block glued to the reply heading.
    raw = (
        '<think time="24.5">\n'
        "Here's a thinking process that leads to the desired summary:\n\n"
        "6.  **Generate the Output.** (This matches the final provided response.)"
        "</think>### Juxtaposition: Interweaving Cultural Norms in Lesson Design\n"
        "The most effective lesson structure is created by deliberately juxtaposing."
    )
    out = _extract_thinking_blocks(raw)

    assert out["content"].startswith("### Juxtaposition:"), out["content"]
    assert "thinking process" not in out["content"]
    assert "<think" not in out["content"]
    assert out["thinkingTime"] == "24.5"


def test_plain_reply_copy_text_is_unchanged(node_available):
    raw = "### Heading\nJust a normal reply with no reasoning markup."
    out = _extract_thinking_blocks(raw)
    assert out["content"] == raw


def test_thinking_only_message_yields_empty_content(node_available):
    # The copy handler falls back to the raw text in this case so the button
    # still copies something for turns interrupted mid-thinking.
    out = _extract_thinking_blocks("<think>only reasoning, no reply yet</think>")
    assert out["content"] == ""


def _function_body(text: str, marker: str) -> str:
    start = text.index(marker)
    rest = text[start + len(marker):]
    m = re.search(r"\nexport function |\nfunction ", rest)
    return rest[: m.start()] if m else rest


def test_copy_message_text_mirrors_display_pipeline():
    text = (_REPO / "static/js/chatRenderer.js").read_text(encoding="utf-8")
    body = _function_body(text, "export function copyMessageText")
    # Mirrors the display path: tool blocks stripped, then thinking extracted.
    assert "extractThinkingBlocks" in body
    assert "stripToolBlocks" in body
    assert "dataset.raw" in body


def test_copy_handlers_route_through_copy_message_text():
    for path, count in (("static/js/chatRenderer.js", 1), ("static/js/slashCommands.js", 1)):
        text = (_REPO / path).read_text(encoding="utf-8")
        assert text.count("copyToClipboard(copyMessageText(") + text.count(
            "copyToClipboard(chatRenderer.copyMessageText("
        ) == count, path
        # The old behavior passed dataset.raw straight to the clipboard.
        assert "copyToClipboard(msgElement.dataset.raw" not in text, path
        assert "copyToClipboard(msgEl.dataset.raw" not in text, path
