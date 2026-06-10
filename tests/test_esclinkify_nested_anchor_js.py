"""Regression: _escLinkify must not nest <a> tags for URLs containing an email.

The old code linkified URLs, then ran the email regex over the already-linkified
HTML. A URL like ".../c?e=foo@bar.com" had its email substring matched again,
inside the href and the link text, producing invalid nested <a> tags and a
corrupted link. URLs and emails are now matched in a single pass.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "emailLibrary" / "utils.js"
_HAS_NODE = shutil.which("node") is not None

# Minimal `document` stub so the module's _esc (textContent -> innerHTML) works
# under node, escaping &, <, > exactly like the browser does.
_DOC_STUB = (
    "globalThis.document={createElement:()=>{let t='';return{"
    "set textContent(v){t=String(v==null?'':v)},get textContent(){return t},"
    "get innerHTML(){return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}};}};"
)


def _link(text):
    js = (
        _DOC_STUB
        + f"const {{ _escLinkify }} = await import('{_HELPER.as_posix()}');"
        + f"console.log(JSON.stringify(_escLinkify({json.dumps(text)})));"
    )
    proc = subprocess.run(["node", "--input-type=module"], input=js,
                          capture_output=True, text=True, cwd=str(_REPO), timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_url_with_email_does_not_nest_anchors():
    out = _link("Contact via https://site.com/c?e=foo@bar.com please")
    assert out.count("<a ") == 1            # the URL only, one anchor
    assert re.search(r"<a\b[^>]*<a\b", out) is None  # no <a opened inside another <a tag
    assert 'href="https://site.com/c?e=foo@bar.com"' in out
    assert "mailto:" not in out             # the email is part of the URL, not its own link


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_standalone_email_and_url_both_linkified():
    out = _link("see https://x.com and mail bob@y.com")
    assert out.count("<a ") == 2
    assert 'href="https://x.com"' in out
    assert 'href="mailto:bob@y.com"' in out
