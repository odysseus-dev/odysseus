"""Keyboard and resource-lifecycle checks against the deployed document editor."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_document_tabs_and_panel_lifecycle():
    result = subprocess.run(
        ["node", "tests/documentPanelRemediation.mjs"], cwd=ROOT,
        env=os.environ.copy(), capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
