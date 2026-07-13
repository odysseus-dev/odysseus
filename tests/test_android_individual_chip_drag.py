import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODAL_MANAGER_JS = (ROOT / "static" / "js" / "modalManager.js").read_text(encoding="utf-8")
LAYOUT_CSS = (ROOT / "static" / "css" / "_layout.css").read_text(encoding="utf-8")
WIRE_CHIP_DRAG_JS = MODAL_MANAGER_JS[
    MODAL_MANAGER_JS.index("function _wireChipDrag(chip, dock) {"):
    MODAL_MANAGER_JS.index("\n// Tracks which _LABELS entries", MODAL_MANAGER_JS.index("function _wireChipDrag(chip, dock) {"))
]


def _block(start_marker: str, end_marker: str) -> str:
    start = MODAL_MANAGER_JS.index(start_marker)
    end = MODAL_MANAGER_JS.index(end_marker, start + len(start_marker))
    return MODAL_MANAGER_JS[start:end]


def test_real_android_uses_direct_individual_chip_drag_only():
    pointer_down = _block("  const onPointerDown = (e) => {", "\n  const onPointerMove = (e) => {")
    independent_branch = pointer_down[
        pointer_down.index("if (_usesIndependentChipPositions()) {"):
        pointer_down.index("const onTouch =")
    ]

    assert "dragMode = 'free';" in independent_branch
    assert "dragArmed = true;" in independent_branch
    assert "if (_isOdysseusAndroidApp())" in independent_branch
    assert "_positionAndroidTrashZone(trashZone);" in independent_branch
    assert "document.addEventListener('pointermove', onPointerMove);" in independent_branch
    assert "document.addEventListener('pointerup', onPointerUp, { once: true });" in independent_branch
    assert "dragMode = 'chain';" not in independent_branch
    assert "longPressTimer" not in independent_branch
    assert "const onTouch = (e.pointerType === 'touch' || _usesCompactTouchChips());" in pointer_down


def test_android_drag_detaches_only_the_grabbed_chip_and_preserves_others():
    assert "_detachToFreeDrag(chip, dock, chipStartLeft, chipStartTop);" in MODAL_MANAGER_JS
    assert "if (newIds.length && _chipPositions.size && !_usesIndependentChipPositions())" in MODAL_MANAGER_JS
    assert "const pairSnap = !_isOdysseusAndroidApp()" in MODAL_MANAGER_JS
    assert "if (!_usesIndependentChipPositions()\n        && !overDockHome" in MODAL_MANAGER_JS

    redock_branch = _block(
        "if (_usesIndependentChipPositions() && overDockHome) {",
        "\n        // Drop wherever the pointer let go",
    )
    assert "_chipPositions.delete(myId);" in redock_branch
    assert "_chipPositions.clear();" not in redock_branch
    assert "_saveDockState();" in redock_branch
    assert "_renderDock();" in redock_branch


def test_android_chip_magnetically_previews_and_commits_its_origin():
    assert "function _positionAndroidTrashZone(z)" in MODAL_MANAGER_JS
    assert "Keep the close\n  // target at the top" in MODAL_MANAGER_JS
    assert "function _androidChipDockSnapPosition(dock, width = 40, height = 40)" in MODAL_MANAGER_JS
    assert "function _isAndroidChipDockOriginHit(dock, left, top, width, height)" in MODAL_MANAGER_JS
    assert "function _isIndependentChipDockOriginHit(dock, left, top, width, height)" in MODAL_MANAGER_JS
    assert "const inDockHome = !inZone && _isIndependentChipDockOriginHit(" in MODAL_MANAGER_JS
    assert "dock.classList.toggle('dock-chip-snap-hover', overDockHome);" in MODAL_MANAGER_JS
    assert "chip.classList.toggle('chip-origin-snap', overDockHome);" in MODAL_MANAGER_JS
    assert "const onPointerUp = (e = {}) => {" in MODAL_MANAGER_JS

    assert "#minimized-dock.dock-chip-snap-hover" in LAYOUT_CSS
    assert ".minimized-dock-chip.chip-origin-snap" in LAYOUT_CSS


def test_android_keeps_origin_fixed_but_retains_saved_individual_positions():
    load_state = _block("function _loadDockState() {", "\n// Push the remembered dock position")
    assert "_dockPos = null;" in load_state
    assert "_dockPosByLayout = {};" in load_state
    assert "whole-chain drag behavior while preserving every per-chip position" in load_state
    assert "_chipPositions.set(id, clamped);" in load_state


@pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")
def test_android_pointer_gesture_moves_and_redocks_only_one_chip():
    script = textwrap.dedent(
        """
        const makeClassList = (...initial) => {
          const values = new Set(initial);
          return {
            add(...names) { names.forEach(name => values.add(name)); },
            remove(...names) { names.forEach(name => values.delete(name)); },
            contains(name) { return values.has(name); },
            toggle(name, force) {
              const enabled = force === undefined ? !values.has(name) : !!force;
              if (enabled) values.add(name); else values.delete(name);
              return enabled;
            },
          };
        };

        const makeStyle = () => ({
          setProperty(name, value) { this[name] = String(value); },
          removeProperty(name) { delete this[name]; },
        });

        const makeEmitter = (target) => {
          const listeners = new Map();
          target.addEventListener = (type, fn, options = {}) => {
            const entries = listeners.get(type) || [];
            entries.push({ fn, once: !!options.once });
            listeners.set(type, entries);
          };
          target.removeEventListener = (type, fn) => {
            listeners.set(type, (listeners.get(type) || []).filter(entry => entry.fn !== fn));
          };
          target.dispatch = (event) => {
            event.target ||= target;
            event.currentTarget = target;
            event.preventDefault ||= () => { event.defaultPrevented = true; };
            for (const entry of [...(listeners.get(event.type) || [])]) {
              entry.fn(event);
              if (entry.once) target.removeEventListener(event.type, entry.fn);
            }
          };
          target._listeners = listeners;
          return target;
        };

        const translatedRect = (chip) => {
          const baseLeft = Number.parseFloat(chip.style.left ?? chip._left) || 0;
          const baseTop = Number.parseFloat(chip.style.top ?? chip._top) || 0;
          const match = String(chip.style.transform || '').match(/translate\\((-?[\\d.]+)px,\\s*(-?[\\d.]+)px\\)/);
          const tx = match ? Number(match[1]) : 0;
          const ty = match ? Number(match[2]) : 0;
          const left = baseLeft + tx;
          const top = baseTop + ty;
          return { left, top, right: left + chip.offsetWidth, bottom: top + chip.offsetHeight,
            width: chip.offsetWidth, height: chip.offsetHeight };
        };

        const makeChip = (id, left, top) => {
          const chip = makeEmitter({
            dataset: { modalId: id },
            classList: makeClassList('minimized-dock-chip'),
            style: makeStyle(),
            offsetWidth: 40,
            offsetHeight: 40,
            _left: left,
            _top: top,
            parentElement: null,
            parentNode: null,
            getBoundingClientRect() { return translatedRect(this); },
            setPointerCapture() {},
          });
          return chip;
        };

        const body = {
          children: [],
          appendChild(chip) {
            if (chip.parentElement?.children) {
              chip.parentElement.children = chip.parentElement.children.filter(child => child !== chip);
            }
            chip.parentElement = this;
            chip.parentNode = this;
            if (!this.children.includes(chip)) this.children.push(chip);
          },
        };
        const documentTarget = makeEmitter({
          body,
          querySelectorAll(selector) {
            return selector === 'body > .minimized-dock-chip'
              ? body.children.filter(chip => chip.classList.contains('minimized-dock-chip'))
              : [];
          },
          getElementById() { return null; },
        });
        globalThis.document = documentTarget;
        globalThis.window = { innerWidth: 720, innerHeight: 1000, _chipDragging: false };
        Object.defineProperty(globalThis, 'navigator', {
          configurable: true,
          value: { vibrate() {}, maxTouchPoints: 1 },
        });
        globalThis.performance = { now: () => 1000 };
        globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };
        globalThis.cancelAnimationFrame = () => {};
        globalThis.setTimeout = () => 1;
        globalThis.clearTimeout = () => {};

        const _chipPositions = new Map();
        const _chipDockEdges = new Map();
        const _dockOrder = [];
        let detachCalls = 0;
        let chainCalls = 0;
        let saveCalls = 0;
        let renderCalls = 0;
        let originHit = false;
        let androidTrashCalls = 0;

        const _isOdysseusAndroidApp = () => true;
        const _usesIndependentChipPositions = () => true;
        const _usesCompactTouchChips = () => true;
        const _ensureTrashZone = () => ({
          classList: makeClassList(), style: makeStyle(), dataset: {},
          getBoundingClientRect: () => ({ left: -500, top: -500, width: 88, height: 88 }),
        });
        const _positionAndroidTrashZone = () => { androidTrashCalls += 1; };
        const _positionTrashZoneOpposite = () => { throw new Error('Android used opposite trash placement'); };
        const _detachToFreeDrag = (chip, dock, left, top) => {
          detachCalls += 1;
          chip.style.setProperty('position', 'fixed', 'important');
          chip.style.setProperty('left', `${left}px`, 'important');
          chip.style.setProperty('top', `${top}px`, 'important');
          body.appendChild(chip);
          chip.classList.add('chip-free-drag');
        };
        const _initChainPhysics = () => { chainCalls += 1; return null; };
        const _stepChain = () => {};
        const _pageTouchDock = () => false;
        const previewZoneAt = () => null;
        const clearPreview = () => {};
        const restore = () => {};
        const snapModalToZone = () => {};
        const restoreModalSnap = () => {};
        const _isFullExpanded = () => false;
        const _modalWindowContent = () => null;
        const _clearFullExpandClasses = () => {};
        const _releaseWindowDockState = () => {};
        const _syncExpandButton = () => {};
        const _clampDockPosition = (_dock, left, top) => ({ left, top });
        const _saveDockState = () => { saveCalls += 1; };
        const _isAndroidChipDockOriginHit = () => originHit;
        const _isIndependentChipDockOriginHit = () => originHit;
        const _androidChipDockSnapPosition = () => ({ left: 320, top: 820 });
        const _desktopChipEdgeSnapPosition = () => null;
        const _nearestFloatingChipSnap = () => null;
        const _isDefaultDockDrop = () => false;
        const _nearDock = () => false;
        const _clampChipPosition = (left, top) => ({ left, top });
        const _resetDockToDefault = () => {};
        const _renderDock = () => { renderCalls += 1; };
        const close = () => {};
        const _trashBurst = () => {};
        """
    )
    script += WIRE_CHIP_DRAG_JS
    script += textwrap.dedent(
        """

        const makeDock = (...chips) => {
          const dock = {
            children: [...chips],
            classList: makeClassList(),
            style: makeStyle(),
            offsetWidth: 92,
            offsetHeight: 48,
            contains(chip) { return this.children.includes(chip); },
            querySelector() { return this.children[0] || null; },
            querySelectorAll() { return [...this.children]; },
            getBoundingClientRect() { return { left: 300, top: 800, width: 92, height: 48 }; },
          };
          chips.forEach(chip => { chip.parentElement = dock; chip.parentNode = dock; });
          return dock;
        };
        const pointer = (type, x, y, id = 1) => ({
          type, clientX: x, clientY: y, pointerId: id, pointerType: 'touch',
          button: 0, buttons: type === 'pointerup' ? 0 : 1, timeStamp: type === 'pointerup' ? 120 : 100,
        });

        // Scenario 1: dragging A must detach only A; B remains in the dock,
        // and an unrelated already-floating chip position must survive.
        const chipA = makeChip('qa-a', 300, 800);
        const chipB = makeChip('qa-b', 346, 800);
        const dock1 = makeDock(chipA, chipB);
        _chipPositions.set('qa-existing', { left: 600, top: 180 });
        _wireChipDrag(chipA, dock1);
        chipA.dispatch(pointer('pointerdown', 320, 820));
        documentTarget.dispatch(pointer('pointermove', 120, 260));
        documentTarget.dispatch(pointer('pointerup', 120, 260));
        const independentMove = {
          aIsFree: chipA.parentElement === body,
          bStayedDocked: dock1.children.length === 1 && dock1.children[0] === chipB,
          aPositionSaved: _chipPositions.has('qa-a'),
          existingPositionPreserved: _chipPositions.has('qa-existing'),
          detachCalls,
          chainCalls,
          androidTrashCalls,
        };

        // Scenario 2: dragging that free chip over the composer origin must
        // show the live snap state, then remove only A's free position.
        documentTarget._listeners.clear();
        originHit = true;
        const chipA2 = makeChip('qa-a', 100, 240);
        body.children = [chipA2];
        chipA2.parentElement = body;
        chipA2.parentNode = body;
        const dock2 = makeDock(chipB);
        _wireChipDrag(chipA2, dock2);
        chipA2.dispatch(pointer('pointerdown', 120, 260, 2));
        documentTarget.dispatch(pointer('pointermove', 360, 840, 2));
        const snapPreview = chipA2.classList.contains('chip-origin-snap')
          && dock2.classList.contains('dock-chip-snap-hover');
        documentTarget.dispatch(pointer('pointerup', 360, 840, 2));
        const individualRedock = {
          snapPreview,
          aPositionRemoved: !_chipPositions.has('qa-a'),
          existingPositionPreserved: _chipPositions.has('qa-existing'),
          bStayedDocked: dock2.children.length === 1 && dock2.children[0] === chipB,
          saveCalls,
          renderCalls,
          chainCalls,
        };

        console.log(JSON.stringify({ independentMove, individualRedock }));
        """
    )

    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "independentMove": {
            "aIsFree": True,
            "bStayedDocked": True,
            "aPositionSaved": True,
            "existingPositionPreserved": True,
            "detachCalls": 1,
            "chainCalls": 0,
            "androidTrashCalls": 1,
        },
        "individualRedock": {
            "snapPreview": True,
            "aPositionRemoved": True,
            "existingPositionPreserved": True,
            "bStayedDocked": True,
            "saveCalls": 2,
            "renderCalls": 2,
            "chainCalls": 0,
        },
    }
