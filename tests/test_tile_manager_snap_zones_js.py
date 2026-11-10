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
          getComputedStyle() {{ return {{ display: 'block' }}; }},
        }};
        let sidebarVisible = false;
        const sidebar = {{
          classList: {{ contains(name) {{ return name === 'hidden' ? !sidebarVisible : false; }} }},
          getBoundingClientRect() {{ return {{ left: 0, right: 240, width: 240, top: 0, bottom: 800 }}; }},
        }};
        globalThis.document = {{
          readyState: 'loading',
          body: {{ appendChild() {{}} }},
          documentElement: {{ style: {{ setProperty() {{}}, removeProperty() {{}} }} }},
          addEventListener() {{}},
          getElementById(id) {{ return id === 'sidebar' ? sidebar : null; }},
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

        const topWithoutSidebar = pick(mod._zoneForPointerForTests(500, 20));
        sidebarVisible = true;
        const topWithSidebar = pick(mod._zoneForPointerForTests(500, 20));
        sidebarVisible = false;

        console.log(JSON.stringify({{
          topEdge: pick(mod._zoneForPointerForTests(500, 0)),
          topStrip: pick(mod._zoneForPointerForTests(500, 8)),
          top: topWithoutSidebar,
          topWithSidebar,
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
def test_tile_manager_uses_one_sidebar_aware_maximize_zone_for_the_whole_top_edge():
    zones = _run_tile_case()

    maximize = {
        "name": "maximize",
        "rect": {"left": 4, "top": 4, "width": 1192, "height": 792},
    }
    assert zones["topEdge"] == maximize
    assert zones["topStrip"] == maximize
    assert zones["top"] == maximize
    assert zones["topWithSidebar"] == {
        "name": "maximize",
        "rect": {"left": 244, "top": 4, "width": 952, "height": 792},
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_tile_manager_detects_side_edges_but_ignores_the_bottom_edge():
    zones = _run_tile_case()

    assert zones["left"] == {
        "name": "left-half",
        "rect": {"left": 4, "top": 4, "width": 596, "height": 792},
    }
    assert zones["right"] == {
        "name": "right-half",
        "rect": {"left": 600, "top": 4, "width": 596, "height": 792},
    }
    assert zones["bottom"] is None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_regular_tool_modals_are_not_limited_to_fullscreen_only():
    zones = _run_tile_case()

    assert zones["memoryBottom"] is None
    assert zones["settingsTop"] is None
    assert zones["settingsRight"]["name"] == "right-half"
