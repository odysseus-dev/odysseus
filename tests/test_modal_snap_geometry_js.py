"""Behavioral regression coverage for reserved modal dock geometry."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MODAL_SNAP_URI = (_REPO / "static" / "js" / "modalSnap.js").as_uri()
_TILE_MANAGER_URI = (_REPO / "static" / "js" / "tileManager.js").as_uri()
_HAS_NODE = shutil.which("node") is not None


def _run_case(
    case_js: str,
    *,
    width: int = 1200,
    height: int = 800,
    rail_right: bool = False,
    scale: float = 1,
    sidebar_visible: bool = False,
):
    script = textwrap.dedent(
        f"""
        const viewport = {{ width: {width}, height: {height}, scale: {scale} }};
        const railOnRight = {json.dumps(rail_right)};
        const sidebarVisible = {json.dumps(sidebar_visible)};
        const modalRegistry = [];
        const appended = [];
        const windowListeners = new Map();

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
            values() {{ return [...names]; }},
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
            appendChild(child) {{ child.parentNode = this; this.children.push(child); appended.push(child); }},
            addEventListener() {{}},
            removeEventListener() {{}},
            querySelector() {{ return null; }},
            contains(other) {{ return this.children.includes(other); }},
            getBoundingClientRect() {{
              const width = numberValue(this.style.width, this.offsetWidth || 0);
              const height = numberValue(this.style.height, this.offsetHeight || 0);
              const rightInset = numberValue(this.style.right, 0);
              const bottomInset = numberValue(this.style.bottom, 0);
              const left = this.style.left === 'auto'
                ? viewport.width - rightInset - width
                : numberValue(this.style.left, 0);
              const top = this.style.top === 'auto'
                ? viewport.height - bottomInset - height
                : numberValue(this.style.top, 0);
              return {{ left, top, right: left + width, bottom: top + height, width, height }};
            }},
            remove() {{ this.isConnected = false; }},
          }};
          return el;
        }};

        const root = makeElement('root');
        root.style.setProperty('--ui-scale-factor', String(viewport.scale));
        const body = makeElement('body');
        body.dataset = {{}};
        const sidebar = makeElement('sidebar');
        if (!sidebarVisible) sidebar.classList.add('hidden');
        sidebar.offsetWidth = sidebarVisible ? 240 : 0;
        sidebar.getBoundingClientRect = () => {{
          const width = sidebar.classList.contains('hidden') ? 0 : 240;
          const left = railOnRight ? viewport.width - width : 0;
          return {{ left, top: 0, right: left + width, bottom: viewport.height, width, height: viewport.height }};
        }};
        const rail = makeElement('icon-rail');
        rail.offsetWidth = 48 * viewport.scale;
        if (railOnRight) rail.classList.add('right-side');
        rail.getBoundingClientRect = () => {{
          const width = 48 * viewport.scale;
          const left = railOnRight ? viewport.width - width : 0;
          return {{ left, top: 0, right: left + width, bottom: viewport.height, width, height: viewport.height }};
        }};
        body.appendChild(sidebar);
        body.appendChild(rail);

        const makeModal = (id) => {{
          const modal = makeElement(id);
          modal.classList.add('modal');
          const content = makeElement(id + '-content');
          content.classList.add('modal-content');
          content.offsetWidth = 720;
          content.offsetHeight = 500;
          modal.children.push(content);
          content.parentNode = modal;
          content.closest = () => modal;
          modal.parentNode = body;
          modal.querySelector = (selector) => selector.includes('modal-content') ? content : null;
          modal.contains = (other) => other === content;
          modalRegistry.push(modal);
          return {{ modal, content }};
        }};

        const byDockClass = (selector) => {{
          const match = /^\\.([a-z-]+)$/.exec(selector);
          if (!match) return null;
          return modalRegistry.filter((modal) => modal.classList.contains(match[1]));
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
            return {{ matches: query.includes('pointer: fine') || query.includes('any-pointer: fine') }};
          }},
          getComputedStyle(el) {{
            return {{
              display: el?.style?.display || 'block',
              visibility: el?.style?.visibility || 'visible',
              opacity: '1',
              zIndex: '250',
              width: el === rail ? `${{48 * viewport.scale}}px` : (el?.style?.width || '0px'),
              getPropertyValue(name) {{ return el?.style?.getPropertyValue?.(name) || ''; }},
            }};
          }},
        }};
        globalThis.document = {{
          readyState: 'complete',
          body,
          documentElement: root,
          addEventListener() {{}},
          removeEventListener() {{}},
          getElementById(id) {{
            if (id === 'sidebar') return sidebar;
            if (id === 'icon-rail') return rail;
            return modalRegistry.find((modal) => modal.id === id) || null;
          }},
          querySelector(selector) {{
            if (selector === '.icon-rail' || selector === '#icon-rail') return rail;
            if (selector.startsWith('.modal-snap-hint')) return null;
            return null;
          }},
          querySelectorAll(selector) {{
            const docked = byDockClass(selector);
            if (docked) return docked;
            if (selector.startsWith('.modal:not(')) {{
              return modalRegistry.filter((modal) => !modal.classList.contains('hidden')
                && !modal.classList.contains('modal-minimized'));
            }}
            if (selector === '.modal-snap-hint') return [];
            return [];
          }},
          createElement() {{ return makeElement(); }},
        }};
        globalThis.getComputedStyle = globalThis.window.getComputedStyle;
        globalThis.requestAnimationFrame = (fn) => {{ fn(); return 1; }};
        globalThis.cancelAnimationFrame = () => {{}};
        globalThis.MutationObserver = class {{ observe() {{}} disconnect() {{}} }};
        globalThis.ResizeObserver = class {{ observe() {{}} disconnect() {{}} }};
        globalThis.CustomEvent = class {{ constructor(type, options) {{ this.type = type; this.detail = options?.detail; }} }};
        globalThis.localStorage = {{
          values: new Map(),
          getItem(key) {{ return this.values.has(key) ? this.values.get(key) : null; }},
          setItem(key, value) {{ this.values.set(key, String(value)); }},
          removeItem(key) {{ this.values.delete(key); }},
        }};
        Object.defineProperty(globalThis, 'navigator', {{ value: {{ maxTouchPoints: 0 }}, configurable: true }});
        globalThis.screen = {{ orientation: null }};

        const snap = await import({json.dumps(_MODAL_SNAP_URI)});
        const dockVar = (name) => root.style.getPropertyValue(name);
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
def test_horizontal_preview_matches_commit_and_preserves_chat_floor():
    result = _run_case(
        """
        const left = makeModal('left-modal');
        left.content._userDockWidth = 320;
        const leftApplied = snap.applyEdgeDock(left.modal, 'left');
        const right = makeModal('right-modal');
        const preview = snap.edgeDockPreviewRect(right.modal, 'right');
        const applied = snap.applyEdgeDock(right.modal, 'right');
        const committed = right.content.getBoundingClientRect();
        console.log(JSON.stringify({
          leftApplied,
          preview,
          applied,
          committed,
          remaining: 1200 - 48 - leftApplied - applied,
          reserve: dockVar('--right-dock-reserve-w'),
        }));
        """
    )

    assert result["preview"]["width"] == result["applied"]
    assert result["preview"]["left"] == result["committed"]["left"]
    assert result["committed"]["width"] == result["applied"]
    assert result["remaining"] >= 380
    assert float(result["reserve"].removesuffix("px")) == result["applied"]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_side_dock_is_rejected_when_opposite_dock_leaves_no_usable_room():
    result = _run_case(
        """
        const left = makeModal('left-modal');
        left.content._userDockWidth = 634;
        const leftApplied = snap.applyEdgeDock(left.modal, 'left');
        const right = makeModal('right-modal');
        const preview = snap.edgeDockPreviewRect(right.modal, 'right');
        const applied = snap.applyEdgeDock(right.modal, 'right');
        console.log(JSON.stringify({
          leftApplied,
          preview,
          applied,
          rightActive: body.classList.contains('right-dock-active'),
          remaining: 1200 - 48 - leftApplied,
        }));
        """
    )

    assert result["preview"] is None
    assert result["applied"] == 0
    assert result["rightActive"] is False
    assert result["remaining"] >= 380


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_right_navigation_preview_matches_left_commit():
    result = _run_case(
        """
        const right = makeModal('right-modal');
        right.content._userDockWidth = 640;
        const rightApplied = snap.applyEdgeDock(right.modal, 'right');
        const left = makeModal('left-modal');
        left.content._userDockWidth = 900;
        const preview = snap.edgeDockPreviewRect(left.modal, 'left');
        const applied = snap.applyEdgeDock(left.modal, 'left');
        const committed = left.content.getBoundingClientRect();
        console.log(JSON.stringify({
          rightApplied,
          preview,
          applied,
          committed,
          remaining: 1920 - 48 - rightApplied - applied,
        }));
        """,
        width=1920,
        rail_right=True,
    )

    assert result["preview"]["width"] == result["applied"]
    assert result["preview"]["left"] == result["committed"]["left"]
    assert result["committed"]["width"] == result["applied"]
    assert result["remaining"] >= 380


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("first_side,second_side", [("top", "bottom"), ("bottom", "top")])
def test_opposite_vertical_dock_is_rejected_when_it_would_remove_chat(first_side, second_side):
    result = _run_case(
        f"""
        const first = makeModal('first-modal');
        const firstApplied = snap.applyEdgeDock(first.modal, {json.dumps(first_side)});
        const second = makeModal('second-modal');
        const preview = snap.edgeDockPreviewRect(second.modal, {json.dumps(second_side)});
        const controller = snap.makeEdgeDockController(second.modal, {json.dumps(second_side)});
        const x = 600;
        const y = {1 if second_side == 'top' else 799};
        const near = controller.near(x, y);
        controller.onMove(x, y);
        const hovering = controller.hovering();
        const applied = snap.applyEdgeDock(second.modal, {json.dumps(second_side)});
        console.log(JSON.stringify({{
          firstApplied,
          preview,
          near,
          hovering,
          applied,
          secondActive: body.classList.contains({json.dumps(second_side + '-dock-active')}),
          remaining: 800 - firstApplied,
        }}));
        """
    )

    assert result["firstApplied"] == 400
    assert result["preview"] is None
    assert result["near"] is False
    assert result["hovering"] is False
    assert result["applied"] == 0
    assert result["secondActive"] is False
    assert result["remaining"] >= 260


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_two_vertical_docks_fit_when_both_modal_and_chat_floors_can_be_preserved():
    result = _run_case(
        """
        const top = makeModal('top-modal');
        top.content._userDockHeight = 220;
        const topApplied = snap.applyEdgeDock(top.modal, 'top');
        const bottom = makeModal('bottom-modal');
        const preview = snap.edgeDockPreviewRect(bottom.modal, 'bottom');
        const applied = snap.applyEdgeDock(bottom.modal, 'bottom');
        const committed = bottom.content.getBoundingClientRect();
        console.log(JSON.stringify({
          topApplied,
          preview,
          applied,
          committed,
          remaining: 1000 - topApplied - applied,
        }));
        """,
        height=1000,
    )

    assert result["preview"]["height"] == result["applied"]
    assert result["preview"]["top"] == result["committed"]["top"]
    assert result["remaining"] >= 260


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_minimized_chip_tile_path_does_not_fallback_when_reserved_edge_has_no_room():
    result = _run_case(
        f"""
        const bottom = makeModal('bottom-modal');
        snap.applyEdgeDock(bottom.modal, 'bottom');
        const target = makeModal('target-modal');
        target.modal.dataset.edgeDockController = '1';
        const tile = await import({json.dumps(_TILE_MANAGER_URI)});
        const preview = tile.previewZoneAt(600, 20, target.modal);
        tile.snapModalToZone(target.modal, {{
          name: 'top-half',
          rect: {{ left: 48, top: 4, width: 1152, height: 396 }},
        }});
        console.log(JSON.stringify({{
          preview,
          topDocked: target.modal.classList.contains('modal-top-docked'),
          legacyTileZone: target.content.dataset._tileZone || null,
        }}));
        """
    )

    assert result == {"preview": None, "topDocked": False, "legacyTileZone": None}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_suspending_last_same_side_owner_clears_phantom_reserve():
    result = _run_case(
        """
        const first = makeModal('first-modal');
        first.content._userDockHeight = 300;
        snap.applyEdgeDock(first.modal, 'bottom');
        const second = makeModal('second-modal');
        second.content._userDockHeight = 350;
        snap.applyEdgeDock(second.modal, 'bottom');
        snap.suspendDock(first.modal);
        const afterFirst = {
          active: body.classList.contains('bottom-dock-active'),
          size: dockVar('--bottom-dock-h'),
          reserve: dockVar('--bottom-dock-reserve-h'),
        };
        snap.suspendDock(second.modal);
        console.log(JSON.stringify({
          afterFirst,
          afterLast: {
            active: body.classList.contains('bottom-dock-active'),
            size: dockVar('--bottom-dock-h'),
            reserve: dockVar('--bottom-dock-reserve-h'),
          },
        }));
        """
    )

    assert result["afterFirst"] == {"active": True, "size": "350px", "reserve": "350px"}
    assert result["afterLast"] == {"active": False, "size": "", "reserve": ""}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_scaled_preview_and_commit_share_logical_coordinates():
    result = _run_case(
        """
        const right = makeModal('right-modal');
        const preview = snap.edgeDockPreviewRect(right.modal, 'right');
        const applied = snap.applyEdgeDock(right.modal, 'right');
        console.log(JSON.stringify({
          preview,
          applied,
          width: parseFloat(right.content.style.width),
          reserve: dockVar('--right-dock-reserve-w'),
        }));
        """,
        width=1500,
        height=1000,
        scale=1.25,
    )

    assert result["preview"]["width"] == result["applied"] == result["width"]
    assert float(result["reserve"].removesuffix("px")) == result["applied"]
    assert result["preview"]["height"] == 800


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_active_vertical_dock_compacts_after_viewport_height_shrinks():
    result = _run_case(
        """
        const top = makeModal('top-modal');
        const before = snap.applyEdgeDock(top.modal, 'top');
        viewport.height = 400;
        window.innerHeight = 400;
        window.dispatchEvent({ type: 'resize' });
        const after = parseFloat(top.content.style.height);
        console.log(JSON.stringify({
          before,
          after,
          reserve: dockVar('--top-dock-reserve-h'),
          remaining: 400 - after,
        }));
        """
    )

    assert result["before"] == 400
    assert 0 < result["after"] < 400
    assert float(result["reserve"].removesuffix("px")) == result["after"]
    assert result["remaining"] > 0


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_two_active_side_docks_rebalance_after_viewport_width_shrinks():
    result = _run_case(
        """
        const left = makeModal('left-modal');
        left.content._userDockWidth = 320;
        snap.applyEdgeDock(left.modal, 'left');
        const right = makeModal('right-modal');
        snap.applyEdgeDock(right.modal, 'right');
        viewport.width = 1000;
        window.innerWidth = 1000;
        window.dispatchEvent({ type: 'resize' });
        const leftAfter = parseFloat(left.content.style.width);
        const rightAfter = parseFloat(right.content.style.width);
        console.log(JSON.stringify({
          leftAfter,
          rightAfter,
          leftReserve: dockVar('--left-dock-reserve-w'),
          rightReserve: dockVar('--right-dock-reserve-w'),
          remaining: 1000 - 48 - leftAfter - rightAfter,
        }));
        """
    )

    assert result["leftAfter"] > 0
    assert result["rightAfter"] > 0
    assert float(result["leftReserve"].removesuffix("px")) == result["leftAfter"]
    assert float(result["rightReserve"].removesuffix("px")) == result["rightAfter"]
    assert result["remaining"] >= 380


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_vertical_dock_does_not_block_sidebar_restore_after_side_dock_suspends():
    result = _run_case(
        """
        const top = makeModal('top-modal');
        snap.applyEdgeDock(top.modal, 'top');
        const right = makeModal('right-modal');
        right.content._userDockWidth = 420;
        snap.applyEdgeDock(right.modal, 'right');
        const collapsed = sidebar.classList.contains('hidden');
        snap.suspendDock(right.modal);
        console.log(JSON.stringify({
          collapsed,
          restored: !sidebar.classList.contains('hidden'),
          topActive: body.classList.contains('top-dock-active'),
          rightActive: body.classList.contains('right-dock-active'),
        }));
        """,
        width=1000,
        sidebar_visible=True,
    )

    assert result == {
        "collapsed": True,
        "restored": True,
        "topActive": True,
        "rightActive": False,
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_active_side_docks_follow_manual_sidebar_expansion():
    left_result = _run_case(
        """
        const left = makeModal('left-modal');
        left.content._userDockWidth = 320;
        snap.applyEdgeDock(left.modal, 'left');
        const collapsedLeft = parseFloat(left.content.style.left);
        sidebar.classList.remove('hidden');
        window.dispatchEvent({ type: 'resize' });
        console.log(JSON.stringify({
          collapsedLeft,
          expandedLeft: parseFloat(left.content.style.left),
        }));
        """,
        width=1000,
        sidebar_visible=True,
    )
    assert left_result == {"collapsedLeft": 48, "expandedLeft": 240}

    right_result = _run_case(
        """
        const right = makeModal('right-modal');
        right.content._userDockWidth = 420;
        const collapsedWidth = snap.applyEdgeDock(right.modal, 'right');
        sidebar.classList.remove('hidden');
        window.dispatchEvent({ type: 'resize' });
        console.log(JSON.stringify({
          collapsedWidth,
          expandedWidth: parseFloat(right.content.style.width),
          remaining: 1000 - 240 - parseFloat(right.content.style.width),
        }));
        """,
        width=1000,
        sidebar_visible=True,
    )
    assert right_result["expandedWidth"] <= right_result["collapsedWidth"]
    assert right_result["remaining"] >= 380
