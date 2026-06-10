"""Regression coverage for the Ctrl+K search overlay stacking above panels."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "searchStacking.js"
_STYLE = (_REPO / "static" / "style.css").read_text(encoding="utf-8")
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _run(js: str):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_search_overlay_css_fallback_sits_above_modal_dock():
    block = re.search(r"\.search-overlay\s*\{(?P<body>[\s\S]*?)\n\s*\}", _STYLE)
    assert block, "search overlay CSS block not found"
    z = re.search(r"z-index:\s*(\d+)", block.group("body"))

    assert z and int(z.group(1)) >= 10050


def test_search_overlay_zindex_uses_minimum_without_panels():
    js = f"""
    import {{ nextSearchOverlayZIndex }} from '{_HELPER.as_uri()}';
    const doc = {{
      defaultView: {{ getComputedStyle: () => ({{ display: 'block', visibility: 'visible', zIndex: 'auto' }}) }},
      querySelectorAll: () => [],
    }};
    console.log(JSON.stringify(nextSearchOverlayZIndex(doc)));
    """

    assert _run(js) == 10050


def test_search_overlay_zindex_clears_current_top_panel():
    js = f"""
    import {{ nextSearchOverlayZIndex }} from '{_HELPER.as_uri()}';
    const visible = (z) => ({{
      classList: {{ contains: () => false }},
      z,
    }});
    const doc = {{
      defaultView: {{
        getComputedStyle: (node) => ({{
          display: 'block',
          visibility: 'visible',
          zIndex: String(node.z),
        }}),
      }},
      querySelectorAll: () => [visible(260), visible(10073), visible(10012)],
    }};
    console.log(JSON.stringify(nextSearchOverlayZIndex(doc)));
    """

    assert _run(js) == 10074


def test_search_overlay_zindex_ignores_hidden_or_minimized_panels():
    js = f"""
    import {{ nextSearchOverlayZIndex }} from '{_HELPER.as_uri()}';
    const panel = (z, hiddenClass, display) => ({{
      classList: {{ contains: (name) => name === hiddenClass }},
      z,
      display,
    }});
    const doc = {{
      defaultView: {{
        getComputedStyle: (node) => ({{
          display: node.display || 'block',
          visibility: 'visible',
          zIndex: String(node.z),
        }}),
      }},
      querySelectorAll: () => [
        panel(12000, 'hidden'),
        panel(11000, 'modal-minimized'),
        panel(10500, null, 'none'),
        panel(10080, null),
      ],
    }};
    console.log(JSON.stringify(nextSearchOverlayZIndex(doc)));
    """

    assert _run(js) == 10081
