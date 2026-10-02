"""Cookbook serve-command preview/normalize round-trip (issue #5979).

Behavioral regression tests for the #5979 launch-command corruption:
`_formatServeCmdPreview` splits the launch command into backslash-continued
display lines; `_normalizeServeCmdForLaunch` collapses it back for the tmux
runner. Two confirmed bugs, both reproduced in Node before the fix: (A) the
normalizer rebuilt the command only when env-assignment lines were present, so
plain multi-line previews round-tripped with continuation backslashes inline
(`llama-server \\ --model ...`), which the shell then mis-parsed into a bogus
` --model` word (llama-server: "error: invalid argument:  --model"); (B) the
tokenizer fused a backslash+space into the following token, so an already
mangled single-line paste re-displayed as `\\ --host` lines and degraded
further every cycle.

Why the shipped defs are extracted instead of imported (documented exception
per tests/TESTING_STANDARD.md behavioral-first policy): the module's import
chain requires a browser DOM (`colorPicker.js` touches `HTMLInputElement` at
import time — observed), so direct Node import is impossible. The three
functions under test are pure (no DOM, no imports) and contiguous in
`static/js/cookbookServe.js`; this file slices them out by stable delimiters
and executes them in Node, then asserts on the executed behavior — the same
pattern as `tests/test_cookbook_stale_shim_recovery.py`, which extracts and
executes shipped shell lines. Node runs from a temp file (not `-e`) so the
shipped chunk is prepended verbatim; `node` must be on PATH (see rule 32:
`~/bin/node` symlink).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JS_SRC = REPO / "static" / "js" / "cookbookServe.js"

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")

_HARNESS = r"""
const __R = {};
const MODEL = '"$(printf %s \'/X/m.gguf\')"';
const CLEAN = 'llama-server --model ' + MODEL + ' --host 0.0.0.0 --port 8000 -ngl 0 -c 40960';
__R.clean_roundtrip = _normalizeServeCmdForLaunch(_formatServeCmdPreview(CLEAN)) === CLEAN;
const MANGLED = 'llama-server \\ --model ' + MODEL + ' \\ --host 0.0.0.0 \\ --port 8000 -ngl 0 -c 40960';
__R.mangled_heals = _normalizeServeCmdForLaunch(_formatServeCmdPreview(MANGLED)) === CLEAN;
__R.stable = _normalizeServeCmdForLaunch(_formatServeCmdPreview(_normalizeServeCmdForLaunch(_formatServeCmdPreview(CLEAN)))) === CLEAN;
const ESC = 'llama-server --model "C:\\models\\m.gguf" --host 0.0.0.0';
__R.escaped_backslash = _normalizeServeCmdForLaunch(_formatServeCmdPreview(ESC)).includes('"C:\\models\\m.gguf"');
const SPC = 'llama-server --model "my model.gguf" --host 0.0.0.0';
__R.quoted_spaces = _normalizeServeCmdForLaunch(_formatServeCmdPreview(SPC)) === SPC;
const PRE = 'MODEL_FILE=$({ find /tmp/x -maxdepth 1 -name m.gguf';
const PRETAIL = 'sort | head -1 }) && llama-server --model "$MODEL_FILE" --host 0.0.0.0';
const PD = _formatServeCmdPreview(PRE + PRETAIL);
const PN = _normalizeServeCmdForLaunch(PD);
__R.prelude = PD.includes('\n&&\n') && PN.includes('&&') && PN.includes('MODEL_FILE=$({') && PN.includes('llama-server') && PN.includes('find /tmp/x');
const ENVIN = 'export FOO=1\nllama-server --model m.gguf --host 0.0.0.0';
__R.env_first = _normalizeServeCmdForLaunch(ENVIN).startsWith('FOO=1');
console.log(JSON.stringify(__R));
"""


def _slice():
    """Slice the three pure preview/normalize functions out of the shipped JS.

    The module import chain requires a browser DOM (`colorPicker.js` touches
    `HTMLInputElement` at import time), so direct Node import is impossible.
    The functions are pure and contiguous; this pins the shipped text by stable
    delimiters (fails loudly if the boundaries move).
    """
    src = JS_SRC.read_text(encoding="utf-8")
    start = src.index("export function _shellSplitForPreview")
    end = src.index("function _modelSizeGb")
    chunk = src[start:end]
    assert "_shellSplitForPreview" in chunk
    assert "_formatServeCmdPreview" in chunk
    assert "_normalizeServeCmdForLaunch" in chunk
    return chunk


def _run(tmp_path):
    """Execute the shipped functions in Node against the scenario harness."""
    target = tmp_path / "ody_serve_extract.mjs"
    target.write_text(_slice(), encoding="utf-8")
    harness_src = target.read_text(encoding="utf-8")
    probe = tmp_path / "ody_probe.mjs"
    probe.write_text(harness_src + "\n" + _HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(probe)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_mangled_single_line_self_heals(tmp_path):
    """#5979 bug B: an inline-continuation paste heals to the launcher form."""
    assert _run(tmp_path)["mangled_heals"] is True


def test_quoted_escapes_and_spaces_preserved(tmp_path):
    """Quoted backslashes/spaces survive the round trip (issue's exact path)."""
    r = _run(tmp_path)
    assert r["escaped_backslash"] is True
    assert r["quoted_spaces"] is True


def test_model_file_prelude_survives(tmp_path):
    """MODEL_FILE=$({ ... }) prelude keeps its && shape through collapse."""
    assert _run(tmp_path)["prelude"] is True


def test_export_assignments_stay_first(tmp_path):
    """Existing env-first reorder behavior is preserved by the rebuild."""
    assert _run(tmp_path)["env_first"] is True


def test_clean_single_round_trip(tmp_path):
    """#5979 bug A: a clean launch command survives display -> normalize."""
    assert _run(tmp_path)["clean_roundtrip"] is True


def test_multi_line_display_collapse(tmp_path):
    """#5979 bug A (display form): multi-line preview collapses losslessly."""
    assert _run(tmp_path)["stable"] is True
