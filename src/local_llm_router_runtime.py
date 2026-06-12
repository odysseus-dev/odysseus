"""Lazy-load the local-llm-router PyPI package."""

from __future__ import annotations

from src.constants import LOCAL_LLM_ROUTER_NAME

LOCAL_LLM_ROUTER_PIP = "local-llm-router[ollama]"
LOCAL_LLM_ROUTER_MISSING = (
    f"{LOCAL_LLM_ROUTER_NAME} is not installed. "
    f"Install with `pip install '{LOCAL_LLM_ROUTER_PIP}'` or use Install in the model picker."
)


def load_local_llm_router():
    """Return the local_llm_router module, or raise a user-facing setup hint."""
    for mod_name in ("local_llm_router", "split_stack"):
        try:
            return __import__(mod_name)
        except ImportError:
            continue
    raise RuntimeError(LOCAL_LLM_ROUTER_MISSING)


def local_llm_router_available() -> bool:
    try:
        load_local_llm_router()
        return True
    except RuntimeError:
        return False
