import builtins

import pytest

from src.constants import LOCAL_LLM_ROUTER_AUTO_MODEL_ID
from src.local_llm_router_runtime import LOCAL_LLM_ROUTER_MISSING, load_local_llm_router


def _block_router_import(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("local_llm_router", "split_stack"):
            raise ImportError(f"No module named {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_missing_dependency_error_is_user_actionable(monkeypatch):
    _block_router_import(monkeypatch)
    with pytest.raises(RuntimeError) as exc:
        load_local_llm_router()
    assert str(exc.value) == LOCAL_LLM_ROUTER_MISSING
    assert "Local-LLM-Router" in str(exc.value)


def test_LOCAL_LLM_ROUTER_AUTO_MODEL_ID_constant():
    assert LOCAL_LLM_ROUTER_AUTO_MODEL_ID == "__auto_stack__"
