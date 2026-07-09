"""Batch 6 contributor regression tests."""
import inspect
from pathlib import Path

import pytest


def test_openai_model_ids_accepts_bare_together_array():
    from routes.model_routes import _openai_model_ids

    ids = _openai_model_ids([{"id": "meta-llama/Llama-3-8b"}, {"id": "mistralai/Mixtral-8x7B"}])
    assert "meta-llama/Llama-3-8b" in ids
    assert "mistralai/Mixtral-8x7B" in ids


def test_interactive_gate_wait_loop_omits_browser_block():
    from src import interactive_gate as ig

    src = inspect.getsource(ig.wait_for_interactive_quiet)
    assert "not browser_active" not in src


def test_cookbook_normalize_download_active_not_done():
    """JS heuristic mirrored: active shard output must not display as done."""
    js = Path("static/js/cookbookRunning.js").read_text(encoding="utf-8")
    assert "_downloadOutputLooksActive" in js
    assert "status: 'running'" in js and "_doneConfirmAt: null" in js


def test_local_agent_includes_mcp_schemas_when_present():
    from src import agent_loop

    src = inspect.getsource(agent_loop.stream_agent_loop)
    assert "all_tool_schemas = mcp_schemas if mcp_schemas else []" in src


def test_imap_sig_learner_uses_uid_commands():
    from src import builtin_actions

    src = inspect.getsource(builtin_actions.action_learn_sender_signatures)
    assert 'conn.uid("SEARCH"' in src
    assert 'conn.uid(\n                            "FETCH"' in src or 'conn.uid("FETCH"' in src
    assert "conn.search(" not in src
    assert "conn.fetch(" not in src


def test_cookbook_install_routes_exempt_from_hard_timeout():
    app = Path(__file__).resolve().parent.parent.joinpath("app.py").read_text(encoding="utf-8")
    assert '"/api/cookbook/packages/install"' in app
    assert '"/api/cookbook/install-system-deps"' in app