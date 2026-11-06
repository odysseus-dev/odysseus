"""The vision name-marker list exists twice; pin the copies together.

Python owns the rule (services/hwfit/vision.py) and both row-producing paths
use it — the hwfit catalogs and the /api/model/cached disk scan. The JS copy in
static/js/cookbookVision.js is needed for one case Python never sees: a model
name typed straight into the serve form, which reaches _detectBackend without
passing through either path.

Two copies of a classification rule drifting apart is precisely the failure
mode that made src/tools/cookbook.py's dead engine picker harmful — it had
silently diverged from the JS it duplicated. This test is the cheap guard that
keeps the surviving duplication honest.
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_JS = _REPO / "static" / "js" / "cookbookVision.js"


def _js_markers():
    src = _JS.read_text(encoding="utf-8")
    block = re.search(r"VISION_NAME_MARKERS\s*=\s*\[(.*?)\]", src, re.S)
    assert block, "VISION_NAME_MARKERS array not found in cookbookVision.js"
    return tuple(re.findall(r"'([^']+)'", block.group(1)))


def test_marker_lists_are_identical_and_ordered_the_same():
    from services.hwfit.vision import VISION_NAME_MARKERS

    # Order matters: the markers go into a regex alternation, so a reordering
    # is a behavior change, not a cosmetic one.
    assert _js_markers() == tuple(VISION_NAME_MARKERS)


def test_pipeline_tag_constant_matches():
    from services.hwfit.vision import VISION_PIPELINE_TAG

    src = _JS.read_text(encoding="utf-8")
    assert f"VISION_PIPELINE_TAG = '{VISION_PIPELINE_TAG}'" in src


def test_skip_vision_opt_out_exists_on_both_sides():
    from services.hwfit import vision

    assert vision.is_vision_model({"name": "x/Molmo-8bit-skip-vision",
                                   "pipeline_tag": "image-text-to-text"}) is False
    assert "skip[-_.]vision" in _JS.read_text(encoding="utf-8")
