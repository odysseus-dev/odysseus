"""Regression coverage for desktop modal tile snap edge zones."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "tileManager.js"
_HAS_NODE = shutil.which("node") is not None


def _run_tile_case():
    script = textwrap.dedent(
        f"""
        globalThis.window = {{
          innerWidth: 1200,
          innerHeight: 800,
          addEventListener() {{}},
        }};
        globalThis.document = {{
          readyState: 'loading',
          body: {{ appendChild() {{}} }},
          documentElement: {{ style: {{ setProperty() {{}}, removeProperty() {{}} }} }},
          addEventListener() {{}},
          getElementById() {{ return null; }},
          querySelector() {{ return null; }},
          querySelectorAll() {{ return []; }},
          createElement() {{
            return {{
              style: {{}},
              classList: {{ add() {{}}, remove() {{}} }},
              remove() {{}},
            }};
          }},
        }};
        globalThis.requestAnimationFrame = (fn) => fn();
        globalThis.MutationObserver = class {{
          observe() {{}}
          disconnect() {{}}
        }};

        const mod = await import('{_HELPER.as_posix()}');
        const pick = (zone) => zone ? {{
          name: zone.name,
          rect: {{
            left: zone.rect.left,
            top: zone.rect.top,
            width: zone.rect.width,
            height: zone.rect.height,
          }},
        }} : null;

        const memoryModal = {{ id: 'memory-modal' }};
        const memoryContent = {{ closest() {{ return memoryModal; }} }};
        const settingsModal = {{ id: 'settings-modal' }};
        const settingsContent = {{ closest() {{ return settingsModal; }} }};

        console.log(JSON.stringify({{
          fullscreen: pick(mod._zoneForPointerForTests(500, 0)),
          maximize: pick(mod._zoneForPointerForTests(500, 8)),
          top: pick(mod._zoneForPointerForTests(500, 20)),
          left: pick(mod._zoneForPointerForTests(20, 300)),
          right: pick(mod._zoneForPointerForTests(1190, 300)),
          bottom: pick(mod._zoneForPointerForTests(500, 790)),
          memoryBottom: pick(mod._zoneForContentForTests(memoryContent, 500, 790)),
          settingsTop: pick(mod._zoneForContentForTests(settingsContent, 500, 20)),
          settingsRight: pick(mod._zoneForContentForTests(settingsContent, 1190, 300)),
        }}));
        """
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_tile_manager_detects_all_four_workspace_edges():
    zones = _run_tile_case()

    assert zones["fullscreen"]["name"] == "fullscreen"
    assert zones["maximize"]["name"] == "maximize"
    assert zones["top"] == {
        "name": "top-half",
        "rect": {"left": 4, "top": 4, "width": 1192, "height": 396},
    }
    assert zones["left"] == {
        "name": "left-half",
        "rect": {"left": 4, "top": 4, "width": 596, "height": 792},
    }
    assert zones["right"] == {
        "name": "right-half",
        "rect": {"left": 600, "top": 4, "width": 596, "height": 792},
    }
    assert zones["bottom"] == {
        "name": "bottom-half",
        "rect": {"left": 4, "top": 400, "width": 1192, "height": 396},
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_regular_tool_modals_are_not_limited_to_fullscreen_only():
    zones = _run_tile_case()

    assert zones["memoryBottom"]["name"] == "bottom-half"
    assert zones["settingsTop"] is None
    assert zones["settingsRight"]["name"] == "right-half"
