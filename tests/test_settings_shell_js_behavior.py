"""Behavioral coverage for the modular Settings shell.

The leaf harness provides focused assertions around the extracted navigation
and lifecycle primitives. The coordinator smoke separately loads the real
settings.js module through Node's native ESM linker together with the real
Settings shell modules, covering their import/export contract and the public
open/close wiring.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_LEAF_HELPER = _REPO / "tests" / "helpers" / "test_settings_shell.js"
_COORDINATOR_HELPER = (
    _REPO / "tests" / "helpers" / "test_settings_shell_coordinator.mjs"
)
_HAS_NODE = shutil.which("node") is not None


def _run_node(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", *args],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_settings_shell_leaf_behavior():
    proc = _run_node(str(_LEAF_HELPER))

    assert proc.returncode == 0, (
        f"Node execution error:\nSTDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}"
    )

    results = json.loads(proc.stdout.strip())

    assert results, "Settings shell leaf harness returned no assertions"

    for result in results:
        assert result["pass"] is True, (
            f"Failed JS behavioral test: {result['test']}"
            + (
                f" ({result.get('detail')})"
                if result.get("detail")
                else ""
            )
        )


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_settings_shell_real_esm_coordinator():
    proc = _run_node(
        "--experimental-vm-modules",
        str(_COORDINATOR_HELPER),
    )

    assert proc.returncode == 0, (
        "Real Settings coordinator smoke failed:\n"
        f"STDERR:\n{proc.stderr}\n"
        f"STDOUT:\n{proc.stdout}"
    )

    result = json.loads(proc.stdout.strip())

    assert result == {
        "realEsmGraph": True,
        "initialization": True,
        "navigationCallback": True,
        "directOpen": True,
        "directClose": True,
    }
