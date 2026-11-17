"""Real Chromium DOM behavior, not source-string wiring assertions."""
import subprocess
from pathlib import Path


def test_turn_rendering_browser_suite():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", "tests/turnRendering.test.mjs"],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
