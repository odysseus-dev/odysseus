"""Browser execution of production history and resume entry functions."""
import subprocess
from pathlib import Path
from tests.helpers.playwright_gate import requires_playwright


@requires_playwright
def test_history_resume_rendering_browser_suite():
    result = subprocess.run(
        ["node", "--test", "tests/historyResumeRendering.test.mjs"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
