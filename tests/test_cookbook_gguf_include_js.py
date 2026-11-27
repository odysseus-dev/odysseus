"""Cookbook GGUF quant include globs must not pull sibling quants (#7294).

The globs are built in JS and matched by huggingface_hub with fnmatch, so
the JS helpers run through `node --input-type=module` and the result is
checked with Python's fnmatch. Skips when `node` is not installed.
"""
import fnmatch
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "cookbookGgufInclude.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node binary not on PATH")

_FILES = [
    "Qwen2.5-Coder-7B-Instruct-Q6_K.gguf",
    "Qwen2.5-Coder-7B-Instruct-Q6_K_L.gguf",
    "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
    "Qwen2.5-Coder-7B-Instruct-Q4_K_S.gguf",
    "Qwen2.5-Coder-7B-Instruct-Q8_0.gguf",
    "Qwen2.5-Coder-7B-Instruct-Q8_0_L.gguf",
    "Q5_K_M/Big-Q5_K_M-00001-of-00002.gguf",
    "Q5_K_M/Big-Q5_K_M-00002-of-00002.gguf",
    "Q5_K_M_L/Big-Q5_K_M_L-00001-of-00002.gguf",
]


def _run(expr):
    js = (
        "import { quantTagInclude, quantFileInclude, includeToRegExp, includeDisplayText } "
        f"from '{_HELPER.as_posix()}';"
        f"console.log(JSON.stringify({expr}));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def _hf_matches(pattern):
    # huggingface_hub.utils.filter_repo_objects uses fnmatch on repo paths.
    return [f for f in _FILES if fnmatch.fnmatch(f, pattern)]


@pytest.mark.parametrize("quant, expected", [
    ("Q6_K", ["Qwen2.5-Coder-7B-Instruct-Q6_K.gguf"]),
    ("Q8_0", ["Qwen2.5-Coder-7B-Instruct-Q8_0.gguf"]),
    ("Q4_K_M", ["Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"]),
    ("Q4_K", []),
    ("Q5_K_M", ["Q5_K_M/Big-Q5_K_M-00001-of-00002.gguf", "Q5_K_M/Big-Q5_K_M-00002-of-00002.gguf"]),
])
def test_tag_include_skips_sibling_quants(quant, expected):
    assert _hf_matches(_run(f"quantTagInclude({json.dumps(quant)})")) == expected


def test_picker_include_skips_sibling_quants():
    assert _hf_matches(_run("quantFileInclude('Q6_K')")) == ["Qwen2.5-Coder-7B-Instruct-Q6_K.gguf"]
    assert _hf_matches(_run("quantFileInclude('Q5_K_M', 'Q5_K_M/')")) == [
        "Q5_K_M/Big-Q5_K_M-00001-of-00002.gguf", "Q5_K_M/Big-Q5_K_M-00002-of-00002.gguf",
    ]


def test_regexp_agrees_with_fnmatch():
    for glob in ("*Q6_K[-.]*", "*Q6_K[-.]*gguf", "Q5_K_M/*Q5_K_M[-.]*gguf", "*Q6_K*.gguf", "*.gguf"):
        got = _run(
            f"{json.dumps(_FILES)}.filter(f => includeToRegExp({json.dumps(glob)}).test(f))"
        )
        assert got == _hf_matches(glob), glob


def test_regexp_matches_under_extra_leading_folders():
    assert _run("includeToRegExp('Q5_K_M/*Q5_K_M[-.]*gguf').test('snap/Q5_K_M/Big-Q5_K_M-00001-of-00002.gguf')") is True
    assert _run("includeToRegExp('*Q6_K[-.]*').test('x/Model-Q6_K_L.gguf')") is False


def test_display_text_keeps_quant_readable():
    assert _run("includeDisplayText('*Q6_K[-.]*')") == "Q6_K."
    assert _run("includeDisplayText('*Q6_K[-.]*gguf')") == "Q6_K.gguf"
    assert _run("includeDisplayText('*Q6_K*.gguf')") == "Q6_K.gguf"
