"""Pin the command registry feeding the command palette (Search Everywhere).

Driven through `node --input-type=module` with a minimal stubbed `document`
(no jsdom in this repo) — perform() bodies and the getItems() visibility
filter are the only DOM consumers, and both take plain objects happily.
Skips when `node` is not installed (same convention as test_fuzzy_js.py /
test_keybind_altgr_js.py).

Invariants pinned here:
- perform() THROWS when its trigger element is missing (no silent no-op —
  the palette turns the throw into a visible toast).
- getItems() drops tool-window items whose trigger is absent or hidden
  (unconfigured email must not appear) — but actions are never filtered.
- registerProvider() results appear in getItems() (how settings-index.js
  plugs in).
- validation logs console.error for malformed items.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_REGISTRY = _REPO / "static" / "js" / "command-registry.js"
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")

# Stub document: every getElementById returns a visible, clickable element.
# Individual tests override getElementById to hide/remove specific triggers.
_STUB = """
const mkEl = (id) => ({
  id, hidden: false, style: {},
  classList: { contains: () => false },
  parentElement: null,
  click() { globalThis.__clicked = id; },
  focus() { globalThis.__focused = id; },
});
globalThis.document = { body: {}, getElementById: (id) => mkEl(id) };
globalThis.mkEl = mkEl;
"""


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=_STUB + js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_static_items_nonempty_and_well_formed():
    js = f"""
    import {{ getStaticItems }} from '{_REGISTRY.as_uri()}';
    const items = getStaticItems();
    const ok = items.every(i => i.id && i.title && Array.isArray(i.keywords)
        && (i.section === 'Windows' || i.section === 'Actions')
        && typeof i.perform === 'function');
    console.log(JSON.stringify({{ count: items.length, ok }}));
    """
    out = json.loads(_run(js))
    # 12 tool windows + presets + 5 actions = 18
    assert out["count"] >= 15
    assert out["ok"] is True


def test_ids_unique():
    js = f"""
    import {{ getStaticItems }} from '{_REGISTRY.as_uri()}';
    const ids = getStaticItems().map(i => i.id);
    console.log(JSON.stringify(ids.length === new Set(ids).size));
    """
    assert json.loads(_run(js)) is True


def test_presets_item_present():
    # Presets lives in the composer overflow menu (overflow-preset-btn), not
    # the sidebar tools list — easy to lose when editing the registry.
    js = f"""
    import {{ getStaticItems }} from '{_REGISTRY.as_uri()}';
    console.log(JSON.stringify(getStaticItems().some(i => i.id === 'window:presets')));
    """
    assert json.loads(_run(js)) is True


def test_validation_flags_malformed_item():
    js = f"""
    const errs = [];
    console.error = (...a) => errs.push(a.join(' '));
    const {{ register }} = await import('{_REGISTRY.as_uri()}');
    register([{{ id: 'broken:no-title' }}]);
    console.log(JSON.stringify(errs.length > 0));
    """
    assert json.loads(_run(js)) is True


def test_perform_clicks_mapped_trigger():
    js = f"""
    import {{ getStaticItems }} from '{_REGISTRY.as_uri()}';
    const cal = getStaticItems().find(i => i.id === 'window:calendar-modal');
    const ok = cal.perform();
    console.log(JSON.stringify({{ ok, clicked: globalThis.__clicked }}));
    """
    out = json.loads(_run(js))
    assert out["ok"] is True
    assert out["clicked"] == "tool-calendar-btn"


def test_perform_throws_when_trigger_missing():
    # A missing target must THROW (the palette shows a toast), never
    # silently no-op.
    js = f"""
    import {{ getStaticItems }} from '{_REGISTRY.as_uri()}';
    document.getElementById = () => null;
    const cal = getStaticItems().find(i => i.id === 'window:calendar-modal');
    let threw = false;
    try {{ cal.perform(); }} catch (e) {{ threw = true; }}
    console.log(JSON.stringify(threw));
    """
    assert json.loads(_run(js)) is True


def test_getitems_drops_absent_trigger():
    # Unconfigured tools must not appear in results.
    js = f"""
    import {{ getItems }} from '{_REGISTRY.as_uri()}';
    const base = document.getElementById;
    document.getElementById = (id) => id === 'email-section-title' ? null : base(id);
    const ids = getItems().map(i => i.id);
    console.log(JSON.stringify(ids.includes('window:email-lib-modal')));
    """
    assert json.loads(_run(js)) is False


def test_getitems_drops_hidden_trigger():
    js = f"""
    import {{ getItems }} from '{_REGISTRY.as_uri()}';
    const base = document.getElementById;
    document.getElementById = (id) => {{
      const el = base(id);
      if (id === 'email-section-title') el.hidden = true;
      return el;
    }};
    const ids = getItems().map(i => i.id);
    console.log(JSON.stringify(ids.includes('window:email-lib-modal')));
    """
    assert json.loads(_run(js)) is False


def test_getitems_drops_trigger_hidden_by_inline_display_none():
    # Customize-UI visibility prefs hide via inline style.display='none'.
    js = f"""
    import {{ getItems }} from '{_REGISTRY.as_uri()}';
    const base = document.getElementById;
    document.getElementById = (id) => {{
      const el = base(id);
      if (id === 'tool-notes-btn') el.style.display = 'none';
      return el;
    }};
    const ids = getItems().map(i => i.id);
    console.log(JSON.stringify(ids.includes('window:notes-panel')));
    """
    assert json.loads(_run(js)) is False


def test_collapsed_sidebar_does_not_hide_tools():
    # The palette is most useful while the sidebar is closed: a trigger whose
    # only hidden ancestor is the collapsed #sidebar stays available (click
    # handlers fire on display:none elements).
    js = f"""
    import {{ getItems }} from '{_REGISTRY.as_uri()}';
    const sidebar = {{ id: 'sidebar', hidden: false, style: {{}},
      classList: {{ contains: (c) => c === 'hidden' }}, parentElement: null }};
    const base = document.getElementById;
    document.getElementById = (id) => {{
      const el = base(id);
      if (id === 'tool-calendar-btn') el.parentElement = sidebar;
      return el;
    }};
    const ids = getItems().map(i => i.id);
    console.log(JSON.stringify(ids.includes('window:calendar-modal')));
    """
    assert json.loads(_run(js)) is True


def test_actions_are_not_filtered():
    # Actions have no triggerId; even with every element missing they remain.
    js = f"""
    import {{ getItems }} from '{_REGISTRY.as_uri()}';
    document.getElementById = () => null;
    const sections = new Set(getItems().map(i => i.section));
    console.log(JSON.stringify([...sections]));
    """
    assert json.loads(_run(js)) == ["Actions"]


def test_provider_results_appear_in_getitems():
    js = f"""
    import {{ getItems, registerProvider }} from '{_REGISTRY.as_uri()}';
    registerProvider(() => [{{ id: 'setting:test', title: 'Test Setting',
      keywords: [], section: 'Settings', perform: () => true }}]);
    console.log(JSON.stringify(getItems().some(i => i.id === 'setting:test')));
    """
    assert json.loads(_run(js)) is True
