"""Run the deployed editor against deterministically reordered navigation responses."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", [
    "sessions", "session-error", "open", "create", "suggestions-active",
    "suggestions-loaded", "suggestions-unloaded", "suggestions-failure",
    "suggestions-away", "suggestions-session",
])
def test_document_navigation_remediation(scenario):
    result = subprocess.run(
        ["node", "tests/documentNavigationRemediation.mjs", scenario],
        cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
