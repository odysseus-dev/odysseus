"""Pin the pure reply-all recipient helpers in emailLibrary/replyRecipients.js.

Driven through `node --input-type=module` so we exercise the real JS without a
full Vitest/Jest setup (same approach as test_compare_js.py). Skips when `node`
is not installed rather than failing.

Regression for issue #360: reply-all dropped every Cc recipient when the user's
own address was unknown, because the old filter used `includes("")` (always
true) instead of an exact-email comparison.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "emailLibrary" / "replyRecipients.js"
_HAS_NODE = shutil.which("node") is not None


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, encoding="utf-8", cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_reply_all_keeps_cc_when_self_unknown():
    data = {"to": "Alice <alice@x.com>, bob@x.com", "cc": "Carol <carol@x.com>"}
    js = f"""
    import {{ buildReplyAllCc }} from '{_HELPER.as_uri()}';
    console.log(JSON.stringify(buildReplyAllCc({json.dumps(data)}, '')));
    """
    cc = json.loads(_run(js))
    # Empty self address must NOT wipe everyone (the #360 bug).
    assert cc == "Alice <alice@x.com>, bob@x.com, Carol <carol@x.com>"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_reply_all_excludes_only_self_exactly():
    data = {"to": "Me <me@x.com>, Alice <alice@x.com>", "cc": "bob@x.com"}
    js = f"""
    import {{ buildReplyAllCc }} from '{_HELPER.as_uri()}';
    console.log(JSON.stringify(buildReplyAllCc({json.dumps(data)}, 'me@x.com')));
    """
    cc = json.loads(_run(js))
    # Our own address is dropped; a substring-similar address is kept.
    assert cc == "Alice <alice@x.com>, bob@x.com"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_reply_all_excludes_all_of_my_addresses():
    # Multi-account user: every one of their own addresses must be excluded,
    # not just the active one.
    data = {"to": "Alice <alice@x.com>, me@work.com", "cc": "me@personal.com, bob@x.com"}
    js = f"""
    import {{ buildReplyAllCc }} from '{_HELPER.as_uri()}';
    console.log(JSON.stringify(buildReplyAllCc({json.dumps(data)}, ["me@work.com", "me@personal.com"])));
    """
    cc = json.loads(_run(js))
    assert cc == "Alice <alice@x.com>, bob@x.com"
