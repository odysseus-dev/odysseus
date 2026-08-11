"""Runs the live-thinking throttle's behavioral suite under pytest.

Behavior lives in tests/live_thinking_scheduler.test.mjs (node:test, no DOM).
This wrapper only exists so the JS suite runs in the normal pytest job.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_live_thinking_scheduler_behavior():
    result = subprocess.run(
        ["node", "--test", "tests/live_thinking_scheduler.test.mjs"],
        cwd=_REPO,
        capture_output=True,
        timeout=30,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"node --test failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
