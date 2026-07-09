"""Batch 6 contributor regression tests."""
import pytest


def test_openai_model_ids_accepts_bare_together_array():
    """Together /v1/models returns a bare JSON array (issue #2202)."""
    from routes.model_routes import _openai_model_ids

    ids = _openai_model_ids([{"id": "meta-llama/Llama-3-8b"}, {"id": "mistralai/Mixtral-8x7B"}])
    assert "meta-llama/Llama-3-8b" in ids
    assert "mistralai/Mixtral-8x7B" in ids


def test_qwen35_treated_as_vision_capable():
    """Qwen 3.5 family should route images to vision when configured (#761)."""
    from src.chat_helpers import model_supports_vision

    assert model_supports_vision("qwen3.5:27b")
    assert model_supports_vision("qwen3-5-vision")


def test_interactive_gate_wait_loop_omits_browser_block():
    import inspect
    from src import interactive_gate as ig

    src = inspect.getsource(ig.wait_for_interactive_quiet)
    assert "not browser_active" not in src


def test_cookbook_normalize_download_active_not_done():
    """JS heuristic mirrored: active shard output must not display as done."""
    import pathlib

    js = pathlib.Path("static/js/cookbookRunning.js").read_text()
    assert "_downloadOutputLooksActive" in js
    assert "status: 'running'" in js and "_doneConfirmAt: null" in js