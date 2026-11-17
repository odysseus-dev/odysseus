"""Final review regressions exercise published editor modules in Chromium."""
import os
import subprocess
from pathlib import Path


def test_document_final_remediation():
    result = subprocess.run(
        ["node", "--test", "tests/documentFinalRemediation.mjs"],
        cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(),
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
