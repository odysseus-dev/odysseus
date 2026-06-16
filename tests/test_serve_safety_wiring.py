"""Wiring/regression guards for model-serving safety.

The full serve path launches tmux/model processes, so rather than spawn one we
pin (a) the safe settings defaults and (b) that the `/api/model/serve` handler
actually invokes the guard before launching — for real-model serves only — so
the protection can't silently regress. The decision logic itself is covered
behaviorally in test_serve_guard.py.
"""

import ast
from pathlib import Path

import pytest


def test_settings_defaults_are_safe():
    from src.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS.get("max_loaded_models") == 1
    assert DEFAULT_SETTINGS.get("serve_replaces_previous") is True
    assert "serve_vram_headroom_gb" in DEFAULT_SETTINGS


def test_settings_keys_are_user_adjustable_via_manage_settings():
    # do_manage_settings gates set/get on key ∈ DEFAULT_SETTINGS, so presence
    # there is what makes "set max_loaded_models 2" work from chat/API.
    from src.settings import DEFAULT_SETTINGS
    for k in ("max_loaded_models", "serve_replaces_previous", "serve_vram_headroom_gb"):
        assert k in DEFAULT_SETTINGS


def _funcs_by_name(path):
    tree = ast.parse(Path(path).read_text())
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, []).append(node)
    return out


def test_model_serve_invokes_guard_gated_on_pip_install():
    funcs = _funcs_by_name("routes/cookbook_routes.py")
    assert "model_serve" in funcs, "model_serve handler not found"
    body = ast.unparse(funcs["model_serve"][0])
    # The guard runs, and only for real serves (pip installs are exempt).
    assert "_enforce_serve_safety" in body
    assert "is_pip_install" in body


def test_guard_refuses_and_stops_previous():
    funcs = _funcs_by_name("routes/cookbook_routes.py")
    assert "_enforce_serve_safety" in funcs, "guard helper not found"
    body = ast.unparse(funcs["_enforce_serve_safety"][0])
    # Refuses with a 409 and stops the previous serve via the shared kill path.
    assert "HTTPException(409" in body
    assert "_cookbook_kill_session" in body
    assert "decide_serve" in body and "vram_verdict" in body
