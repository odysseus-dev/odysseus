"""Regressions independently found while reviewing the integrated remediation."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", [
    "restore-save", "undo-edit", "undo-normal", "session-save-failure", "rich-restore", "stream-suggestions",
])
def test_document_independent_regressions(scenario):
    result = subprocess.run(
        ["node", "tests/documentAdversarialRemediation.cjs", scenario], cwd=ROOT,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
