from src.agent_loop import _model_request_capture_enabled, _model_request_snapshot


def test_model_request_capture_is_opt_in(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_CAPTURE_MODEL_REQUESTS", raising=False)
    assert not _model_request_capture_enabled()
    monkeypatch.setenv("ODYSSEUS_CAPTURE_MODEL_REQUESTS", "true")
    assert _model_request_capture_enabled()


def test_model_request_snapshot_has_model_visible_fields_only():
    messages = [{"role": "system", "content": "route carefully"}]
    tools = [{"type": "function", "function": {"name": "manage_notes"}}]
    result = _model_request_snapshot(
        round_num=2,
        model="qwen",
        messages=messages,
        tools=tools,
        temperature=0.2,
        max_tokens=4096,
        prompt_type="agent",
        agent_prompt_mode="qwen_minimal",
    )
    assert result["messages"] is messages
    assert result["tools"] is tools
    assert result["prompt_type"] is None
    assert "headers" not in result
    assert "endpoint" not in result
