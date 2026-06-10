from src.local_llm_router_routing import is_local_llm_router_auto_model, is_local_llm_router_auto_session
from src.constants import LOCAL_LLM_ROUTER_AUTO_MODEL_ID


def test_local_llm_router_model_constant():
    assert LOCAL_LLM_ROUTER_AUTO_MODEL_ID == "__auto_stack__"
    assert is_local_llm_router_auto_model(LOCAL_LLM_ROUTER_AUTO_MODEL_ID)


def test_local_llm_router_session_by_model_only():
    class Sess:
        model = LOCAL_LLM_ROUTER_AUTO_MODEL_ID

    assert is_local_llm_router_auto_session(Sess()) is True
    assert is_local_llm_router_auto_session(Sess(), require_enabled=True) is True
