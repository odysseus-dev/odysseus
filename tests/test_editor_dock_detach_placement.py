"""The dock groups no longer expose a tear-off / undock control."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCK_JS = (ROOT / "static/js/editor/build/dock.js").read_text()
SW_JS = (ROOT / "static/sw.js").read_text()


def test_group_undock_control_is_removed():
    assert "ge-dock-detach" not in DOCK_JS
    assert "Undock panel group" not in DOCK_JS
    assert "activeItem.after(detach);" not in DOCK_JS
    assert "detachPlacers.set(" not in DOCK_JS


def test_per_tab_tearoff_glyph_is_removed():
    assert "ge-dock-tab-pop" not in DOCK_JS
    assert "item.appendChild(pop);" not in DOCK_JS


def test_sw_cache_name_is_versioned():
    m = re.search(r"CACHE_NAME = '([^']+)'", SW_JS)
    assert m and re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
