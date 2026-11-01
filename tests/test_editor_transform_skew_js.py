from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


STATE = read("static/js/editor/state.js")
POPUP = read("static/js/editor/build/transform-popup.js")
SESSION = read("static/js/editor/tools/transform-session.js")
MULTI = read("static/js/editor/multi-transform.js")
GEO = read("static/js/editor/transform-frame-geometry.js")
HANDLES = read("static/js/editor/tools/transform-handles.js")
DRAG = read("static/js/editor/tools/transform-drag.js")


def test_state_has_skew_fields():
    assert "transformPendingSkewX: 0" in STATE
    assert "transformPendingSkewY: 0" in STATE


def test_popup_exposes_skew_fields():
    assert 'id="ge-transform-skew-x"' in POPUP
    assert 'id="ge-transform-skew-y"' in POPUP
    assert 'data-spin-for="ge-transform-skew-x"' in POPUP
    assert 'data-spin-for="ge-transform-skew-y"' in POPUP


def test_session_threads_skew_through_transform():
    assert "state.transformPendingSkewX" in SESSION
    assert "state.transformPendingSkewY" in SESSION
    assert "'#ge-transform-skew-x': state.transformPendingSkewX" in SESSION
    assert "skewX: state.transformPendingSkewX" in SESSION
    assert "state.transformPendingSkewX = target.skewX" in SESSION
    assert "Math.tan((geometry.skewX" in SESSION
    assert "Math.tan((geometry.skewY" in SESSION


def test_multitransform_geometry_carries_skew():
    assert "target.skewX || 0" in MULTI
    assert "target.skewY || 0" in MULTI
    assert "skewPadX" in MULTI
    assert "skewPadY" in MULTI
    assert "skewX," in MULTI
    assert "skewY," in MULTI


def test_geometry_applies_local_shear():
    assert "export function skewTransformFrame(frame, handle, startPoint, currentPoint, options = {}) {" in GEO
    assert "const shearX = Math.tan(skewX * Math.PI / 180);" in GEO
    assert "const shearY = Math.tan(skewY * Math.PI / 180);" in GEO
    assert "if (ySign) skewX = clamp(startSkewX" in GEO
    assert "if (xSign) skewY = clamp(startSkewY" in GEO


def test_ctrl_drag_skews_the_grabbed_handle():
    assert "import { resizeTransformFrame, skewTransformFrame, distortTransformFrame } from '../transform-frame-geometry.js';" in DRAG
    assert "state.transformStartSkewX = state.transformPendingSkewX || 0;" in DRAG
    assert "state.transformStartSkewY = state.transformPendingSkewY || 0;" in DRAG
    assert "if (e.ctrlKey || e.metaKey) {" in DRAG
    assert "skewTransformFrame({" in DRAG
    assert "state.transformPendingSkewX = skewed.skewX;" in DRAG
    assert "state.transformPendingSkewY = skewed.skewY;" in DRAG


def test_handles_thread_skew_and_grow_the_overlay_past_the_image():
    # transformFrame() must feed skew into the shared geometry...
    assert "skewX: state.transformPendingSkewX || 0," in HANDLES
    assert "skewY: state.transformPendingSkewY || 0," in HANDLES
    # ...and the overlay must be sized to the frame, not just the image.
    assert "export function syncOverlay(margin, extents = null) {" in HANDLES
    assert "const cover = syncOverlay(margin, frameExtents(geometry));" in HANDLES
    assert "function frameExtents(geometry) {" in HANDLES
    assert "ctx.translate(-cover.left, -cover.top);" in HANDLES
    assert "const MAX_BITMAP = 8192;" in HANDLES


def test_transform_controls_live_in_the_header_strip():
    css = read("static/css/13-image-editor.css")
    controls = read("static/js/editor/build/controls.js")
    assert 'class="ge-transform-section" id="ge-transform-section" style="display:none;"' in controls
    assert "const pop = document.getElementById('ge-transform-section');" in SESSION
    assert "pop.innerHTML = `<div class=\"ge-transform-popup\">${transformPopupHTML()}</div>`;" in SESSION
    assert "state.container.appendChild(pop)" not in SESSION
    assert "pop.className = 'ge-transform-popup'" not in SESSION
    assert "state.transformPopup.style.display = 'none';" in SESSION
    assert "#ge-transform-section .ge-transform-popup," in css
    assert "display: contents;" in css
    # The head's `display: flex !important` (for the floating popup) must be
    # out-ranked so the wrapper flattens into the strip's single row.
    assert "#ge-transform-section .ge-transform-popup-head { display: contents !important; }" in css
    # The floating popup spaced its buttons off the fields; that stray top
    # margin must be neutralised or the strip row grows taller than the bar.
    assert "#ge-transform-section button { margin-top: 0 !important; }" in css


def test_ctrl_drag_distorts_only_the_dragged_point():
    # New state slot + reset + target round-trip.
    assert "transformPendingDistort: null," in STATE
    assert "distort: state.transformPendingDistort," in SESSION
    assert "state.transformPendingDistort = target.distort || null;" in SESSION
    # The session renders the warped quad.
    assert "if (geometry.distort && geometry.quad) {" in SESSION
    assert "perspectiveWarpCanvas(source, normalized, canvas.width, canvas.height)" in SESSION
    # Geometry exposes the pure distort helper + places handles on the quad.
    assert "export function distortTransformFrame(frame, handle, startPoint, currentPoint, options = {}) {" in GEO
    assert "const corners = ['tl', 'tr', 'br', 'bl'];" in GEO
    assert "tl: ['tl'], tr: ['tr'], br: ['br'], bl: ['bl']," in GEO
    assert "t: ['tl', 'tr'], b: ['bl', 'br'], l: ['tl', 'bl'], r: ['tr', 'br']," in GEO
    assert "}[handle];" in GEO
    assert "const LIMIT = 4;" in GEO
    # Handles thread the quad through to the skeleton.
    assert "distort: state.transformPendingDistort || null," in HANDLES
    # Multi-transform sizes the layer to the quad AABB.
    assert "const distort = target.distort || null;" in MULTI
    # Drag: Ctrl grabs a corner/edge -> distort (only that point); skew stays for rot.
    assert "import { resizeTransformFrame, skewTransformFrame, distortTransformFrame } from '../transform-frame-geometry.js';" in DRAG
    assert "state.transformStartDistort = state.transformPendingDistort || null;" in DRAG
    assert "const distorted = distortTransformFrame({" in DRAG
    assert "state.transformPendingDistort = distorted;" in DRAG


def test_distort_bakes_into_the_raster_instead_of_the_placed_source():
    # A free-distort quad is a non-affine warp; placed-layer data is affine
    # only, so the session must fall back to the raster branch for it.
    assert "const usePlaced = snapshot.placed" in SESSION
    assert "&& (snapshot.wasPlaced || state.transformPreserveSource !== false)" in SESSION
    assert "&& !plannedGeometry.distort;" in SESSION
    assert "if (usePlaced) {" in SESSION
    assert "if (snapshot.kind === 'raster' || plannedGeometry.distort) {" in SESSION
