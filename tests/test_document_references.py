"""Verify document module reference integrity and negative controls."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _require_node_acorn():
    if not shutil.which("node"):
        pytest.skip("Node is unavailable")
    probe = subprocess.run(
        ["node", "-e", "import('node:module').then(({createRequire})=>createRequire(import.meta.url).resolve('acorn'))"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if probe.returncode != 0:
        pytest.skip("acorn package is unavailable to Node")


def test_document_reference_checker_passes():
    _require_node_acorn()
    result = subprocess.run(
        ["node", "scripts/check_document_references.mjs"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_document_reference_tests_pass():
    _require_node_acorn()
    result = subprocess.run(
        [
            "node",
            "--test",
            "tests/documentReferences.test.mjs",
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
