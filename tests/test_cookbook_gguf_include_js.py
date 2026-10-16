"""Pin the Cookbook GGUF download filter (static/js/cookbookGguf.js). Driven
through `node --input-type=module`; skips when `node` is missing.

Regression (#5137): the filter was built as `*<quant>*`, but `hf --include`
matches case-sensitively, so catalog quant "Q4_0" fetched 0 files from
google/gemma-4-12B-it-qat-q4_0-gguf, whose files are named `...q4_0.gguf`.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MOD = _REPO / "static" / "js" / "cookbookGguf.js"
_HAS_NODE = shutil.which("node") is not None

_needs_node = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _call(fn, *args):
    js = f"""
    import {{ {fn} }} from '{_MOD.as_posix()}';
    console.log(JSON.stringify({fn}(...{json.dumps(list(args))})));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@_needs_node
def test_quant_filter_ignores_case():
    assert _call("ggufIncludePattern", {"quant": "Q4_0"}, None) == "*[Qq]4_0*"
    assert _call("ggufIncludePattern", {"quant": "UD-Q4_K_XL"}, None) == "*[Uu][Dd]-[Qq]4_[Kk]_[Xx][Ll]*"


@_needs_node
def test_explicit_source_file_and_missing_quant_unchanged():
    assert _call("ggufIncludePattern", {"quant": "Q4_0"}, {"file": "model-Q8_0.gguf"}) == "model-Q8_0.gguf"
    assert _call("ggufIncludePattern", {}, None) == "*.gguf"


@_needs_node
def test_filter_matches_both_filename_casings_in_hf():
    hf_utils = pytest.importorskip("huggingface_hub.utils")
    pattern = _call("ggufIncludePattern", {"quant": "Q4_0"}, None)
    files = [
        "gemma-4-12b-it-qat-q4_0.gguf",
        "Qwen3-4B-Q4_0.gguf",
        "Qwen3-4B-Q8_0.gguf",
        "README.md",
    ]
    assert list(hf_utils.filter_repo_objects(files, allow_patterns=[pattern])) == files[:2]


@_needs_node
def test_include_text_decodes_filter_for_labels_and_serve_matching():
    assert _call("includeText", "*[Qq]4_0*") == "Q4_0"
    assert _call("includeText", "*[Uu][Dd]-[Qq]4_[Kk]_[Xx][Ll]*") == "UD-Q4_K_XL"
    assert _call("includeText", "BF16/model-*.gguf") == "BF16/model-.gguf"
    assert _call("includeText", None) == ""


def test_manual_download_box_builds_case_insensitive_filters():
    # The Download box's quant picker (`*Q4_0*.gguf`) and `org/repo:tag`
    # split (`*tag*`) live inside cookbook.js, so pin them by source.
    src = (_REPO / "static" / "js" / "cookbook.js").read_text(encoding="utf-8")
    assert "import { caseInsensitiveGlob, includeText } from './cookbookGguf.js';" in src
    assert "return `${prefix}*${caseInsensitiveGlob(quant)}*.gguf`;" in src
    assert "return `*${caseInsensitiveGlob(quant)}*.gguf`;" in src
    assert "include: `*${caseInsensitiveGlob(m[2])}*`" in src
    assert "`*${quant}*.gguf`" not in src
    assert "include: `*${m[2]}*`" not in src
