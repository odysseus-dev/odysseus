"""Regression coverage for Mermaid diagram theming in the browser renderer.

Mermaid renders each diagram to an SVG with the palette baked in at render time,
so the diagram cannot follow the app theme through CSS alone. `static/js/markdown.js`
drives Mermaid's themeable `base` theme from the app's live CSS variables and
re-renders on-screen diagrams when the theme changes. These tests exercise that
logic under `node`, mirroring tests/test_markdown_rendering_js.py.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


# JS that loads static/js/markdown.js as an in-memory ES module, stripping the
# browser-only imports the same way tests/test_markdown_rendering_js.py does.
_LOAD_MODULE = r"""
        let source = fs.readFileSync('./static/js/markdown.js', 'utf8');
        source = source.replace(/import uiModule from ['"]\.\/ui\.js['"];/, '');
        source = source.replace(
          /import \{ splitTableRow \} from ['"]\.\/markdown\/tableRow\.js['"];/,
          `function splitTableRow(row) {
            return (row || '').replace(/^\\s*\\|/, '').replace(/\\|\\s*$/, '').split('|').map(c => c.trim());
          }`
        );
        const emojiSource = fs.readFileSync('./static/js/emojiShortcodes.js', 'utf8')
          .replace(/^export default .*$/m, '')
          .replace(/export const /g, 'const ')
          .replace(/export function /g, 'function ');
        source = source.replace(
          /import \{ replaceEmojiShortcodes, hasEmojiShortcode \} from ['"]\.\/emojiShortcodes\.js['"];/,
          () => emojiSource
        );
        source = source.replace(
          /var escapeHtml = uiModule\.esc;/,
          `var escapeHtml = (value) => String(value ?? '');`
        );
        const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
        const mod = await import(moduleUrl);
"""


def _run_node(script: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", textwrap.dedent(script)],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
    return json.loads(result.stdout.splitlines()[-1])


# Generic English palettes — a dark one and a light one. No app-specific data.
_DARK = {
    "bg": "#282c34", "panel": "#111111", "fg": "#9cdef2",
    "border": "#355a66", "accent": "#e06c75", "font": "'Fira Code', monospace",
}
_LIGHT = {
    "bg": "#f0ebe3", "panel": "#faf6f0", "fg": "#5a5248",
    "border": "#d4cdc2", "accent": "#c47d5a", "font": "'Fira Code', monospace",
}


def test_theme_variables_map_palette_to_mermaid(node_available):
    """_mermaidThemeVariables maps each app colour onto the matching Mermaid
    theme variable, and derives darkMode from the background luminance."""
    script = (
        "import fs from 'node:fs';\n"
        "globalThis.window = { location: { origin: 'http://localhost' } };\n"
        "globalThis.document = { readyState: 'loading', addEventListener() {},\n"
        "  createElement() { return { content: { querySelectorAll() { return []; } },\n"
        "    set innerHTML(v) {}, get innerHTML() { return ''; } }; } };\n"
        "globalThis.MutationObserver = class { observe() {} };\n"
        + _LOAD_MODULE
        + "const dark = mod._mermaidThemeVariables(" + json.dumps(_DARK) + ");\n"
        + "const light = mod._mermaidThemeVariables(" + json.dumps(_LIGHT) + ");\n"
        + "console.log(JSON.stringify({ dark, light }));\n"
    )
    out = _run_node(script)
    dark, light = out["dark"], out["light"]

    # Dark palette maps straight through to the matching Mermaid variables.
    assert dark["background"] == _DARK["bg"]
    assert dark["primaryColor"] == _DARK["panel"]
    assert dark["mainBkg"] == _DARK["panel"]
    assert dark["primaryTextColor"] == _DARK["fg"]
    assert dark["textColor"] == _DARK["fg"]
    assert dark["lineColor"] == _DARK["border"]
    assert dark["nodeBorder"] == _DARK["accent"]
    assert dark["primaryBorderColor"] == _DARK["accent"]
    assert dark["fontFamily"] == _DARK["font"]
    assert dark["darkMode"] is True

    # Light palette flips darkMode and follows the light colours instead.
    assert light["background"] == _LIGHT["bg"]
    assert light["primaryColor"] == _LIGHT["panel"]
    assert light["primaryTextColor"] == _LIGHT["fg"]
    assert light["lineColor"] == _LIGHT["border"]
    assert light["nodeBorder"] == _LIGHT["accent"]
    assert light["darkMode"] is False

    # The two palettes must actually differ, or the diagram wouldn't retheme.
    assert dark["background"] != light["background"]
    assert dark["primaryTextColor"] != light["primaryTextColor"]


def test_theme_change_dispatch_rerenders_mermaid(node_available):
    """Dispatching `odysseus-theme-changed` re-initialises Mermaid with the new
    palette and re-renders every already-drawn diagram from its saved source."""
    script = (
        "import fs from 'node:fs';\n"
        # Collapse the debounce timer so the re-render runs synchronously.
        "globalThis.setTimeout = (fn) => { fn(); return 0; };\n"
        "globalThis.clearTimeout = () => {};\n"
        # Live CSS variables, swapped from dark to light between renders.
        "let CSSVARS = {\n"
        "  '--bg': '#282c34', '--panel': '#111111', '--fg': '#9cdef2',\n"
        "  '--border': '#355a66', '--red': '#e06c75', '--font-family': \"'Fira Code', monospace\"\n"
        "};\n"
        "globalThis.getComputedStyle = () => ({ getPropertyValue(n) { return CSSVARS[n] || ''; } });\n"
        "globalThis.CustomEvent = class { constructor(t, i) { this.type = t; this.detail = i && i.detail; } };\n"
        # One fake <pre class=\"mermaid\"> node with an attribute map.
        "function makeNode(text) {\n"
        "  const attrs = new Map();\n"
        "  return { textContent: text,\n"
        "    getAttribute(n) { return attrs.has(n) ? attrs.get(n) : null; },\n"
        "    setAttribute(n, v) { attrs.set(n, String(v)); },\n"
        "    removeAttribute(n) { attrs.delete(n); },\n"
        "    hasAttribute(n) { return attrs.has(n); },\n"
        "    _processed() { return attrs.has('data-processed'); } };\n"
        "}\n"
        "const NODE = makeNode('graph TD; Start-->Process');\n"
        "const LISTENERS = {};\n"
        "globalThis.document = {\n"
        "  documentElement: {}, readyState: 'loading',\n"
        "  addEventListener(type, fn) { (LISTENERS[type] = LISTENERS[type] || []).push(fn); },\n"
        "  dispatchEvent(evt) { (LISTENERS[evt.type] || []).forEach(fn => fn(evt)); return true; },\n"
        "  createElement() { return { content: { querySelectorAll() { return []; } },\n"
        "    set innerHTML(v) {}, get innerHTML() { return ''; } }; },\n"
        "  querySelectorAll(sel) {\n"
        "    const wantProcessed = sel.includes('[data-processed]') && !sel.includes(':not');\n"
        "    return [NODE].filter(n => wantProcessed ? n._processed() : !n._processed());\n"
        "  } };\n"
        "globalThis.MutationObserver = class { observe() {} };\n"
        # Fake Mermaid global recording every initialize() / run() call.
        "const initCalls = []; const runCalls = [];\n"
        "globalThis.window = { location: { origin: 'http://localhost' },\n"
        "  mermaid: {\n"
        "    initialize(cfg) { initCalls.push(cfg); },\n"
        "    run({ nodes }) { runCalls.push(nodes.map(n => n.textContent));\n"
        "      nodes.forEach(n => n.setAttribute('data-processed', 'true')); } } };\n"
        + _LOAD_MODULE
        # First render on the dark palette (captures source, marks processed).
        + "mod.renderMermaid();\n"
        # Switch the live palette to light, then fire the theme-change event.
        + "CSSVARS = {\n"
        "  '--bg': '#f0ebe3', '--panel': '#faf6f0', '--fg': '#5a5248',\n"
        "  '--border': '#d4cdc2', '--red': '#c47d5a', '--font-family': \"'Fira Code', monospace\"\n"
        "};\n"
        "document.dispatchEvent(new CustomEvent('odysseus-theme-changed', { detail: {} }));\n"
        "const last = initCalls[initCalls.length - 1];\n"
        "console.log(JSON.stringify({\n"
        "  initCount: initCalls.length,\n"
        "  firstBg: initCalls[0] && initCalls[0].themeVariables && initCalls[0].themeVariables.background,\n"
        "  lastBg: last && last.themeVariables && last.themeVariables.background,\n"
        "  lastTheme: last && last.theme,\n"
        "  lastDarkMode: last && last.themeVariables && last.themeVariables.darkMode,\n"
        "  runCount: runCalls.length,\n"
        "  lastRunSource: runCalls[runCalls.length - 1]\n"
        "}));\n"
    )
    out = _run_node(script)

    # Mermaid was initialised once for the dark palette (at load / first render)
    # and re-initialised for the light palette when the theme changed.
    assert out["initCount"] >= 2
    assert out["firstBg"] == _DARK["bg"]
    assert out["lastBg"] == _LIGHT["bg"]
    assert out["lastTheme"] == "base"
    assert out["lastDarkMode"] is False
    # The diagram was rendered once, then re-rendered from its preserved source.
    assert out["runCount"] == 2
    assert out["lastRunSource"] == ["graph TD; Start-->Process"]
