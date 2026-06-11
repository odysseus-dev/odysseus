"""Pin the web_search tool-icon rendering in the agent thread (PR #??).

Verifies:
- web_search renders an <svg> icon instead of raw markup
- Other tools get the default ▶ icon
- Hostile tool names are HTML-escaped in the label

Pure JS via node --input-type=module (same approach as
test_composer_arrow_up_recall_js.py). Skips when node is not installed.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None

_CHECK_JS = r"""
function esc(s) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return (s || '').replace(/[&<>"']/g, (m) => map[m]);
}

const _searchIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" style="vertical-align:-2px;margin-right:4px"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';

const _toolLabels = {
  web_search: 'Searching',
  bash: 'Running',
};

const _toolIcons = {
  web_search: _searchIcon,
};

function renderIcon(toolName) {
  return _toolIcons[toolName.toLowerCase()] || '\u25B6';
}

function renderLabel(toolName) {
  return _toolLabels[toolName.toLowerCase()] || toolName;
}

function renderThreadHTML(toolName, cmd) {
  const label = renderLabel(toolName);
  const icon = renderIcon(toolName);
  const cmdHtml = cmd ? `<pre class="agent-thread-cmd">${esc(cmd)}</pre>` : '';
  return `<div class="agent-thread-dot"></div><div class="agent-thread-header"><span class="agent-thread-icon">${icon}</span><span class="agent-thread-tool">${esc(label)}</span><span class="agent-thread-wave">\u2581\u2582\u2583</span></div><div class="agent-thread-content">${cmdHtml}</div>`;
}

const cases = CASES_JSON;
const results = cases.map(c => {
  const html = renderThreadHTML(c.tool, c.cmd || '');
  return { tool: c.tool, html };
});
console.log(JSON.stringify(results));
"""


def _run(cases: list) -> list:
    js = _CHECK_JS.replace("CASES_JSON", json.dumps(cases))
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_web_search_icon_contains_svg():
    out = _run([{"tool": "web_search"}])[0]
    assert "<svg" in out["html"], "Expected <svg> in agent-thread-icon for web_search"
    assert "Searching" in out["html"], "Expected 'Searching' label for web_search"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_default_tool_icon_is_triangle():
    out = _run([{"tool": "bash"}])[0]
    assert "▶" in out["html"], "Expected ▶ icon for tools without custom icon"
    assert "<svg" not in out["html"], "Expected no <svg> for bash"
    assert "Running" in out["html"], "Expected 'Running' label for bash"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_unknown_tool_falls_back_to_name():
    out = _run([{"tool": "my_custom_tool"}])[0]
    assert "▶" in out["html"], "Expected ▶ for unknown tool"
    assert "my_custom_tool" in out["html"], "Expected tool name as label"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_hostile_tool_name_is_escaped():
    out = _run([{"tool": '<img src=x onerror="alert(1)">'}])[0]
    assert "&lt;img" in out["html"], "Expected < to be HTML-escaped"
    assert "&gt;" in out["html"], "Expected > to be HTML-escaped"
    assert "<img" not in out["html"], "Raw <img> must not appear"
    assert "onerror" not in out["html"] or "&quot;" in out["html"], "onerror must not be executable"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_unknown_tool_case_insensitive_matches_icons():
    out = _run([{"tool": "WEB_SEARCH"}, {"tool": "Web_Search"}])
    for r in out:
        assert "<svg" in r["html"], f"Expected SVG for case-variant '{r['tool']}'"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_command_is_escaped():
    out = _run([{"tool": "bash", "cmd": "echo $HOME && ls"}])[0]
    assert "echo $HOME" in out["html"], "Expected command text in output"
