"""Pin the pure emoji shortcode → Unicode helpers in emojiShortcodes.js.

Driven through `node --input-type=module` so we exercise the real JS without a
full Vitest/Jest setup (same approach as test_reply_recipients_js.py / test_compare_js.py).
Skips when `node` is not installed rather than failing.

Regression for issue #345: chat models emit GitHub-style :shortcode: text
(e.g. :blush:, :microphone:) instead of the actual emoji, and nothing in the
render pipeline translated them, so they showed up as literal ":blush:" text.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "emojiShortcodes.js"
_HAS_NODE = shutil.which("node") is not None


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _replace(text: str) -> str:
    js = f"""
    import {{ replaceEmojiShortcodes }} from '{_HELPER.as_posix()}';
    console.log(JSON.stringify(replaceEmojiShortcodes({json.dumps(text)})));
    """
    return json.loads(_run(js))


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_issue_345_examples_convert():
    # The exact shortcodes the issue reported as showing up as literal text.
    assert _replace("visit today? :blush:") == "visit today? \U0001f60a"
    assert _replace("hobbies? **:microphone:**") == "hobbies? **\U0001f3a4**"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_common_shortcodes_and_aliases():
    assert _replace(":fire:") == "\U0001f525"
    assert _replace(":tada:") == "\U0001f389"
    assert _replace(":thinking:") == "\U0001f914"
    # +1 / thumbsup are aliases for the same glyph.
    assert _replace(":+1:") == "\U0001f44d"
    assert _replace(":thumbsup:") == "\U0001f44d"
    # Multiple in one string, mixed with surrounding text.
    assert _replace("nice :fire: work :100:") == "nice \U0001f525 work \U0001f4af"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_unknown_and_nonshortcodes_untouched():
    # Unknown shortcode left verbatim (incl. the :emoji: placeholder).
    assert _replace(":definitely_not_an_emoji:") == ":definitely_not_an_emoji:"
    assert _replace(":emoji:") == ":emoji:"
    # Time ranges / ratios must not be mangled.
    assert _replace("meet at 10:30:45 today") == "meet at 10:30:45 today"
    assert _replace("ratio 16:9 vs 4:3") == "ratio 16:9 vs 4:3"
    # No colons at all → returned as-is.
    assert _replace("plain text") == "plain text"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_known_shortcode_embedded_in_token_is_not_converted():
    # Regression: a KNOWN shortcode that happens to sit inside a longer run of
    # digits/letters is literal text, not an emoji. The classic trap is a numeric
    # range whose middle segment spells a real shortcode (`:100:` → 💯):
    assert _replace("1:100:2") == "1:100:2"
    assert _replace("scale 3:100:7 ok") == "scale 3:100:7 ok"
    # Glued to a word on either side → left alone (e.g. `key:value:` style text,
    # URL authorities like `host:fire:port`).
    assert _replace("host:fire:port") == "host:fire:port"
    assert _replace("status:fire:") == "status:fire:"
    assert _replace(":fire:done") == ":fire:done"
    # But a standalone shortcode flanked by whitespace/punctuation still converts,
    # including back-to-back shortcodes and the leading `:100:` once delimited.
    assert _replace("we hit :100: today") == "we hit \U0001f4af today"
    assert _replace("see :fire:!") == "see \U0001f525!"
    assert _replace(":fire::tada:") == "\U0001f525\U0001f389"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_has_emoji_shortcode_detector():
    js = f"""
    import {{ hasEmojiShortcode }} from '{_HELPER.as_posix()}';
    const out = [
      hasEmojiShortcode(':blush:'),
      hasEmojiShortcode('no shortcodes here'),
      hasEmojiShortcode('a single : colon'),
    ];
    console.log(JSON.stringify(out));
    """
    assert json.loads(_run(js)) == [True, False, False]
