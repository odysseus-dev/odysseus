"""Regression guards for vLLM serve controls that live in browser-heavy modules.

The Cookbook command builder and Serve panel depend on browser globals, so these
source-level checks pin the important wiring while node --check covers syntax.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COOKBOOK_JS = ROOT / "static/js/cookbook.js"
SERVE_JS = ROOT / "static/js/cookbookServe.js"
RUNNING_JS = ROOT / "static/js/cookbookRunning.js"


def test_vllm_core_serve_flags_wired_through_command_and_edit_paths():
    cookbook = COOKBOOK_JS.read_text(encoding="utf-8")
    serve = SERVE_JS.read_text(encoding="utf-8")
    running = RUNNING_JS.read_text(encoding="utf-8")

    assert "--gpu-memory-utilization" in cookbook
    assert 'data-field="max_seqs"' in serve
    assert "max_seqs: _ex(/--max-num-seqs" in serve
    assert "max_seqs: ex(/--max-num-seqs" in running


def test_vllm_sglang_launch_probes_gpu_before_start():
    serve = SERVE_JS.read_text(encoding="utf-8")

    assert "No GPU detected" in serve
    assert "['vllm', 'sglang'].includes(serveState.backend)" in serve
    assert "/api/cookbook/gpus" in serve
