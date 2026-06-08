"""Pin the pure splitTableRow helper (static/js/markdown/tableRow.js).

Driven through `node --input-type=module` (same approach as test_compare_js.py);
skips when `node` is not installed.

Regression: the old split filtered out every empty cell, so an intentionally
empty interior cell ("| a |  | c |") collapsed the row to 2 columns and
misaligned it with the header.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "markdown" / "tableRow.js"
_HAS_NODE = shutil.which("node") is not None


def _split(row: str):
    js = f"""
    import {{ splitTableRow }} from '{_HELPER.as_posix()}';
    console.log(JSON.stringify(splitTableRow({json.dumps(row)})));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_keeps_empty_interior_cell():
    assert _split("| a |  | c |") == ["a", "", "c"]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_rows_without_outer_pipes():
    assert _split("a | b | c") == ["a", "b", "c"]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_header_row_unaffected():
    assert _split("| h1 | h2 | h3 |") == ["h1", "h2", "h3"]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_non_string_row_falls_back_to_empty_cell():
    js = f"""
    import {{ splitTableRow }} from '{_HELPER.as_posix()}';
    console.log(JSON.stringify([
      splitTableRow(null),
      splitTableRow({{"bad": "row"}})
    ]));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == [[""], [""]]
