from src import agent_loop as al


def test_compact_prompt_enabled_for_api_models():
    assert al._should_use_compact_prompt(
        is_api_model=True,
        model="gpt-4.1",
        endpoint_url="https://api.openai.com/v1",
    )


def test_compact_prompt_enabled_for_small_context_local(monkeypatch):
    monkeypatch.setattr(al, "budget_context_for_model", lambda *_a, **_k: 8192)
    assert al._should_use_compact_prompt(
        is_api_model=False,
        model="qwen2.5:7b",
        endpoint_url="http://localhost:11434/api/chat",
    )


def test_compact_prompt_disabled_for_large_context_local(monkeypatch):
    monkeypatch.setattr(al, "budget_context_for_model", lambda *_a, **_k: 131072)
    assert not al._should_use_compact_prompt(
        is_api_model=False,
        model="llama-3.1",
        endpoint_url="http://localhost:11434/api/chat",
    )
