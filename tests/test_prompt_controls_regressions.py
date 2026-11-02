from src import agent_loop, llm_core
from src.agent_tools import admin_tools
from src import settings
from routes.chat_routes import _resolve_prompt_thinking_mode


def test_kimi_thinking_off_translates_to_provider_payload():
    assert llm_core._detect_provider("https://api.kimi.com/coding/v1") == "kimi-code"
    payload = {}
    llm_core._apply_hosted_thinking_mode(
        payload, "kimi-code", "kimi-for-coding", "off"
    )
    assert payload["thinking"] == {"type": "disabled"}


def test_kimi_thinking_default_does_not_add_provider_field():
    payload = {}
    llm_core._apply_hosted_thinking_mode(
        payload, "kimi-code", "kimi-for-coding", None
    )
    assert "thinking" not in payload


def test_openrouter_thinking_off_uses_unified_reasoning_control():
    payload = {}
    llm_core._apply_hosted_thinking_mode(
        payload, "openrouter", "moonshotai/kimi-k3", "off"
    )
    assert payload["reasoning"] == {"effort": "none"}


def test_openrouter_does_not_disable_mandatory_grok_45_reasoning():
    payload = {"reasoning": {"effort": "none"}}
    llm_core._apply_hosted_thinking_mode(
        payload, "openrouter", "x-ai/grok-4.5", "off"
    )
    assert "reasoning" not in payload


def test_stream_transport_only_keeps_required_protocol_reasoning_when_off():
    import ast
    source = open(llm_core.__file__, encoding="utf-8").read()
    condition = next(node.test for node in ast.walk(ast.parse(source))
                     if isinstance(node, ast.If) and isinstance(node.test, ast.BoolOp)
                     and isinstance(node.test.values[0], ast.Name)
                     and node.test.values[0].id == "reasoning")
    expression = compile(ast.Expression(condition), "reasoning-policy", "eval")
    for model, mode, expected in [("generic", "off", False), ("generic", "on", True),
                                  ("deepseek-v4", "off", True)]:
        assert bool(eval(expression, {"reasoning": "analysis", "thinking_mode": mode,
                                      "model": model, "_normalize_thinking_mode": llm_core._normalize_thinking_mode})) is expected


def test_response_cache_is_partitioned_by_thinking_mode():
    args = ("https://api.kimi.com/coding/v1", "kimi-for-coding", [{"role": "user", "content": "Hi"}], 1.0, 100)
    assert llm_core._get_cache_key(*args, thinking_mode="off") != llm_core._get_cache_key(*args, thinking_mode="on")


def test_chat_backend_reads_thinking_mode_from_active_prompt_preset():
    manager = type("PresetManager", (), {"presets": {"custom": {"enabled": True, "thinking_mode": "off"}}})()
    assert _resolve_prompt_thinking_mode(None, "custom", manager) == "off"
    assert _resolve_prompt_thinking_mode("on", "custom", manager) == "on"


def test_email_writing_style_is_agent_manageable_and_routable():
    assert settings.DEFAULT_SETTINGS["email_writing_style"] == ""
    assert settings.DEFAULT_SETTINGS["document_writing_style"] == ""
    source = open(admin_tools.__file__, encoding="utf-8").read()
    assert '"writing style": "document_writing_style"' in source
    assert '"email writing style": "email_writing_style"' in source
    assert any(
        "writing style" in triggers and "manage_settings" in tools
        for triggers, tools in agent_loop._QWEN38_ROUTER_KEYWORD_TOOLS
    )
    index_source = open("src/tool_index.py", encoding="utf-8").read()
    assert '"writing style", "reply style", "email writing style"' in index_source
