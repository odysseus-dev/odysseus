"""A window snapped to a side must take the workspace with it.

Dragging a tool window (extension panel, memory, gallery, ...) to the left or
right edge pins it to that edge as a full-height panel. Before this, the snap
only moved the window: it is ``position: fixed``, so the chat underneath kept
its full width and the docked window sat on top of half the conversation. The
window "docked" and nothing else on screen reacted to it.

``tileManager.applySideDockYield()`` reserves exactly the strip the side window
covers, which the CSS turns into a margin on the workspace. The reservation is
kept under tileManager's own ``tile-left-dock`` / ``tile-right-dock`` body
classes and ``--tile-*-w`` properties rather than modalSnap's
``left-dock-active`` / ``right-dock-active``, because both systems can be live
at the same time and each has to release only its own reservation.

The module pulls in browser globals on import (pointer listeners, a sidebar
watcher), so it runs under a small DOM stub — same approach as
test_copy_message_strips_thinking_js.py.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.helpers.stylesheets import app_css

ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/tileManager.js").as_uri()
CSS = app_css()

_HAS_NODE = shutil.which("node") is not None

# Viewport / geometry the stub pretends to have. Sidebar is 240px wide, so the
# workspace starts at 244 (240 + tileManager's 4px gutter) and the right edge
# of the safe area is 1596. The reservation is measured from the nav edge
# itself (240), not from the gutter — otherwise the chat overlaps the docked
# window by exactly 4px.
INNER_WIDTH = 1600
INNER_HEIGHT = 1000
SIDEBAR_RIGHT = 240
SAFE_LEFT = SIDEBAR_RIGHT + 4
LEFT_HALF_RIGHT = SAFE_LEFT + 676  # window's right edge when tiled left
vw = INNER_WIDTH  # shorter alias for the reservation arithmetic below


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def run_side_yield(contents: str, edge_vars: dict | None = None) -> dict:
    """Run applySideDockYield() against `contents` and report what it reserved.

    `contents` is JS building a list of fake snapped elements, passed as the
    stubbed document.querySelectorAll() result. `edge_vars` seeds modalSnap's
    `--left-dock-w` / `--right-dock-w` (a true edge dock: email, notes).
    """
    script = textwrap.dedent(
        f"""
        const bodyClasses = new Set();
        const rootProps = {{}};

        const sidebar = {{
          classList: {{ contains: () => false }},
          getBoundingClientRect: () => ({{
            left: 0, right: {SIDEBAR_RIGHT}, width: {SIDEBAR_RIGHT},
            top: 0, bottom: {INNER_HEIGHT},
          }}),
        }};

        function el(overrides = {{}}) {{
          const node = Object.assign({{
            isConnected: true,
            hidden: false,
            style: {{ display: '' }},
            dataset: {{}},
            getBoundingClientRect: () => rect(0, 0, 0, 0),
          }}, overrides);
          // Bound to the node, so `classList.contains('hidden')` reflects
          // whatever state the test put on the element itself.
          node.classList = {{
            contains(c) {{ return c === 'hidden' && node.hidden === true; }},
          }};
          return node;
        }}

        // A real DOMRect carries left/top/width/height AND the derived
        // right/bottom. The reservation math reads `right`, so the stub has
        // to derive it too or the tests would pass against a shape the
        // browser never produces.
        function rect(left, top, width, height) {{
          return {{ left, top, width, height, right: left + width, bottom: top + height }};
        }}

        const contents = {contents};

        const rootStyle = {{
          setProperty(name, value) {{ rootProps[name] = value; }},
          removeProperty(name) {{ delete rootProps[name]; }},
          getPropertyValue(name) {{ return rootProps[name] ?? ''; }},
        }};
        Object.assign(rootProps, {json.dumps(edge_vars or {})});

        globalThis.window = {{
          innerWidth: {INNER_WIDTH},
          innerHeight: {INNER_HEIGHT},
          addEventListener() {{}},
          getComputedStyle: () => ({{ display: 'block', getPropertyValue: () => '' }}),
        }};
        globalThis.document = {{
          readyState: 'complete',
          body: {{
            classList: {{
              add: (c) => bodyClasses.add(c),
              remove: (c) => bodyClasses.delete(c),
              contains: (c) => bodyClasses.has(c),
            }},
            className: '',
          }},
          documentElement: {{ style: rootStyle }},
          addEventListener() {{}},
          getElementById: (id) => (id === 'sidebar' ? sidebar : null),
          querySelector: () => null,
          querySelectorAll: () => contents,
        }};
        globalThis.MutationObserver = class {{
          observe() {{}} disconnect() {{}}
        }};
        globalThis.requestAnimationFrame = (cb) => {{ cb(); return 1; }};
        globalThis.getComputedStyle = () => ({{
          getPropertyValue: (name) => rootProps[name] ?? '',
        }});

        // Dynamic, not a static import: the module installs document/window
        // listeners at import time, so the stubs have to exist first.
        const {{ applySideDockYield }} = await import({json.dumps(MODULE)});

        applySideDockYield();

        console.log(JSON.stringify({{
          classes: Array.from(bodyClasses).sort(),
          // Only tileManager's own writes — edge-dock vars are seeded inputs.
          props: Object.fromEntries(
            Object.entries(rootProps).filter(([k]) => k.startsWith('--tile-'))
          ),
        }}));
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def _snapped(zone, left, top, width, height):
    return (
        "el({ dataset: { _tileZone: %r }, "
        "getBoundingClientRect: () => rect(%d, %d, %d, %d) })"
        % (zone, left, top, width, height)
    )


def _hidden(zone, left, top, width, height):
    """A tile that still carries its zone but is no longer on screen."""
    return _snapped(zone, left, top, width, height).replace(
        "el({", "el({ hidden: true,"
    )


def test_right_snapped_window_reserves_its_strip():
    # Right half of the 244..1596 safe area: left = 244 + 1352/2 = 920.
    result = run_side_yield("[%s]" % _snapped("right-half", 920, 4, 676, 992))
    assert result["classes"] == ["tile-right-dock"]
    assert result["props"] == {"--tile-right-w": "%dpx" % (INNER_WIDTH - 920)}


def test_left_snapped_window_reserves_its_strip():
    # Measured from the nav's own right edge (SIDEBAR_RIGHT), not from the
    # workspace's current rect — re-measuring would compound the margin, and
    # measuring from tileManager's 4px gutter would overlap the window.
    result = run_side_yield("[%s]" % _snapped("left-half", SAFE_LEFT, 4, 676, 992))
    assert result["classes"] == ["tile-left-dock"]
    assert result["props"] == {"--tile-left-w": "%dpx" % (LEFT_HALF_RIGHT - SIDEBAR_RIGHT)}


def test_windows_on_both_sides_each_get_a_reservation():
    result = run_side_yield(
        "[%s, %s]" % (
            _snapped("right-half", 920, 4, 676, 992),
            _snapped("left-half", SAFE_LEFT, 4, 676, 992),
        )
    )
    assert result["classes"] == ["tile-left-dock", "tile-right-dock"]
    # Both windows are the same size, so the combined clamp scales them
    # equally — neither side is arbitrarily starved.
    assert result["props"]["--tile-left-w"] == result["props"]["--tile-right-w"]


def test_maximize_reserves_nothing():
    # Maximize fills the whole safe area; there is no edge strip to give up.
    result = run_side_yield("[%s]" % _snapped("maximize", SAFE_LEFT, 4, 1352, 992))
    assert result["classes"] == []
    assert result["props"] == {}


def test_reservation_is_released_when_no_window_remains():
    result = run_side_yield("[]")
    assert result["classes"] == []
    assert result["props"] == {}


def test_hidden_or_detached_window_releases_its_reservation():
    # A minimized/closed window keeps its zone data until something tears it
    # down. Reserving space for something nobody can see leaves a dead strip.
    result = run_side_yield("[%s]" % _hidden("right-half", 920, 4, 676, 992))
    assert result["classes"] == []
    assert result["props"] == {}


def test_reservation_never_squeezes_the_workspace_below_the_floor():
    # A window claiming almost the whole viewport must still leave room. The
    # floor is measured against the workspace, i.e. after the nav is taken out
    # — the nav is not usable chat width.
    result = run_side_yield("[%s]" % _snapped("right-half", 40, 4, 1556, 992))
    assert result["classes"] == ["tile-right-dock"]
    assert result["props"] == {"--tile-right-w": "%dpx" % (vw - SIDEBAR_RIGHT - 380)}


def test_two_side_windows_cannot_squeeze_the_workspace_together():
    # Both halves tiled at once: each side passes its own check, so only a
    # clamp on the COMBINED reservation stops them from meeting in the middle
    # and leaving a chat a few dozen pixels wide.
    result = run_side_yield(
        "[%s, %s]" % (
            _snapped("right-half", 920, 4, 676, 992),
            _snapped("left-half", SAFE_LEFT, 4, 676, 992),
        )
    )
    assert result["classes"] == ["tile-left-dock", "tile-right-dock"]
    left = int(result["props"]["--tile-left-w"].removesuffix("px"))
    right = int(result["props"]["--tile-right-w"].removesuffix("px"))
    # Workspace starts after the nav and keeps the floor.
    assert left + right == vw - SIDEBAR_RIGHT - 380


def test_a_true_edge_dock_counts_against_the_budget():
    # Notes holds --right-dock-w from modalSnap's own edge-dock system while a
    # tile takes the left. Both are full-height panels, so the workspace has
    # to keep its floor even though neither system knows about the other.
    result = run_side_yield(
        "[%s]" % _snapped("left-half", SAFE_LEFT, 4, 676, 992),
        edge_vars={"--right-dock-w": "608px"},
    )
    assert result["classes"] == ["tile-left-dock"]
    left = int(result["props"]["--tile-left-w"].removesuffix("px"))
    # Left share + the 608px notes already holds must fit the workspace budget.
    assert left + 608 == vw - SIDEBAR_RIGHT - 380


def test_a_side_with_only_an_edge_dock_gets_no_tile_class():
    # Notes alone owns the right edge. tileManager must not claim that side —
    # it would be a second owner clearing the first one's reservation.
    result = run_side_yield("[]", edge_vars={"--right-dock-w": "608px"})
    assert result["classes"] == []
    assert result["props"] == {}


def test_css_turns_the_reservation_into_workspace_margin():
    # The reservation is inert without these rules — the whole point is that
    # the chat moves out from under the docked window.
    assert "body.tile-right-dock .chat-container," in CSS
    assert "margin-right: var(--tile-right-w, 0px);" in CSS
    assert "body.tile-left-dock:not(.email-doc-split-active) .chat-container {" in CSS
    assert "margin-left: var(--tile-left-w, 0px);" in CSS


def test_snapping_a_side_window_updates_the_reservation():
    # _applySnap / _unsnap / _reclampAll are the three points where a tile's
    # geometry can change. Each has to refresh the reservation or the chat
    # keeps a stale strip (or never gives the space back).
    source = (ROOT / "static/js/tileManager.js").read_text(encoding="utf-8")
    for fn in ("_applySnap", "_unsnap", "_reclampAll"):
        start = source.index(f"function {fn}(")
        # Walk to the matching close of the function's own block.
        depth, i = 0, source.index("{", start)
        while True:
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = source[start:i]
        assert "applySideDockYield()" in body, f"{fn} never refreshes the reservation"