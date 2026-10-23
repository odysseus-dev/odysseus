"""Gallery's capture-phase Escape handler must leave the key to a layer on top.

It used to close the Gallery on any Escape, so with the Ctrl+K search palette
(or another tool window) stacked above it, one press closed both the palette
and the Gallery underneath. `_galleryOwnsEscape()` gates the detail/close
branches on the Gallery actually being the top layer.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GALLERY = ROOT / "static" / "js" / "gallery.js"
HELPER = ROOT / "static" / "js" / "toolWindowZOrder.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _owns_escape_source() -> str:
    src = GALLERY.read_text()
    start = src.index("function _galleryOwnsEscape()")
    return src[start:src.index("\n}\n", start) + 2]


def test_gallery_owns_escape_only_when_it_is_the_top_layer():
    script = textwrap.dedent(
        f"""
        import {{ topToolWindowZ }} from '{HELPER.as_uri()}';
        const cls = (...names) => ({{ contains: (name) => names.includes(name) }});
        function owns({{ searchOpen = false, galleryZ = '1001', otherZ = null }} = {{}}) {{
          const gallery = {{ id: 'gallery-modal', classList: cls(), style: {{ zIndex: galleryZ }} }};
          const others = otherZ === null ? [] : [{{ id: 'memory-modal', classList: cls(), style: {{ zIndex: String(otherZ) }} }}];
          const search = {{ classList: cls(...(searchOpen ? [] : ['hidden'])) }};
          globalThis.document = {{
            getElementById: (id) => ({{ 'search-overlay': search, 'gallery-modal': gallery }})[id] || null,
            querySelectorAll: () => [gallery, ...others],
          }};
          globalThis.getComputedStyle = (el) => el.style;
          return _galleryOwnsEscape();
        }}
        {_owns_escape_source()}
        console.log(JSON.stringify({{
          alone: owns(),
          searchOpen: owns({{ searchOpen: true }}),
          windowAbove: owns({{ otherZ: 1002 }}),
          windowBelow: owns({{ otherZ: 900 }}),
          unknownZ: owns({{ galleryZ: 'auto', otherZ: 1002 }}),
        }}));
        """
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {
        "alone": True,
        "searchOpen": False,
        "windowAbove": False,
        "windowBelow": True,
        "unknownZ": True,
    }


def test_escape_handler_checks_ownership_before_detail_and_close():
    src = GALLERY.read_text()
    handler = src[src.index("_escHandler = (e) => {"):src.index("document.addEventListener('keydown', _escHandler, true);")]
    guard = handler.index("if (!_galleryOwnsEscape()) return;")
    # The editor keeps first claim on Escape (its canvas-size prompt is its own
    # .modal above the Gallery); only Gallery navigation/close is gated.
    assert handler.index("window.__galleryEditorHandleEscape") < guard
    assert guard < handler.index("gallery-detail-back") < handler.index("closeGallery();")
