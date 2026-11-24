"""Run document session and tab adoption contract tests in node test runner."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_document_session_and_adoption_contracts():
    result = subprocess.run(
        ["node", "--test", "tests/documentSession.test.mjs"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
