import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / 'static' / 'css' / '13-image-editor.css').read_text(encoding='utf-8')
SW = (ROOT / 'static' / 'sw.js').read_text(encoding='utf-8')


def _block(selector):
    idx = CSS.index(selector)
    return CSS[idx:idx + 420]


def test_swatch_row_label_is_more_legible():
    block = _block('.ge-swatch-row-label {')
    assert 'font-size: 10px;' in block
    assert 'opacity: 0.78;' in block
    assert 'opacity: 0.5;' not in block
    assert 'font-size: 9px;' not in block


def test_inactive_dock_tabs_raised():
    block = _block('.ge-dock-tab {')
    assert 'color: color-mix(in srgb, var(--fg) 72%, transparent);' in block


def test_group_head_glyphs_raised():
    assert 'color: color-mix(in srgb, var(--fg) 72%, transparent);' in _block('.ge-dock-collapse {')
    assert 'color: color-mix(in srgb, var(--fg) 72%, transparent);' in _block('.ge-dock-tabs-more {')
    assert 'color: color-mix(in srgb, var(--fg) 70%, transparent);' in _block('.ge-dock-detach {')


def test_sw_cache_name_is_version_agnostic():
    m = re.search(r"CACHE_NAME = '([^']+)'", SW)
    assert m and re.fullmatch(r'odysseus-v\d+-pr40-editor-precache', m.group(1))
