"""Regression guards for vLLM serve controls that live in browser-heavy modules.

The Cookbook command builder and Serve panel depend on browser globals, so these
source-level checks pin the important wiring while node --check covers syntax.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COOKBOOK_JS = ROOT / "static/js/cookbook.js"
SERVE_JS = ROOT / "static/js/cookbookServe.js"
RUNNING_JS = ROOT / "static/js/cookbookRunning.js"


def test_vllm_max_num_batched_tokens_is_wired_through_command_and_edit_paths():
    cookbook = COOKBOOK_JS.read_text(encoding="utf-8")
    serve = SERVE_JS.read_text(encoding="utf-8")
    running = RUNNING_JS.read_text(encoding="utf-8")

    assert "--max-num-batched-tokens" in cookbook
    assert 'data-field="max_batched_tokens"' in serve
    assert "max_batched_tokens: _ex(/--max-num-batched-tokens" in serve
    assert "max_batched_tokens: ex(/--max-num-batched-tokens" in running


def test_vllm_sglang_launch_warns_when_selected_gpus_are_below_memory_target():
    serve = SERVE_JS.read_text(encoding="utf-8")

    assert "GPU memory already in use" in serve
    assert "free / total < _gpuTarget" in serve
    assert "['vllm', 'sglang'].includes(serveState.backend)" in serve
