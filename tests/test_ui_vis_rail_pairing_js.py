import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")
APP = ROOT / "static" / "app.js"


# Each full-sidebar tab that has an icon-rail counterpart must pair the rail
# button into its UI_VIS_MAP selector. Otherwise minimizing the sidebar to the
# collapsed icon rail re-shows every tab the user turned off in the full view,
# because the rail launchers are otherwise always visible (see _railToolMap for
# the id mapping; #tool-library-btn's rail counterpart is #rail-archive).
EXPECTED_RAIL_PAIRS = {
    "email-section": "#rail-email",
    "tool-calendar": "#rail-calendar",
    "tool-compare": "#rail-compare",
    "tool-cookbook": "#rail-cookbook",
    "tool-research": "#rail-research",
    "tool-gallery": "#rail-gallery",
    "tool-library": "#rail-archive",
    "tool-memory": "#rail-memory",
    "tool-notes": "#rail-notes",
    "tool-tasks": "#rail-tasks",
    "tool-theme": "#rail-theme",
}


def _load_ui_vis_map():
    src = APP.read_text(encoding="utf-8")
    m = re.search(r"const UI_VIS_MAP = (\{.*?\n  \})", src, re.S)
    assert m, "UI_VIS_MAP block not found in static/app.js"
    obj_lit = m.group(1)
    # Wrap in parens so eval parses it as an object literal, not a block.
    result = subprocess.run(
        ["node", "--input-type=module", "-e",
         "console.log(JSON.stringify(eval('(' + " + json.dumps(obj_lit) + " + ')')))"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def test_every_customizable_tab_pairs_its_rail_button():
    ui_vis_map = _load_ui_vis_map()
    missing = {
        key: rail
        for key, rail in EXPECTED_RAIL_PAIRS.items()
        if rail not in ui_vis_map.get(key, "")
    }
    assert not missing, (
        "these customizable tabs are missing their icon-rail counterpart in "
        f"UI_VIS_MAP (minimizing the sidebar would re-show them): {missing}"
    )