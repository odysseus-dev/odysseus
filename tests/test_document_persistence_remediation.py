"""Behavioral regressions for draft persistence and version-history ownership."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", [
    "minimized-save", "closed-failure", "closed-missing", "closed-network", "history-switch", "history-ai",
    "history-preview", "history-restore", "history-metadata",
    "history-lifecycle", "history-failure", "history-rapid",
])
def test_document_persistence_remediation(scenario):
    result = subprocess.run(
        ["node", "tests/documentPersistenceRemediation.mjs", scenario],
        cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
