"""Audit regressions execute the published facade and actual Chromium listeners."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_document_audit_remediation():
    if not shutil.which("node"):
        pytest.skip("Node is unavailable")
    result = subprocess.run(
        ["node", "--test", "tests/documentAuditRemediation.mjs"], cwd=ROOT,
        env=os.environ.copy(), capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
