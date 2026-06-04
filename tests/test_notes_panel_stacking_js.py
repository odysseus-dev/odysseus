"""Regression coverage for Notes panel click-to-front stacking."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "notesStacking.js"
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


def test_notes_panel_zindex_uses_minimum_without_modals():
    js = f"""
    import {{ nextNotesPaneZIndex }} from '{_HELPER.as_uri()}';
    const doc = {{
      defaultView: {{ getComputedStyle: () => ({{ display: 'block', visibility: 'visible', zIndex: 'auto' }}) }},
      querySelectorAll: () => [],
    }};
    console.log(JSON.stringify(nextNotesPaneZIndex(doc)));
    """

    assert _run(js) == 1000


def test_notes_panel_zindex_clears_current_top_modal():
    js = f"""
    import {{ nextNotesPaneZIndex }} from '{_HELPER.as_uri()}';
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
      querySelectorAll: () => [visible(260), visible(1007), visible(300)],
    }};
    console.log(JSON.stringify(nextNotesPaneZIndex(doc)));
    """

    assert _run(js) == 1008


def test_notes_panel_zindex_ignores_hidden_or_minimized_windows():
    js = f"""
    import {{ nextNotesPaneZIndex }} from '{_HELPER.as_uri()}';
    const windowNode = (z, hiddenClass, display) => ({{
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
        windowNode(5000, 'hidden'),
        windowNode(4000, 'modal-minimized'),
        windowNode(3000, null, 'none'),
        windowNode(1200, null),
      ],
    }};
    console.log(JSON.stringify(nextNotesPaneZIndex(doc)));
    """

    assert _run(js) == 1201
