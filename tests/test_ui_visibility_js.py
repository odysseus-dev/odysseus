import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _resolve(state):
    """Run resolveVisibility(state) in node; return {selector: visible}."""
    return _node_eval(
        f"""
        const {{ resolveVisibility }} = await import('./static/js/ui_visibility.js');
        console.log(JSON.stringify(resolveVisibility({json.dumps(state)})));
        """
    )


def _map():
    return _node_eval(
        """
        const { UI_VIS_MAP } = await import('./static/js/ui_visibility.js');
        console.log(JSON.stringify(UI_VIS_MAP));
        """
    )


# Selectors (kept in one place so the tests read as plain assertions).
EMAIL = "#email-section, #rail-email"
TOOLS = "#tools-section"
CAL = "#tool-calendar-btn, #rail-calendar"
COMPARE = "#tool-compare-btn, #rail-compare"
LIB = "#tool-library-btn, #rail-archive"
RESEARCH = "#tool-research-btn, #rail-research"
NEWCHAT = "#rail-new-session"
RAG = "#overflow-rag-btn"

# Full-sidebar tabs that have an icon-rail counterpart must pair it into their
# UI_VIS_MAP selector; otherwise minimizing the sidebar re-shows a tab the user
# turned off in the full view (#tool-library-btn's rail counterpart is #rail-archive).
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


def test_every_customizable_tab_pairs_its_rail_button():
    ui_vis_map = _map()
    missing = {
        key: rail
        for key, rail in EXPECTED_RAIL_PAIRS.items()
        if rail not in ui_vis_map.get(key, "")
    }
    assert not missing, (
        "these tabs are missing their icon-rail counterpart in UI_VIS_MAP "
        f"(minimizing the sidebar would re-show them): {missing}"
    )


def test_defaults_everything_visible_except_default_off():
    m = _resolve({})
    assert m[EMAIL] is True
    assert m[TOOLS] is True
    assert m[CAL] is True
    assert m[NEWCHAT] is True
    assert m[RAG] is False  # rag-toggle-btn is default-off


def test_email_off_hides_email_and_its_rail_only():
    m = _resolve({"email-section": False})
    assert m[EMAIL] is False
    assert m[CAL] is True
    assert m[TOOLS] is True


def test_tool_off_hides_its_rail_launcher():
    m = _resolve({"tool-calendar": False})
    assert m[CAL] is False
    assert m[COMPARE] is True


def test_library_off_hides_archive_rail():
    # tool-library's rail counterpart is #rail-archive (mirrors _railToolMap).
    m = _resolve({"tool-library": False})
    assert m[LIB] is False


def test_tools_off_hides_every_tool_rail_but_not_email():
    m = _resolve({"tools-section": False})
    assert m[TOOLS] is False
    for sel in (CAL, COMPARE, LIB, RESEARCH):
        assert m[sel] is False, sel
    assert m[EMAIL] is True  # email is independent of the Tools section


def test_tools_off_overrides_per_tool_on():
    # A tool individually "on" must still hide when its parent Tools is off.
    m = _resolve({"tools-section": False, "tool-calendar": True})
    assert m[CAL] is False


def test_tools_on_with_tool_off_hides_only_that_tool():
    m = _resolve({"tools-section": True, "tool-research": False})
    assert m[RESEARCH] is False
    assert m[CAL] is True


def test_rail_new_chat_off_hides_new_session():
    m = _resolve({"rail-new-chat": False})
    assert m[NEWCHAT] is False


def test_explicit_false_takes_precedence_over_default_on():
    m = _resolve({"rag-toggle-btn": True})
    assert m[RAG] is True