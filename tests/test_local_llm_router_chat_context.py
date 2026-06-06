from types import SimpleNamespace

from src.local_llm_router_routing import is_local_llm_router_auto_model


def test_local_llm_router_skips_normalization_path():
    sess = SimpleNamespace(model="__auto_stack__", endpoint_url="http://127.0.0.1:11434")
    assert is_local_llm_router_auto_model(sess.model)
