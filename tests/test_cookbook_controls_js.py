"""Behavioral browser coverage for Cookbook recipes and manual hardware."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node binary not on PATH")
def test_cookbook_controls_browser_suite():
    result = subprocess.run(
        ["node", "--test", "tests/cookbookControls.test.mjs"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
