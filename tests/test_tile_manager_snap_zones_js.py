"""Regression coverage for desktop modal tile snap edge zones."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "tileManager.js"
_HELPER_URI = _HELPER.as_uri()
_HAS_NODE = shutil.which("node") is not None


def _run_tile_case():
    script = textwrap.dedent(
        f"""
        const makeClassList = () => {{
          const names = new Set();
          return {{
            add(...values) {{ values.forEach((value) => names.add(value)); }},
            remove(...values) {{ values.forEach((value) => names.delete(value)); }},
            contains(value) {{ return names.has(value); }},
          }};
        }};
        const makeStyle = () => {{
          const values = new Map();
          return {{
            setProperty(name, value) {{ values.set(name, String(value)); }},
            removeProperty(name) {{ values.delete(name); }},
            getPropertyValue(name) {{ return values.get(name) || ''; }},
          }};
        }};
        const makeElement = () => ({{
          style: makeStyle(),
          classList: makeClassList(),
          dataset: {{}},
          isConnected: true,
          appendChild() {{}},
          addEventListener() {{}},
          removeEventListener() {{}},
          querySelector() {{ return null; }},
          contains() {{ return false; }},
          getBoundingClientRect() {{
            return {{ left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }};
          }},
          remove() {{}},
        }});
        const body = makeElement();
        const documentElement = makeElement();
        globalThis.window = {{
          innerWidth: 1200,
          innerHeight: 800,
          addEventListener() {{}},
          removeEventListener() {{}},
          dispatchEvent() {{}},
          matchMedia(query) {{
            return {{ matches: query.includes('pointer: fine') || query.includes('any-pointer: fine') }};
          }},
          getComputedStyle() {{
            return {{
              display: 'block',
              visibility: 'visible',
              opacity: '1',
              zIndex: '0',
              getPropertyValue() {{ return ''; }},
            }};
          }},
        }};
        globalThis.document = {{
          readyState: 'loading',
          body,
          documentElement,
          addEventListener() {{}},
          removeEventListener() {{}},
          getElementById() {{ return null; }},
          querySelector() {{ return null; }},
          querySelectorAll() {{ return []; }},
          createElement() {{ return makeElement(); }},
        }};
        Object.defineProperty(globalThis, 'navigator', {{
          value: {{ maxTouchPoints: 0 }},
          configurable: true,
        }});
        globalThis.screen = {{ orientation: null }};
        globalThis.getComputedStyle = globalThis.window.getComputedStyle;
        globalThis.requestAnimationFrame = (fn) => fn();
        globalThis.MutationObserver = class {{
          observe() {{}}
          disconnect() {{}}
        }};

        const mod = await import({json.dumps(_HELPER_URI)});
        const pick = (zone) => zone ? {{
          name: zone.name,
          rect: {{
            left: zone.rect.left,
            top: zone.rect.top,
            width: zone.rect.width,
            height: zone.rect.height,
          }},
        }} : null;

        const memoryModal = {{ id: 'memory-modal', dataset: {{}} }};
        const memoryContent = {{ closest() {{ return memoryModal; }} }};
        const settingsModal = {{ id: 'settings-modal', dataset: {{}} }};
        const settingsContent = {{ closest() {{ return settingsModal; }} }};
        const sharedModal = {{ id: 'shared-modal', dataset: {{ edgeDockController: '1' }} }};
        const sharedContent = {{ closest() {{ return sharedModal; }} }};

        console.log(JSON.stringify({{
          fullscreen: pick(mod._zoneForPointerForTests(500, 0)),
          maximize: pick(mod._zoneForPointerForTests(500, 8)),
          top: pick(mod._zoneForPointerForTests(500, 20)),
          left: pick(mod._zoneForPointerForTests(20, 300)),
          right: pick(mod._zoneForPointerForTests(1190, 300)),
          bottom: pick(mod._zoneForPointerForTests(500, 790)),
          memoryTop: pick(mod._zoneForContentForTests(memoryContent, 500, 20)),
          memoryBottom: pick(mod._zoneForContentForTests(memoryContent, 500, 790)),
          settingsTop: pick(mod._zoneForContentForTests(settingsContent, 500, 20)),
          settingsLeft: pick(mod._zoneForContentForTests(settingsContent, 20, 300)),
          settingsRight: pick(mod._zoneForContentForTests(settingsContent, 1190, 300)),
          settingsBottom: pick(mod._zoneForContentForTests(settingsContent, 500, 790)),
          sharedFullscreen: pick(mod._zoneForContentForTests(sharedContent, 500, 0)),
          sharedMaximize: pick(mod._zoneForContentForTests(sharedContent, 500, 8)),
          sharedTop: pick(mod._zoneForContentForTests(sharedContent, 500, 20)),
          sharedLeft: pick(mod._zoneForContentForTests(sharedContent, 20, 300)),
          sharedRight: pick(mod._zoneForContentForTests(sharedContent, 1190, 300)),
          sharedBottom: pick(mod._zoneForContentForTests(sharedContent, 500, 790)),
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

    assert zones["memoryTop"]["name"] == "top-half"
    assert zones["memoryBottom"]["name"] == "bottom-half"
    assert zones["settingsTop"]["name"] == "top-half"
    assert zones["settingsLeft"] is None
    assert zones["settingsRight"]["name"] == "right-half"
    assert zones["settingsBottom"]["name"] == "bottom-half"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_shared_edge_controller_preserves_fullscreen_and_maximize_zones():
    zones = _run_tile_case()

    assert zones["sharedFullscreen"]["name"] == "fullscreen"
    assert zones["sharedMaximize"]["name"] == "maximize"
    assert zones["sharedTop"] is None
    assert zones["sharedLeft"] is None
    assert zones["sharedRight"] is None
    assert zones["sharedBottom"] is None
