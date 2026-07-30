"""Runs the Node-based Memory Graph pure-logic suite (tests/memoryGraph/*.test.mjs).

Covers static/js/memoryGraph.js's non-DOM pieces: category->color resolution,
mapping an API graph response into Cytoscape elements (including dropping
edges with dangling endpoints), the fetch query string, the bundled demo
graph's referential integrity, and the isolate-component BFS. Loaded via a
vm.createContext() sandbox (tests/memoryGraph/graphHarness.mjs) since the
module isn't directly ESM-importable outside a browser (sibling imports of
ui.js/spinner.js/modalManager.js/windowDrag.js). Skipped when node is
unavailable, mirroring tests/test_streaming_segmenter_js.py.

Node-click/detail-panel/search/filter/isolate DOM behavior is exercised
against a running app, not here, consistent with how this project tests
browser-coupled code (see docs/progress.md Session 4 for the manual pass).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_memory_graph_pure_logic_suite():
    test_files = sorted(str(p) for p in (_REPO / "tests" / "memoryGraph").glob("*.test.mjs"))
    assert test_files, "no memoryGraph test files found"

    result = subprocess.run(
        ["node", "--test", *test_files],
        cwd=_REPO,
        capture_output=True,
        timeout=180,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"node --test failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
