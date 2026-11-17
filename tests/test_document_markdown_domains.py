"""Discover the pure Markdown codec and source-fidelity regressions in pytest."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_markdown_visual_domain_contracts():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    result = subprocess.run(
        [node, '--test', 'tests/sourceFidelity.test.mjs', 'tests/markdownVisualCodec.test.mjs'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
