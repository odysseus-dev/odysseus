"""Regression coverage for Notes dock state across minimize and restore.

Notes registers a virtual ``notes-panel`` with modalManager while the actual
dock metadata lives on ``#notes-pane``.  These tests exercise the small state
handoff helpers in Node and keep source-level guards around the DOM lifecycle
that cannot be imported without the full browser application.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.helpers.css_loader import read_css_with_imports


ROOT = Path(__file__).resolve().parents[1]
NOTES_PATH = ROOT / "static" / "js" / "notes.js"
CSS_PATH = ROOT / "static" / "style.css"
NOTES_JS = NOTES_PATH.read_text(encoding="utf-8")
CSS = read_css_with_imports(CSS_PATH)


def _dock_helper_source() -> str:
    start = NOTES_JS.index("const NOTES_DOCK_SIDES =")
    end = NOTES_JS.index("\nfunction _forceCloseNotesPanel", start)
    return NOTES_JS[start:end]


def _node_eval(body: str):
    if not shutil.which("node"):
        pytest.skip("node binary not on PATH")
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=body,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def _function_body(signature: str, next_signature: str) -> str:
    start = NOTES_JS.index(signature)
    end = NOTES_JS.index(next_signature, start + len(signature))
    return NOTES_JS[start:end]


def test_minimized_notes_restore_all_four_dock_sides_and_user_sizes():
    script = textwrap.dedent(
        """
        let androidMode = false;
        let mobileLayout = false;
        const applied = [];
        const applyEdgeDock = (pane, side) => {
          applied.push({
            side,
            touchLandscapeDockWidth: pane._touchLandscapeDockWidth ?? null,
            userDockWidth: pane._userDockWidth ?? null,
            userDockHeight: pane._userDockHeight ?? null,
          });
          return true;
        };
        function _isNotesAndroidDockMode() { return androidMode; }
        function _isNotesMobileLayout() { return mobileLayout; }
        """
    )
    script += _dock_helper_source()
    script += textwrap.dedent(
        """
        const restored = [];
        for (const side of NOTES_DOCK_SIDES) {
          const oldPane = {
            _dockSide: side,
            _touchLandscapeDockWidth: 299,
            _userDockWidth: 481,
            _userDockHeight: 333,
            classList: { contains() { return false; } },
          };
          const newPane = {};
          _rememberNotesMinimizedDockState(oldPane);
          restored.push(_restoreNotesMinimizedDockState(newPane));
        }
        console.log(JSON.stringify({ restored, applied }));
        """
    )

    result = _node_eval(script)

    assert result["restored"] == [True, True, True, True]
    assert result["applied"] == [
        {
            "side": side,
            "touchLandscapeDockWidth": 299,
            "userDockWidth": 481,
            "userDockHeight": 333,
        }
        for side in ("left", "right", "top", "bottom")
    ]


def test_minimized_fullscreen_notes_restore_the_underlying_dock_state():
    script = textwrap.dedent(
        """
        const applied = [];
        const applyEdgeDock = (pane, side) => {
          applied.push({
            side,
            touchLandscapeDockWidth: pane._touchLandscapeDockWidth ?? null,
            userDockWidth: pane._userDockWidth ?? null,
            userDockHeight: pane._userDockHeight ?? null,
          });
          return true;
        };
        function _isNotesAndroidDockMode() { return false; }
        function _isNotesMobileLayout() { return false; }
        """
    )
    script += _dock_helper_source()
    script += textwrap.dedent(
        """
        const oldPane = {
          _notesFullscreenReturnState: {
            mode: 'dock',
            side: 'bottom',
            touchLandscapeDockWidth: 302,
            userDockWidth: 515,
            userDockHeight: 348,
          },
          classList: { contains() { return false; } },
        };
        _rememberNotesMinimizedDockState(oldPane);
        const restored = _restoreNotesMinimizedDockState({});
        console.log(JSON.stringify({ restored, applied }));
        """
    )

    assert _node_eval(script) == {
        "restored": True,
        "applied": [
            {
                "side": "bottom",
                "touchLandscapeDockWidth": 302,
                "userDockWidth": 515,
                "userDockHeight": 348,
            }
        ],
    }


def test_android_auto_dock_is_not_overridden_by_saved_desktop_state():
    script = textwrap.dedent(
        """
        let androidMode = true;
        const applied = [];
        const applyEdgeDock = (pane, side) => {
          applied.push(side);
          return true;
        };
        function _isNotesAndroidDockMode() { return androidMode; }
        function _isNotesMobileLayout() { return false; }
        """
    )
    script += _dock_helper_source()
    script += textwrap.dedent(
        """
        _rememberNotesMinimizedDockState({
          _dockSide: 'left',
          _userDockWidth: 444,
          classList: { contains() { return false; } },
        });
        const androidRestore = _restoreNotesMinimizedDockState({});
        androidMode = false;
        const replayAfterAndroid = _restoreNotesMinimizedDockState({});
        console.log(JSON.stringify({ androidRestore, replayAfterAndroid, applied }));
        """
    )

    assert _node_eval(script) == {
        "androidRestore": False,
        "replayAfterAndroid": False,
        "applied": [],
    }


def test_notes_minimize_captures_and_suspends_before_removing_real_pane():
    body = _function_body("export function closePanel(direction)", "export function togglePanel()")

    remember = body.index("_rememberNotesMinimizedDockState(pane)")
    suspend = body.index("suspendDock(pane)")
    leaving = body.index("pane.classList.add('notes-pane-leaving')")
    minimize = body.index("Modals.minimize('notes-panel')")
    assert remember < suspend < leaving < minimize
    assert "_notesMinimizedDockState = null;" in body


def test_notes_restore_runs_after_viewport_mode_and_force_close_discards_state():
    open_body = _function_body("export function openPanel()", "export function closePanel(direction)")
    assert open_body.index("_syncNotesViewportMode(pane);") < open_body.index(
        "_restoreNotesMinimizedDockState(pane);"
    )

    force_close = _function_body("function _forceCloseNotesPanel()", "function _isNotesMobileLayout()")
    assert "_notesMinimizedDockState = null;" in force_close


def test_notes_backdrop_stays_chat_transparent_without_unreachable_click_handler():
    assert "backdrop.addEventListener('click'" not in NOTES_JS

    backdrop_start = CSS.index(".notes-pane-backdrop {")
    pane_start = CSS.index(".notes-pane-backdrop .notes-pane {", backdrop_start)
    dock_start = CSS.index(".notes-pane-backdrop:has(", pane_start)
    backdrop_block = CSS[backdrop_start:pane_start]
    pane_block = CSS[pane_start:dock_start]
    assert "pointer-events: none;" in backdrop_block
    assert "pointer-events: auto;" in pane_block
