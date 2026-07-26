# tests/test_tour_core_js.py
"""Tests for static/js/tour-core.js module presence and JS syntax."""

from pathlib import Path
import subprocess

REPO_ROOT = Path(__file__).resolve().parent.parent
TOUR_CORE_JS = REPO_ROOT / "static" / "js" / "tour-core.js"


def test_tour_core_file_exists():
    """Verify that static/js/tour-core.js exists in the workspace."""
    assert TOUR_CORE_JS.exists(), "tour-core.js must exist under static/js/"


def test_tour_core_js_syntax():
    """Verify that static/js/tour-core.js passes node syntax checking."""
    result = subprocess.run(
        ["node", "--check", str(TOUR_CORE_JS)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --check failed for tour-core.js:\n{result.stderr}"


def test_tour_core_defines_global():
    """Verify that tour-core.js exports window.TourCore."""
    content = TOUR_CORE_JS.read_text(encoding="utf-8")
    assert "window.TourCore" in content
    assert "ensureTourStyles" in content
    assert "cancelActiveTour" in content
    assert "makeHalo" in content
    assert "positionTooltip" in content
    assert "streamHTML" in content
