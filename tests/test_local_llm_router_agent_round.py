from pathlib import Path


def test_agent_loop_emits_local_llm_router_model_resolved():
    source = Path("src/agent_loop.py").read_text(encoding="utf-8")

    assert "local_llm_router_active" in source
    assert "resolve_local_llm_router" in source
    assert '"local_llm_router": True' in source
    assert "plan_mode" not in source
    assert "plan_mode_disabled_tools" not in source
