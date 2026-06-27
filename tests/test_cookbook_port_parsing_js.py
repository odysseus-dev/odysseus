"""Behavioral tests for Cookbook port parsing / picking (#4507 follow-up).

Driven through `node --input-type=module` (same approach as the other
*_js.py tests); skips when `node` is not installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "cookbookPorts.js"
_HAS_NODE = shutil.which("node") is not None


def _run(expr):
    js = (
        f"import {{ portOf, nextFreePort }} from '{_HELPER.as_posix()}';"
        f"console.log(JSON.stringify({expr}));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_port_of_handles_all_forms():
    assert _run("portOf('vllm serve m --host 0.0.0.0 --port 8000')") == "8000"
    assert _run("portOf('x --port=8001')") == "8001"
    assert _run("portOf('llama-server -p 8002')") == "8002"
    assert _run("portOf('llama-server -p=8003')") == "8003"
    assert _run("portOf('serve with no port flag')") == ""


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_next_free_port_skips_taken_including_eq_and_short_flag():
    # a --port= serve and a -p serve are both 'taken'; picker skips them
    taken = "[portOf('a --port=8000'), portOf('b -p 8001')]"
    assert _run(f"nextFreePort({taken})") == "8002"
    assert _run("nextFreePort([])") == "8000"
    assert _run("nextFreePort(['8000', '8002'])") == "8001"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_clash_outcome_same_port_flagged_different_ignored():
    # the guard's predicate is portOf(cmd) === target
    assert _run("portOf('m --port 8000') === '8000'") is True
    assert _run("portOf('m --port 8001') === '8000'") is False
