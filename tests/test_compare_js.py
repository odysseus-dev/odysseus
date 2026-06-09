"""Pin pure helpers in the compare/ frontend module — drives them
through `node --input-type=module` so we get real JS execution without
needing a full Vitest/Jest setup. If `node` isn't installed the suite
skips itself rather than failing.

Most of compare/ pulls in browser-only globals (document, localStorage,
fetch, theme/ui modules). We only test the modules that are genuinely
portable — state.js (plain object + reset function) and the SVG-icon
constants in icons.js. The bigger state-coupled pieces are best
covered via Playwright/Bombadil specs against a running app.
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


def _run_node(script: str) -> dict:
    """Run a JS snippet under node --input-type=module. Returns parsed
    JSON from the last `console.log` line."""
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    out_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not out_lines:
        raise AssertionError("node produced no stdout")
    return json.loads(out_lines[-1])


# ── state.js ───────────────────────────────────────────────────────

def test_state_reset_preserves_config(node_available):
    """`state.reset()` clears transient flags but leaves config
    sticky (API_BASE, _parallel, _blindMode, etc.). A reset must abort
    any pending fetches and zero the metrics array — anything that
    survives reset would leak between compare sessions."""
    script = textwrap.dedent("""
        const mod = await import('./static/js/compare/state.js');
        const { default: state, reset } = mod;
        state.API_BASE = 'http://x';
        state._blindMode = true;
        state._parallel = false;
        state._openingSelector = true;
        state._streaming = true;
        state._finishOrder = 7;
        state._paneSessionIds = ['a','b'];
        state._paneMetrics = [{x:1}];
        state._cachedModels = [{id:1}];
        let aborted = 0;
        state._abortControllers = [{abort: () => aborted++}, {abort: () => aborted++}];
        reset();
        console.log(JSON.stringify({
          api_base_sticky: state.API_BASE,
          blind_sticky: state._blindMode,
          parallel_sticky: state._parallel,
          opening_cleared: state._openingSelector,
          streaming_cleared: state._streaming,
          finish_order_cleared: state._finishOrder,
          session_ids_cleared: state._paneSessionIds.length,
          metrics_cleared: state._paneMetrics.length,
          cached_models_cleared: state._cachedModels.length,
          controllers_aborted: aborted,
          controllers_cleared: state._abortControllers.length,
        }));
    """)
    out = _run_node(script)
    assert out == {
        "api_base_sticky": "http://x",
        "blind_sticky": True,
        "parallel_sticky": False,
        "opening_cleared": False,
        "streaming_cleared": False,
        "finish_order_cleared": 0,
        "session_ids_cleared": 0,
        "metrics_cleared": 0,
        "cached_models_cleared": 0,
        "controllers_aborted": 2,
        "controllers_cleared": 0,
    }


def test_state_reset_resets_probed_set(node_available):
    """`_probed` tracks which model IDs have passed the probe — must
    be cleared on reset so a stale endpoint can't silently use cached
    'ok' state from a previous session."""
    script = textwrap.dedent("""
        const { default: state, reset } = await import('./static/js/compare/state.js');
        state._probed = new Set(['gpt-4', 'sonnet']);
        reset();
        console.log(JSON.stringify({
          size: state._probed.size,
          is_set: state._probed instanceof Set,
        }));
    """)
    out = _run_node(script)
    assert out == {"size": 0, "is_set": True}


# ── icons.js ───────────────────────────────────────────────────────

def test_svg_icon_exports_are_valid_svg(node_available):
    """Every name matching the icon-export naming pattern (`*_ICON`,
    `ICON_*`, `*_SVG`, `EYE_*`, `SAVE_*`, `CHAT_*`, `SEND_*`) must be
    a non-empty string starting with `<svg`. A `null`/`undefined`
    slipping in here only surfaces at runtime when the icon is rendered."""
    script = textwrap.dedent("""
        const icons = await import('./static/js/compare/icons.js');
        const isIconName = (n) => (
          n.endsWith('_ICON') || n.startsWith('ICON_') || n.endsWith('_SVG') ||
          n.startsWith('EYE_') || n.startsWith('SAVE_') ||
          n.startsWith('CHAT_') || n.startsWith('SEND_')
        );
        const bad = [];
        let checked = 0;
        for (const [name, val] of Object.entries(icons)) {
          if (!isIconName(name)) continue;
          checked++;
          if (typeof val !== 'string' || !val.trim().startsWith('<svg')) {
            bad.push({name, type: typeof val, head: String(val).slice(0, 40)});
          }
        }
        console.log(JSON.stringify({ checked, bad }));
    """)
    out = _run_node(script)
    assert out["checked"] >= 10, f"too few icons matched the naming pattern: {out}"
    assert out["bad"] == [], f"non-svg icon export(s): {out['bad']}"


def test_wave_frames_is_valid_animation_strip(node_available):
    """`WAVE_FRAMES` powers the streaming-pane "thinking" animation.
    Pin: array of equal-length non-empty strings — frames of different
    lengths would visibly jitter the layout."""
    script = textwrap.dedent("""
        const { WAVE_FRAMES } = await import('./static/js/compare/icons.js');
        const lengths = new Set(WAVE_FRAMES.map(f => [...f].length));
        console.log(JSON.stringify({
          count: WAVE_FRAMES.length,
          unique_lengths: lengths.size,
          all_strings: WAVE_FRAMES.every(f => typeof f === 'string' && f.length > 0),
        }));
    """)
    out = _run_node(script)
    assert out["count"] > 0
    assert out["unique_lengths"] == 1, "WAVE_FRAMES must be equal-length frames"
    assert out["all_strings"] is True


def test_storage_keys_are_namespaced(node_available):
    """The compare module stores votes + an exclusion pool in
    localStorage. Pin that the keys start with `odysseus-` so they
    can't collide with other apps on the same origin or with a
    different feature of this app."""
    script = textwrap.dedent("""
        const m = await import('./static/js/compare/icons.js');
        console.log(JSON.stringify({
          votes: m.VOTES_STORAGE_KEY,
          pool: m.POOL_STORAGE_KEY,
        }));
    """)
    out = _run_node(script)
    assert out["votes"].startswith("odysseus-")
    assert out["pool"].startswith("odysseus-")
