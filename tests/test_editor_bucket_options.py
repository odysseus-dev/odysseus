from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLS = ROOT / "static/js/editor/build/controls.js"
RIGHT_PANEL = ROOT / "static/js/editor/build/right-panel.js"
GALLERY = ROOT / "static/js/galleryEditor.js"
WIRE = ROOT / "static/js/editor/wire-selection-controls.js"
STATE = ROOT / "static/js/editor/state.js"


def test_bucket_options_section_exists_with_tolerance_range():
    src = CONTROLS.read_text(encoding="utf-8")
    assert 'id="ge-bucket-section"' in src
    assert 'class="ge-section-title">Paint Bucket' in src
    assert 'id="ge-bucket-tolerance"' in src
    # Photoshop's Paint Bucket tolerance is 0-255 (not the wand's 0-100).
    assert 'id="ge-bucket-tolerance" min="0" max="255"' in src
    assert 'id="ge-bucket-tolerance-label"' in src
    # Seeded from the live state value, not a hardcoded number.
    assert "value=\"${bucketTolerance}\"" in src
    assert "wandTolerance, bucketTolerance" in src


def test_bucket_section_toggles_for_the_bucket_tool():
    src = GALLERY.read_text(encoding="utf-8")
    assert "document.getElementById('ge-bucket-section')" in src
    assert "bucketSection.style.display = toolId === 'bucket' ? '' : 'none';" in src


def test_bucket_tolerance_is_wired_to_state():
    wire = WIRE.read_text(encoding="utf-8")
    assert "document.getElementById('ge-bucket-tolerance')?.addEventListener('input'" in wire
    assert "state.bucketTolerance = Math.max(0, Math.min(255" in wire
    assert "document.getElementById('ge-bucket-tolerance-label')" in wire


def test_bucket_tolerance_seeded_from_state():
    rp = RIGHT_PANEL.read_text(encoding="utf-8")
    assert "bucketTolerance: state.bucketTolerance," in rp
    # The default must still exist server/client-side and be what the fill reads.
    assert "bucketTolerance: 32" in STATE.read_text(encoding="utf-8")
