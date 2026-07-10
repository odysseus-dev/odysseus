"""Behavioral coverage for the custom one-active-dock geometry contract."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MODAL_SNAP_URI = (_REPO / "static" / "js" / "modalSnap.js").as_uri()
_TILE_MANAGER_URI = (_REPO / "static" / "js" / "tileManager.js").as_uri()
_WINDOW_DRAG_URI = (_REPO / "static" / "js" / "windowDrag.js").as_uri()
_HAS_NODE = shutil.which("node") is not None


def _run_case(
    case_js: str,
    *,
    width: int = 1200,
    height: int = 800,
    touch_landscape: bool = False,
    scale: float = 1,
):
    script = textwrap.dedent(
        f"""
        const viewport = {{ width: {width}, height: {height} }};
        const touchLandscape = {json.dumps(touch_landscape)};
        const modalRegistry = [];
        const windowListeners = new Map();
        const documentListeners = new Map();

        const makeClassList = (initial = []) => {{
          const names = new Set(initial);
          return {{
            add(...values) {{ values.forEach((value) => names.add(value)); }},
            remove(...values) {{ values.forEach((value) => names.delete(value)); }},
            contains(value) {{ return names.has(value); }},
            toggle(value, force) {{
              const enabled = force === undefined ? !names.has(value) : !!force;
              if (enabled) names.add(value); else names.delete(value);
              return enabled;
            }},
          }};
        }};

        const makeStyle = () => {{
          const values = new Map();
          return {{
            setProperty(name, value) {{ values.set(name, String(value)); }},
            removeProperty(name) {{ values.delete(name); }},
            getPropertyValue(name) {{ return values.get(name) || ''; }},
            getPropertyPriority() {{ return ''; }},
          }};
        }};

        const numberValue = (value, fallback = 0) => {{
          const parsed = parseFloat(value || '');
          return Number.isFinite(parsed) ? parsed : fallback;
        }};

        const makeElement = (id = '') => {{
          const listeners = new Map();
          const el = {{
            id,
            style: makeStyle(),
            classList: makeClassList(),
            dataset: {{}},
            isConnected: true,
            parentNode: null,
            children: [],
            offsetWidth: 720,
            offsetHeight: 500,
            appendChild(child) {{ child.parentNode = this; this.children.push(child); }},
            addEventListener(type, fn) {{
              const handlers = listeners.get(type) || [];
              handlers.push(fn);
              listeners.set(type, handlers);
            }},
            removeEventListener(type, fn) {{
              const handlers = listeners.get(type) || [];
              listeners.set(type, handlers.filter((handler) => handler !== fn));
            }},
            dispatchEvent(event) {{
              if (!event.target) event.target = this;
              for (const fn of listeners.get(event.type) || []) fn(event);
            }},
            querySelector() {{ return null; }},
            closest() {{ return null; }},
            contains(other) {{ return this.children.includes(other); }},
            getBoundingClientRect() {{
              const boxWidth = numberValue(this.style.width, this.offsetWidth || 0);
              const boxHeight = numberValue(this.style.height, this.offsetHeight || 0);
              const rightInset = numberValue(this.style.right, 0);
              const bottomInset = numberValue(this.style.bottom, 0);
              const left = this.style.left === 'auto'
                ? viewport.width - rightInset - boxWidth
                : numberValue(this.style.left, 0);
              const top = this.style.top === 'auto'
                ? viewport.height - bottomInset - boxHeight
                : numberValue(this.style.top, 0);
              return {{
                left,
                top,
                right: left + boxWidth,
                bottom: top + boxHeight,
                width: boxWidth,
                height: boxHeight,
              }};
            }},
            remove() {{ this.isConnected = false; }},
          }};
          return el;
        }};

        const root = makeElement('root');
        root.style.setProperty('--ui-scale-factor', {json.dumps(scale)});
        const body = makeElement('body');
        const sidebar = makeElement('sidebar');
        sidebar.classList.add('hidden');
        sidebar.getBoundingClientRect = () => ({{
          left: 0, top: 0, right: 0, bottom: viewport.height, width: 0, height: viewport.height,
        }});
        const rail = makeElement('icon-rail');
        rail.offsetWidth = 48;
        rail.getBoundingClientRect = () => ({{
          left: 0, top: 0, right: 48, bottom: viewport.height, width: 48, height: viewport.height,
        }});
        body.appendChild(sidebar);
        body.appendChild(rail);

        const makeModal = (id) => {{
          const modal = makeElement(id);
          modal.classList.add('modal');
          const content = makeElement(id + '-content');
          content.classList.add('modal-content');
          modal.children.push(content);
          content.parentNode = modal;
          content.closest = () => modal;
          modal.parentNode = body;
          modal.querySelector = (selector) => selector.includes('modal-content') ? content : null;
          modal.contains = (other) => other === content;
          modalRegistry.push(modal);
          return {{ modal, content }};
        }};

        const dockedForSelector = (selector) => {{
          const classes = [...selector.matchAll(/\\.([a-z]+-(?:left|right|top|bottom)-docked)/g)]
            .map((match) => match[1]);
          if (!classes.length) return null;
          return modalRegistry.filter((modal) => classes.some((name) => modal.classList.contains(name)));
        }};

        globalThis.window = {{
          innerWidth: viewport.width,
          innerHeight: viewport.height,
          addEventListener(type, fn) {{
            const handlers = windowListeners.get(type) || [];
            handlers.push(fn);
            windowListeners.set(type, handlers);
          }},
          removeEventListener(type, fn) {{
            const handlers = windowListeners.get(type) || [];
            windowListeners.set(type, handlers.filter((handler) => handler !== fn));
          }},
          dispatchEvent(event) {{
            for (const fn of windowListeners.get(event.type) || []) fn(event);
          }},
          matchMedia(query) {{
            if (query.includes('orientation: landscape')) return {{ matches: touchLandscape }};
            if (query.includes('pointer: coarse') || query.includes('hover: none')) {{
              return {{ matches: touchLandscape }};
            }}
            if (query.includes('pointer: fine') || query.includes('any-pointer: fine')) {{
              return {{ matches: !touchLandscape }};
            }}
            return {{ matches: false }};
          }},
          getComputedStyle(el) {{
            return {{
              display: el?.style?.display || 'block',
              visibility: el?.style?.visibility || 'visible',
              opacity: '1',
              zIndex: '250',
              width: el === rail ? '48px' : (el?.style?.width || '0px'),
              getPropertyValue(name) {{ return el?.style?.getPropertyValue?.(name) || ''; }},
            }};
          }},
        }};
        if (touchLandscape) window.ontouchstart = null;
        globalThis.document = {{
          readyState: 'complete',
          body,
          documentElement: root,
          addEventListener(type, fn) {{
            const handlers = documentListeners.get(type) || [];
            handlers.push(fn);
            documentListeners.set(type, handlers);
          }},
          removeEventListener(type, fn) {{
            const handlers = documentListeners.get(type) || [];
            documentListeners.set(type, handlers.filter((handler) => handler !== fn));
          }},
          dispatchEvent(event) {{
            for (const fn of [...(documentListeners.get(event.type) || [])]) fn(event);
          }},
          getElementById(id) {{
            if (id === 'sidebar') return sidebar;
            if (id === 'icon-rail') return rail;
            return modalRegistry.find((modal) => modal.id === id) || null;
          }},
          querySelector(selector) {{
            if (selector === '.icon-rail' || selector === '#icon-rail') return rail;
            return null;
          }},
          querySelectorAll(selector) {{
            const docked = dockedForSelector(selector);
            if (docked) return docked;
            if (selector.startsWith('.modal:not(')) {{
              return modalRegistry.filter((modal) => !modal.classList.contains('hidden')
                && !modal.classList.contains('modal-minimized'));
            }}
            return [];
          }},
          createElement() {{ return makeElement(); }},
        }};
        globalThis.getComputedStyle = globalThis.window.getComputedStyle;
        globalThis.requestAnimationFrame = (fn) => {{ fn(); return 1; }};
        globalThis.cancelAnimationFrame = () => {{}};
        globalThis.MutationObserver = class {{ observe() {{}} disconnect() {{}} }};
        globalThis.ResizeObserver = class {{ observe() {{}} disconnect() {{}} }};
        globalThis.CustomEvent = class {{
          constructor(type, options) {{ this.type = type; this.detail = options?.detail; }}
        }};
        globalThis.localStorage = {{
          values: new Map(),
          getItem(key) {{ return this.values.has(key) ? this.values.get(key) : null; }},
          setItem(key, value) {{ this.values.set(key, String(value)); }},
          removeItem(key) {{ this.values.delete(key); }},
        }};
        Object.defineProperty(globalThis, 'navigator', {{
          value: {{ maxTouchPoints: touchLandscape ? 1 : 0 }},
          configurable: true,
        }});
        globalThis.screen = {{ orientation: null }};

        const snap = await import({json.dumps(_MODAL_SNAP_URI)});
        {textwrap.indent(textwrap.dedent(case_js).strip(), '        ')}
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
def test_available_desktop_dock_preview_matches_commit_and_preserves_chat_floor():
    result = _run_case(
        """
        const target = makeModal('target-modal');
        const preview = snap.edgeDockPreviewRect(target.modal, 'right');
        const applied = snap.applyEdgeDock(target.modal, 'right');
        const committed = target.content.getBoundingClientRect();
        console.log(JSON.stringify({
          preview,
          applied,
          committed,
          remaining: 1200 - 48 - applied,
        }));
        """
    )

    assert result["preview"]["width"] == result["applied"]
    assert result["preview"]["left"] == result["committed"]["left"]
    assert result["committed"]["width"] == result["applied"]
    assert result["remaining"] >= 380


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_new_desktop_dock_is_rejected_without_chat_floor_and_keeps_active_dock():
    result = _run_case(
        """
        const existing = makeModal('existing-modal');
        const existingSize = snap.applyEdgeDock(existing.modal, 'left');
        viewport.width = 780;
        window.innerWidth = 780;
        const target = makeModal('target-modal');
        const preview = snap.edgeDockPreviewRect(target.modal, 'right');
        const controller = snap.makeEdgeDockController(target.modal, 'right');
        const near = controller.near(779, 300);
        controller.onMove(779, 300);
        const applied = snap.applyEdgeDock(target.modal, 'right');
        console.log(JSON.stringify({
          existingSize,
          preview,
          near,
          hovering: controller.hovering(),
          applied,
          existingStillDocked: existing.modal.classList.contains('modal-left-docked'),
          targetDocked: target.modal.classList.contains('modal-right-docked'),
        }));
        """
    )

    assert result["existingSize"] > 0
    assert result["preview"] is None
    assert result["near"] is False
    assert result["hovering"] is False
    assert result["applied"] == 0
    assert result["existingStillDocked"] is True
    assert result["targetDocked"] is False


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_new_vertical_dock_is_rejected_when_chat_height_cannot_fit():
    result = _run_case(
        """
        const target = makeModal('target-modal');
        const preview = snap.edgeDockPreviewRect(target.modal, 'top');
        const controller = snap.makeEdgeDockController(target.modal, 'top');
        const near = controller.near(600, 1);
        controller.onMove(600, 1);
        const applied = snap.applyEdgeDock(target.modal, 'top');
        console.log(JSON.stringify({
          preview,
          near,
          hovering: controller.hovering(),
          applied,
          active: body.classList.contains('top-dock-active'),
        }));
        """,
        height=400,
    )

    assert result == {
        "preview": None,
        "near": False,
        "hovering": False,
        "applied": 0,
        "active": False,
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_active_vertical_dock_compacts_after_viewport_shrink():
    result = _run_case(
        """
        const target = makeModal('target-modal');
        const before = snap.applyEdgeDock(target.modal, 'top');
        viewport.height = 400;
        window.innerHeight = 400;
        window.dispatchEvent({ type: 'resize' });
        const after = parseFloat(target.content.style.height);
        console.log(JSON.stringify({ before, after, remaining: 400 - after }));
        """
    )

    assert result["before"] == 400
    assert 0 < result["after"] < result["before"]
    assert result["remaining"] >= 260


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_touch_landscape_keeps_compact_side_docking():
    result = _run_case(
        """
        const target = makeModal('target-modal');
        const preview = snap.edgeDockPreviewRect(target.modal, 'right');
        const applied = snap.applyEdgeDock(target.modal, 'right');
        console.log(JSON.stringify({ preview, applied }));
        """,
        width=700,
        height=400,
        touch_landscape=True,
    )

    assert result["preview"] is not None
    assert result["applied"] == result["preview"]["width"]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_failed_reserved_tile_snap_does_not_fall_back_to_half_tile():
    result = _run_case(
        f"""
        const target = makeModal('target-modal');
        const tile = await import({json.dumps(_TILE_MANAGER_URI)});
        const preview = tile.previewZoneAt(779, 300, target.modal);
        tile.snapModalToZone(target.modal, {{
          name: 'right-half',
          rect: {{ left: 390, top: 4, width: 386, height: 792 }},
        }});
        console.log(JSON.stringify({{
          preview,
          docked: target.modal.classList.contains('modal-right-docked'),
          tileZone: target.content.dataset._tileZone || null,
        }}));
        """,
        width=780,
    )

    assert result == {"preview": None, "docked": False, "tileZone": None}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_pointerup_in_maximize_strip_clears_stale_top_dock_hover():
    result = _run_case(
        f"""
        const target = makeModal('target-modal');
        const header = makeElement('target-header');
        let dragEnds = 0;
        const drag = await import({json.dumps(_WINDOW_DRAG_URI)});
        drag.makeWindowDraggable(target.modal, {{
          content: target.content,
          header,
          enableResize: false,
          onDragEnd() {{ dragEnds += 1; }},
        }});
        const mouseEvent = (type, x, y) => ({{
          type,
          clientX: x,
          clientY: y,
          button: 0,
          target: header,
          preventDefault() {{}},
          stopPropagation() {{}},
        }});
        header.dispatchEvent(mouseEvent('mousedown', 600, 260));
        // Arm the top dock one pixel below tileManager's reserved strip, then
        // release inside maximize without an intervening mousemove.
        document.dispatchEvent(mouseEvent('mousemove', 600, 13));
        document.dispatchEvent(mouseEvent('mouseup', 600, 8));
        console.log(JSON.stringify({{
          topDocked: target.modal.classList.contains('modal-top-docked'),
          dragEnds,
        }}));
        """
    )

    assert result == {"topDocked": False, "dragEnds": 1}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("scale", [0.9, 1.25, 1.4])
def test_subthreshold_drag_jitter_uses_layout_coordinates_at_ui_scale(scale):
    result = _run_case(
        f"""
        const target = makeModal('target-modal');
        const header = makeElement('target-header');
        const drag = await import({json.dumps(_WINDOW_DRAG_URI)});
        drag.makeWindowDraggable(target.modal, {{
          content: target.content,
          header,
          enableResize: false,
          enableDock: false,
        }});
        const mouseEvent = (type, x, y) => ({{
          type,
          clientX: x,
          clientY: y,
          button: 0,
          target: header,
          prevented: false,
          stopped: false,
          preventDefault() {{ this.prevented = true; }},
          stopPropagation() {{ this.stopped = true; }},
        }});
        header.dispatchEvent(mouseEvent('mousedown', 600, 260));
        document.dispatchEvent(mouseEvent('mousemove', 601, 260));
        document.dispatchEvent(mouseEvent('mouseup', 601, 260));
        const click = mouseEvent('click', 601, 260);
        header.dispatchEvent(click);
        console.log(JSON.stringify({{
          prevented: click.prevented,
          stopped: click.stopped,
        }}));
        """,
        scale=scale,
    )

    assert result == {"prevented": False, "stopped": False}
