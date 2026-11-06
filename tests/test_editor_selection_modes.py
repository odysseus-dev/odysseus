"""Quick Select / Object Select must honour the New/Add/Subtract mode toggle.

The wand's segmented control (`.ge-wand-mode-btn`) writes `state.wandMode`;
every selection tool has to read it back. Quick Select used to hard-code
`'add'` (so every dab added, regardless of the toggle) and Object Select
hard-coded `'replace'`, so picking Subtract did nothing in either tool.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
QUICK = (ROOT / "static/js/editor/tools/quick-select.js").read_text(encoding="utf-8")
OBJECT = (ROOT / "static/js/editor/tools/object-select.js").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_quick_select_reads_the_wand_mode_toggle():
    assert "import { state } from '../state.js';" in QUICK
    assert "const modeFor = (event) => selectionModeForEvent(event, state.wandMode || 'replace');" in QUICK
    # the draft mode is captured on begin and re-used for the whole stroke
    assert "let strokeMode = 'replace';" in QUICK
    assert "strokeMode = modeFor(event);" in QUICK
    assert "sample(event, strokeMode);" in QUICK
    # after the first replace, later dabs accumulate instead of wiping progress
    assert "sample(event, strokeMode === 'replace' ? 'add' : selectionModeForEvent(event, strokeMode));" in QUICK
    # the old hard-coded add default is gone
    assert "selectionModeForEvent(event, 'add')" not in QUICK


def test_object_select_reads_the_wand_mode_toggle():
    assert "import { state } from '../state.js';" in OBJECT
    assert "mode = selectionModeForEvent(event, state.wandMode || 'replace');" in OBJECT
    assert "mode = selectionModeForEvent(event, 'replace');" not in OBJECT


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
