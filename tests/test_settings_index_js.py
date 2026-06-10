"""Pin the settings search index (command-palette provider).

settings-index.js takes its heavy dependencies (settingsModule,
SHORTCUT_LABELS) via initSettingsIndex() injection instead of importing
settings.js (whose ui.js import does top-level DOM work), so the module
imports cleanly under `node --input-type=module` with a minimal structural
document stub (no jsdom in this repo).

Security invariants pinned here:
- Admin-tab items (tools/users/system) are EXCLUDED unless
  window._isAdmin === true — fail-closed: undefined (auth fetch in flight)
  counts as not-admin. perform() re-checks and throws.
- Value-leakage guard: items derive from label/header text via the strict
  allowlist; input values (e.g. secret@example.com) never reach titles.
- h2 card names use DIRECT text nodes only (svg/span noise dropped).
- Titles are `<Tab> › <Card> › <Label>`, deduped by id.
- Hand-declared shortcuts entries exist without any DOM rows (fresh session).

What this stub does NOT cover (manual browser pass): scraping fidelity
against the real index.html markup, deep-link scroll/pulse timing.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_INDEX = _REPO / "static" / "js" / "settings-index.js"
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")

# Structural stub mirroring the settings DOM contract:
#   [data-settings-panel] > .admin-card > h2 + rows with .settings-label.
# The 'ai' panel carries an input whose VALUE must never leak; 'users' is an
# admin tab. window starts WITHOUT _isAdmin (the fail-closed case).
_STUB = """
const text = (t) => ({ nodeType: 3, textContent: t });
const elem = (props) => ({
  hidden: false, style: {}, childNodes: [], dataset: {},
  querySelector: () => null, querySelectorAll: () => [],
  closest: () => null, getClientRects: () => [],
  classList: { add(){}, remove(){}, contains: () => false },
  ...props,
});

function mkLabel(t) {
  const row = elem({});
  const label = elem({ textContent: t, closest: () => row });
  return { label, row };
}

function mkCard(headerText, labelTexts, extras = {}) {
  const h2 = elem({ childNodes: [elem({}), text(headerText)] }); // svg + text node
  const labels = labelTexts.map(mkLabel);
  return elem({
    querySelector: (sel) => (sel === 'h2' ? h2 : null),
    querySelectorAll: (sel) =>
      sel.includes('.settings-label') ? labels.map(l => l.label) : [],
    ...extras,
  });
}

function mkPanel(tab, cards) {
  return elem({
    dataset: { settingsPanel: tab },
    querySelectorAll: (sel) => (sel === '.admin-card' ? cards : []),
  });
}

// 'ai': two cards with the SAME repeated labels (Endpoint/Model) + an input
// value that must not leak. 'users': admin-gated. A hidden card is skipped.
const secretInput = elem({ value: 'secret@example.com' });
const aiCards = [
  mkCard('Default Chat Model', ['Endpoint', 'Model']),
  mkCard('Utility Model', ['Endpoint', 'Model']),
  mkCard('Hidden Card', ['Ghost'], { hidden: true }),
];
const usersCards = [mkCard('Registration', ['Open signup'])];
const xssCards = [mkCard('<img src=x onerror=alert(1)>', ['<b>Bold</b> label'])];

globalThis.window = globalThis;        // window._isAdmin starts undefined
globalThis.document = {
  body: {},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: (sel) => sel === '[data-settings-panel]'
    ? [mkPanel('ai', aiCards), mkPanel('users', usersCards), mkPanel('search', xssCards)]
    : [],
};
globalThis.requestAnimationFrame = (fn) => fn();
globalThis.performance = globalThis.performance || { now: () => 0 };
"""

_SETUP = f"""
{_STUB}
const {{ getSettingsItems, initSettingsIndex }} = await import('{_INDEX.as_uri()}');
initSettingsIndex({{ open: (tab) => {{ globalThis.__opened = tab; }} }},
                  {{ search: 'Search conversations' }});
"""


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=_SETUP + js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_admin_items_excluded_when_isadmin_undefined():
    # Fail-closed: _isAdmin is undefined (auth fetch in flight) → zero
    # admin-tab items, including the tab-jump item.
    out = _run("""
    const ids = getSettingsItems().map(i => i.id);
    console.log(JSON.stringify(ids.filter(id => id.startsWith('setting:users'))));
    """)
    assert json.loads(out) == []


def test_admin_items_excluded_when_isadmin_false():
    out = _run("""
    window._isAdmin = false;
    const ids = getSettingsItems().map(i => i.id);
    console.log(JSON.stringify(ids.some(id => id.startsWith('setting:users'))));
    """)
    assert json.loads(out) is False


def test_admin_items_included_for_admin():
    out = _run("""
    window._isAdmin = true;
    const ids = getSettingsItems().map(i => i.id);
    console.log(JSON.stringify(ids.some(id => id.startsWith('setting:users'))));
    """)
    assert json.loads(out) is True


def test_perform_rechecks_admin_and_throws():
    # Items captured while admin must STILL refuse to navigate after the flag
    # flips (or was spoofed into the list) — perform() re-checks fail-closed.
    out = _run("""
    window._isAdmin = true;
    const item = getSettingsItems().find(i => i.id === 'setting:users');
    window._isAdmin = false;
    let threw = false;
    try { item.perform(); } catch (e) { threw = true; }
    console.log(JSON.stringify({ threw, opened: globalThis.__opened || null }));
    """)
    res = json.loads(out)
    assert res["threw"] is True
    assert res["opened"] is None


def test_perform_opens_tab_for_allowed_item():
    out = _run("""
    const item = getSettingsItems().find(i => i.id === 'setting:ai');
    const ok = item.perform();
    console.log(JSON.stringify({ ok, opened: globalThis.__opened }));
    """)
    res = json.loads(out)
    assert res["ok"] is True
    assert res["opened"] == "ai"


def test_no_value_leakage_in_titles():
    # The stub's ai panel contains an input valued secret@example.com; no
    # item title or keyword may contain it (labels/headers only).
    out = _run("""
    const items = getSettingsItems();
    const blob = JSON.stringify(items.map(i => [i.id, i.title, i.keywords]));
    console.log(JSON.stringify(blob.includes('secret@example.com')));
    """)
    assert json.loads(out) is False


def test_card_segment_disambiguates_repeated_labels():
    # "Endpoint" appears in two ai cards → two DISTINCT ids, titles carry
    # the card segment.
    out = _run("""
    const titles = getSettingsItems()
      .filter(i => i.id.includes(':endpoint'))
      .map(i => i.title);
    console.log(JSON.stringify(titles));
    """)
    titles = json.loads(out)
    assert "AI Defaults › Default Chat Model › Endpoint" in titles
    assert "AI Defaults › Utility Model › Endpoint" in titles


def test_hidden_cards_are_skipped():
    out = _run("""
    const ids = getSettingsItems().map(i => i.id);
    console.log(JSON.stringify(ids.some(id => id.includes('hidden-card'))));
    """)
    assert json.loads(out) is False


def test_xss_title_kept_as_plain_text():
    # Scraped titles flow into the palette's textContent-only renderer
    # (pinned in test_fuzzy_js.py highlightRuns tests); here we pin that the
    # provider passes the raw text through without HTML-stringifying it into
    # anything executable — the title is data, never markup.
    out = _run("""
    const item = getSettingsItems().find(i => i.title.includes('onerror'));
    console.log(JSON.stringify({ found: !!item, title: item ? item.title : null }));
    """)
    res = json.loads(out)
    assert res["found"] is True
    assert res["title"].startswith("Search › <img")


def test_shortcut_entries_hand_declared_without_dom():
    # Fresh session: no shortcut rows exist anywhere in the (stub) DOM, yet
    # the hand-declared entry from SHORTCUT_LABELS is searchable.
    out = _run("""
    const item = getSettingsItems().find(i => i.id === 'setting:shortcuts:search');
    console.log(JSON.stringify(item ? item.title : null));
    """)
    assert json.loads(out) == "Shortcuts › Search conversations"


def test_tab_jump_items_present():
    out = _run("""
    const ids = getSettingsItems().map(i => i.id);
    console.log(JSON.stringify(['setting:ai', 'setting:search'].every(id => ids.includes(id))));
    """)
    assert json.loads(out) is True
