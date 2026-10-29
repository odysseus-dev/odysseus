"""Browser regressions for request timeout, attachment updates, and read aloud."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node binary not on PATH")
def test_chat_request_lifecycle_browser_suite():
    result = subprocess.run(
        ["node", "--test", "tests/chatRequestLifecycle.test.mjs"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
