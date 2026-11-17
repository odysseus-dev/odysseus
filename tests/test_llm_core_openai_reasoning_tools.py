from src import llm_core


def _tool():
    return {
        "type": "function",
        "function": {
            "name": "search",
            "description": "search",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_openai_chat_tools_force_gpt5_reasoning_effort_none():
    payload = {"tools": [_tool()], "reasoning_effort": "high"}

    llm_core._scrub_openai_chat_tool_reasoning(
        payload,
        "https://api.openai.com/v1/chat/completions",
        "gpt-5.6-luna",
    )

    assert payload["reasoning_effort"] == "none"


def test_openai_chat_tools_match_gpt5_variants():
    for model in ["gpt-5", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "openai/gpt-5.6-luna"]:
        payload = {"tools": [_tool()], "reasoning_effort": "medium"}

        llm_core._scrub_openai_chat_tool_reasoning(
            payload,
            "https://api.openai.com/v1/chat/completions",
            model,
        )

        assert payload["reasoning_effort"] == "none"


def test_openai_chat_no_tools_leaves_reasoning_effort_unchanged():
    payload = {"reasoning_effort": "high"}

    llm_core._scrub_openai_chat_tool_reasoning(
        payload,
        "https://api.openai.com/v1/chat/completions",
        "gpt-5.6-luna",
    )

    assert payload["reasoning_effort"] == "high"


def test_non_openai_host_leaves_reasoning_effort_unchanged():
    payload = {"tools": [_tool()], "reasoning_effort": "high"}

    llm_core._scrub_openai_chat_tool_reasoning(
        payload,
        "https://openrouter.ai/api/v1/chat/completions",
        "openai/gpt-5.6-luna",
    )

    assert payload["reasoning_effort"] == "high"


def test_non_gpt5_model_leaves_reasoning_effort_unchanged():
    payload = {"tools": [_tool()], "reasoning_effort": "high"}

    llm_core._scrub_openai_chat_tool_reasoning(
        payload,
        "https://api.openai.com/v1/chat/completions",
        "gpt-4.1",
    )

    assert payload["reasoning_effort"] == "high"


def test_deepseek_v4_reasoning_defaults_are_explicit_and_bounded():
    payload = {}

    llm_core._apply_deepseek_v4_reasoning_defaults(
        payload,
        "https://api.deepseek.com/v1/chat/completions",
        "deepseek-v4-pro",
    )

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"


def test_deepseek_reasoning_defaults_do_not_affect_other_hosts():
    payload = {}

    llm_core._apply_deepseek_v4_reasoning_defaults(
        payload,
        "https://openrouter.ai/api/v1/chat/completions",
        "deepseek-v4-pro",
    )

    assert payload == {}


def test_deepseek_v4_thinking_off_overrides_reasoning_defaults():
    payload = {}

    llm_core._apply_deepseek_v4_reasoning_defaults(
        payload,
        "https://api.deepseek.com/v1/chat/completions",
        "deepseek-v4-pro",
        "off",
    )

    assert payload["thinking"] == {"type": "disabled"}
    assert payload["reasoning_effort"] == "none"
