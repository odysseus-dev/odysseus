from pathlib import Path


CHAT_ROUTES = Path(__file__).resolve().parents[1] / "routes" / "chat_routes.py"


def _source() -> str:
    return CHAT_ROUTES.read_text(encoding="utf-8")


def test_research_fast_path_respects_tool_policy():
    src = _source()
    assert "pre_context_tool_policy = build_effective_tool_policy(" in src
    assert "allow_tool_preprocessing = not pre_context_tool_policy.block_all_tool_calls" in src
    assert "allow_tool_preprocessing=allow_tool_preprocessing" in src
    assert "research_blocked_by_policy = bool(" in src
    assert 'tool_policy.blocks("trigger_research")' in src
    assert 'tool_policy.blocks("manage_research")' in src
    assert 'effective_do_research = bool(' in src
    assert 'if effective_do_research:' in src
    assert '"is_research": effective_do_research' in src
    assert "_effective_mode = 'research' if effective_do_research else (chat_mode or 'chat')" in src
    assert '_model_suffix = "Research" if effective_do_research else None' in src
    assert "do_research=effective_do_research" in src


def test_non_streaming_chat_path_uses_tool_policy_before_context_and_research():
    src = _source()
    chat_endpoint = src[src.index("async def chat_endpoint"):src.index("# ------------------------------------------------------------------ #", src.index("async def chat_endpoint"))]
    assert "tool_policy = build_effective_tool_policy(last_user_message=message)" in chat_endpoint
    assert "allow_tool_preprocessing = not tool_policy.block_all_tool_calls" in chat_endpoint
    assert 'if not tool_policy.blocks("manage_memory"):' in chat_endpoint
    assert "allow_tool_preprocessing=allow_tool_preprocessing" in chat_endpoint
    assert 'tool_policy.blocks("trigger_research")' in chat_endpoint
    assert "if use_research and not research_blocked_by_policy:" in chat_endpoint
    assert "allow_background_extraction=not tool_policy.block_all_tool_calls" in chat_endpoint


def test_image_generation_fast_path_checks_policy_before_tool_start():
    src = _source()
    policy_gate = src.index('if tool_policy.blocks("generate_image"):')
    tool_start = src.index('"type": "tool_start", "tool": "generate_image"')
    generator_call = src.index("do_generate_image(")
    assert policy_gate < tool_start
    assert policy_gate < generator_call


def test_streaming_chat_paths_disable_background_extraction_under_policy():
    src = _source()
    assert src.count("allow_background_extraction=not tool_policy.block_all_tool_calls") >= 3
