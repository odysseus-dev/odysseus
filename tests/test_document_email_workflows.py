"""Run document editor email workflows test suite in node test runner."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_document_editor_email_workflows():
    result = subprocess.run(
        [
            "node",
            "--experimental-loader",
            "./tests/helpers/document_email_host_loader.mjs",
            "--test",
            "tests/documentEmailWorkflows.test.mjs",
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
