"""Pin the pure emoji-shortcode helper in emoji/shortcodes.js.

Driven through `node --input-type=module` (same approach as test_compare_js.py);
skips when `node` is not installed rather than failing.

Regression for issue #345: `:blush:` style shortcodes emitted by models were
shown as literal text instead of the Unicode emoji.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "emoji" / "shortcodes.js"
_HAS_NODE = shutil.which("node") is not None


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _call(html: str) -> str:
    js = f"""
    import {{ replaceEmojiShortcodes }} from '{_HELPER.as_posix()}';
    console.log(JSON.stringify(replaceEmojiShortcodes({json.dumps(html)})));
    """
    return json.loads(_run(js))


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_known_shortcodes_become_unicode():
    assert _call("nice work :rocket: :blush:") == "nice work 🚀 😊"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_unknown_shortcode_left_untouched():
    assert _call("ping :not_an_emoji: pong") == "ping :not_an_emoji: pong"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_shortcodes_inside_code_are_preserved():
    html = "say <code>:rocket:</code> then :rocket:"
    assert _call(html) == "say <code>:rocket:</code> then 🚀"
