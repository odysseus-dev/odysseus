"""Pin the RFC-3676 "-- " signature delimiter fold for self-closing breaks.

_foldSignature folded the standard "-- " sig delimiter only when the
surrounding line breaks were the literal `<br>`; the regex missed `<br/>`
and `<br />` (what Apple Mail and many clients emit), even though the very
next matcher in the same function already uses `<br\\s*/?>`. So a plain-text
signature delimiter with self-closing breaks was never folded.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MOD = _REPO / "static" / "js" / "emailLibrary" / "signatureFold.js"
_HAS_NODE = shutil.which("node") is not None


def _folds(html):
    js = f"""
    globalThis.document = {{ createElement: () => {{ let t=''; return {{ set textContent(v){{t=String(v);}}, get innerHTML(){{return t;}} }}; }} }};
    const mod = await import('{_MOD.as_posix()}');
    const html = {json.dumps(html)};
    const out = mod._foldSignature(html, null);
    console.log(JSON.stringify(out.includes('email-sig-fold')));
    """
    proc = subprocess.run(["node", "--input-type=module"], input=js,
                          capture_output=True, text=True, cwd=str(_REPO), timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


_SIG = "X" * 250  # long enough to be a "bloated" foldable signature


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_self_closing_br_delimiter_folds():
    assert _folds(f"Hello, please review.<br />-- <br />John Smith<br />Acme<br />{_SIG}") is True
    assert _folds(f"Hi.<br/>-- <br/>Jane Doe<br/>{_SIG}") is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_classic_br_delimiter_still_folds():
    assert _folds(f"Hello.<br>-- <br>John Smith<br>{_SIG}") is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_short_signature_is_not_folded():
    # not bloated -> wrap() returns the html unchanged (no fold)
    assert _folds("Hello.<br />-- <br />JS") is False
