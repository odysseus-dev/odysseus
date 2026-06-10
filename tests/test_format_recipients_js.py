"""Pin the pure recipient-formatting helper in emailLibrary/utils.js.

Driven through `node --input-type=module` so we exercise the real JS without a
full Vitest/Jest setup (same approach as test_reply_recipients_js.py). Skips
when `node` is not installed rather than failing.

Regression: _formatRecipients split the header on bare commas, so a quoted
display name containing a comma ("Doe, John" <john@x.com>) was cut in two,
inflating the count and pushing a real recipient past the "+N" cap so it was
dropped from the displayed list.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "emailLibrary" / "utils.js"
_HAS_NODE = shutil.which("node") is not None


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_split_keeps_comma_inside_quoted_display_name():
    js = f"""
    import {{ _splitRecipientList }} from '{_HELPER.as_posix()}';
    console.log(JSON.stringify(_splitRecipientList('"Doe, John" <john@x.com>, jane@y.com')));
    """
    assert json.loads(_run(js)) == ['"Doe, John" <john@x.com>', "jane@y.com"]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_format_does_not_drop_second_recipient_after_comma_name():
    js = f"""
    import {{ _formatRecipients }} from '{_HELPER.as_posix()}';
    console.log(JSON.stringify(_formatRecipients('"Doe, John" <john@x.com>, jane@y.com')));
    """
    # Two real recipients: the display name (with its legitimate comma) and jane.
    # Pre-fix this was "Doe, John +1" with jane lost.
    assert json.loads(_run(js)) == "Doe, John, jane"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_format_plain_list_still_caps_at_two():
    js = f"""
    import {{ _formatRecipients }} from '{_HELPER.as_posix()}';
    console.log(JSON.stringify(_formatRecipients('a@x.com, b@y.com, c@z.com')));
    """
    assert json.loads(_run(js)) == "a, b +1"
