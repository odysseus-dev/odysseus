"""Regression tests for GGUF --include/allow_patterns derivation.

A catalog entry whose `quant` is a display label (e.g. "QAT-INT4") rather
than a real quant token must NOT become an `allow_patterns` filter — that
matches no filename and silently downloads 0 files (google/gemma-4-26B repro).

Driven through `node --input-type=module` (same approach as the other
*_js.py tests); skips when `node` is not installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "cookbookGguf.js"
_HAS_NODE = shutil.which("node") is not None


def _include(model, source=None):
    js = (
        f"import {{ _ggufIncludePattern }} from '{_HELPER.as_posix()}';"
        f"const m = {json.dumps(model)};"
        f"const s = {json.dumps(source)};"
        f"process.stdout.write(_ggufIncludePattern(m, s));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_display_label_quant_falls_back_to_all_gguf():
    # "QAT-INT4" is a label, not a filename substring — must not filter.
    assert _include({"quant": "QAT-INT4"}) == "*.gguf"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_real_quant_tokens_are_preserved():
    for quant in ("Q4_0", "q4_0", "Q4_K_M", "IQ4_XS", "UD-Q4_K_XL", "Q8_0"):
        assert _include({"quant": quant}) == f"*{quant}*", quant


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_other_non_quant_labels_fall_back():
    for label in ("INT4", "QAT", "int8", ""):
        assert _include({"quant": label}) == "*.gguf", repr(label)


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_explicit_source_file_wins():
    # An exact filename from the catalog always takes precedence.
    got = _include({"quant": "QAT-INT4"}, {"file": "model-q4_0.gguf"})
    assert got == "model-q4_0.gguf"
