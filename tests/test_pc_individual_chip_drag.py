import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODAL_MANAGER_JS = (ROOT / "static" / "js" / "modalManager.js").read_text(
    encoding="utf-8"
)
LAYOUT_CSS = (ROOT / "static" / "css" / "_layout.css").read_text(
    encoding="utf-8"
)
CHIP_HELPERS_JS = MODAL_MANAGER_JS[
    MODAL_MANAGER_JS.index("const CHIP_EDGE_CAPTURE = 38;") : MODAL_MANAGER_JS.index(
        "\nfunction _applyDockPos(dock)"
    )
]


def _block(start_marker: str, end_marker: str) -> str:
    start = MODAL_MANAGER_JS.index(start_marker)
    end = MODAL_MANAGER_JS.index(end_marker, start + len(start_marker))
    return MODAL_MANAGER_JS[start:end]


def test_pc_uses_direct_individual_drag_before_pointer_or_shape_branching():
    platform_gate = _block(
        "function _usesIndependentChipPositions() {",
        "\nfunction _androidChipDockSnapPosition(",
    )
    pointer_down = _block(
        "  const onPointerDown = (e) => {",
        "\n  const onPointerMove = (e) => {",
    )
    independent = pointer_down[
        pointer_down.index("if (_usesIndependentChipPositions()) {") : pointer_down.index(
            "const onTouch ="
        )
    ]

    assert "return _isOdysseusAndroidApp() || !_isMobileDevice();" in platform_gate
    assert "dragMode = 'free';" in independent
    assert "dragArmed = true;" in independent
    assert "_detachToFreeDrag" not in independent  # detaches after crossing threshold
    assert "document.addEventListener('pointermove', onPointerMove);" in independent
    assert pointer_down.index("if (_usesIndependentChipPositions()) {") < pointer_down.index(
        "const onTouch ="
    )


def test_pc_free_positions_render_for_both_pills_and_narrow_circles():
    render = _block("function _renderDock() {", "\n// Lazy-build the magnetic close target")

    assert "if (pos && _usesIndependentChipPositions())" in render
    assert render.index("document.body.appendChild(chip);") < render.index(
        "const next = _chipPositionForStoredEdge("
    )
    assert ".minimized-dock-chip {" in LAYOUT_CSS
    assert "@media (max-width: 768px)" in LAYOUT_CSS
    narrow = LAYOUT_CSS[LAYOUT_CSS.index("@media (max-width: 768px)") :]
    assert ".minimized-dock-chip {" in narrow
    assert "width: 40px; height: 40px;" in narrow
    assert "border-radius: 50% !important;" in narrow


def test_pc_drop_priority_and_individual_state_cleanup_are_explicit():
    pointer_move = _block(
        "  const onPointerMove = (e) => {",
        "\n  const onPointerUp = (e = {}) => {",
    )
    pointer_up = _block(
        "  const onPointerUp = (e = {}) => {",
        "\n  chip.addEventListener('pointerdown', onPointerDown);",
    )

    assert pointer_move.index("_isIndependentChipDockOriginHit(") < pointer_move.index(
        "_desktopChipEdgeSnapPosition("
    )
    assert pointer_move.index("_desktopChipEdgeSnapPosition(") < pointer_move.index(
        "_nearestFloatingChipSnap("
    )
    assert "_chipDockEdges.set(myId, chipEdgeSnap.edge);" in pointer_up
    assert "_chipDockEdges.delete(myId);" in pointer_up
    assert "if (!_usesIndependentChipPositions()" in pointer_up
    assert "previewZoneAt(" not in pointer_move
    assert "restore(chip.dataset.modalId)" not in pointer_up


def test_pc_edge_state_is_persistent_responsive_and_cleaned_on_close():
    assert "chipEdges: Object.fromEntries(_chipDockEdges)" in MODAL_MANAGER_JS
    assert "_chipDockEdges.set(id, edge);" in MODAL_MANAGER_JS
    assert "_applyFreeChipPositions();" in MODAL_MANAGER_JS
    assert MODAL_MANAGER_JS.count("_chipDockEdges.delete(id);") >= 3
    assert "&& !a.name.startsWith('data-chip-')" in MODAL_MANAGER_JS
    assert "delete chip.dataset.chipDockEdge;" in MODAL_MANAGER_JS
    assert '.minimized-dock-chip[data-chip-dock-edge="left"]' in LAYOUT_CSS
    assert '.minimized-dock-chip[data-chip-dock-edge="right"]' in LAYOUT_CSS
    assert '.minimized-dock-chip[data-chip-dock-edge="top"]' in LAYOUT_CSS
    assert '.minimized-dock-chip[data-chip-dock-edge="bottom"]' in LAYOUT_CSS


@pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")
def test_pc_edge_and_pair_geometry_supports_pills_circles_and_resize():
    script = textwrap.dedent(
        """
        let viewportWidth = 1200;
        const _chipDockEdges = new Map();
        const _dockWorkspaceBounds = () => ({ left: 100, right: viewportWidth - 100 });
        const _isMobileDevice = () => false;
        const _composerTop = () => 820;
        const _aboveComposerTop = (height) => 820 - height - 8;
        const _clampChipPosition = (left, top, width = 44, height = 44) => ({
          left: Math.max(104, Math.min(viewportWidth - 100 - width - 4, left)),
          top: Math.max(4, Math.min(_aboveComposerTop(height), top)),
        });

        const targetPill = {
          getBoundingClientRect: () => ({
            left: 300, top: 200, right: 400, bottom: 232, width: 100, height: 32,
          }),
        };
        const targetCircle = {
          getBoundingClientRect: () => ({
            left: 500, top: 300, right: 540, bottom: 340, width: 40, height: 40,
          }),
        };
        const dragged = {};
        globalThis.document = {
          querySelectorAll: () => [dragged, targetPill, targetCircle],
        };
        """
    )
    script += CHIP_HELPERS_JS
    script += textwrap.dedent(
        """
        const edges = {
          left: _desktopChipEdgeSnapPosition(101, 410, 120, 32),
          right: _desktopChipEdgeSnapPosition(1099, 410, 120, 32),
          top: _desktopChipEdgeSnapPosition(500, 2, 120, 32),
          bottom: _desktopChipEdgeSnapPosition(700, 850, 120, 32),
        };

        _chipDockEdges.set('right-pill', 'right');
        const wideRight = _chipPositionForStoredEdge(
          'right-pill', { left: 900, top: 420 }, 120, 32,
        );
        viewportWidth = 900;
        const narrowRight = _chipPositionForStoredEdge(
          'right-pill', { left: 900, top: 420 }, 40, 40,
        );

        const xSnap = _nearestFloatingChipSnap(dragged, {
          left: 418, top: 208, right: 528, bottom: 240, width: 110, height: 32,
        });
        const ySnap = _nearestFloatingChipSnap(dragged, {
          left: 492, top: 354, right: 532, bottom: 394, width: 40, height: 40,
        });

        console.log(JSON.stringify({ edges, wideRight, narrowRight, xSnap, ySnap }));
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
    result = json.loads(proc.stdout)
    assert result["edges"] == {
        "left": {"left": 104, "top": 394, "edge": "left"},
        "right": {"left": 976, "top": 394, "edge": "right"},
        "top": {"left": 440, "top": 4, "edge": "top"},
        "bottom": {"left": 640, "top": 780, "edge": "bottom"},
    }
    assert result["wideRight"] == {"left": 976, "top": 420}
    assert result["narrowRight"] == {"left": 756, "top": 420}
    assert result["xSnap"]["axis"] == "x"
    assert result["xSnap"]["left"] == 406
    assert result["xSnap"]["top"] == 200
    assert result["ySnap"]["axis"] == "y"
    assert result["ySnap"]["left"] == 500
    assert result["ySnap"]["top"] == 346
