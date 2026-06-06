"""Pin langIcon (static/js/langIcons.js) against an explicit null opts.
Driven through `node --input-type=module`; skips without node.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "langIcons.js"
_HAS_NODE = shutil.which("node") is not None


def _icon(lang, size, opts):
    js = f"""
    import {{ langIcon }} from '{_HELPER.as_posix()}';
    console.log(langIcon({json.dumps(lang)}, {json.dumps(size)}, {json.dumps(opts)}));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_lang_icon_tolerates_null_opts():
    # `opts = {}` default only applies when the arg is omitted; an explicit
    # null (easy to pass) hit opts.className and threw a TypeError.
    out = _icon("python", 14, None)
    assert out.startswith("<svg")
    assert "class=" not in out


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_lang_icon_applies_opts_when_given():
    assert 'class="ic"' in _icon("python", 14, {"className": "ic"})
