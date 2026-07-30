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


def test_copilot_chat_tools_force_gpt5_reasoning_effort_none():
    # GitHub Copilot proxies OpenAI's GPT-5.x models through its own
    # /chat/completions endpoint and 400s on reasoning_effort + tools the
    # same way api.openai.com does — the guard must also cover it.
    for model in ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5"]:
        payload = {"tools": [_tool()], "reasoning_effort": "medium"}

        llm_core._scrub_openai_chat_tool_reasoning(
            payload,
            "https://api.githubcopilot.com/chat/completions",
            model,
        )

        assert payload["reasoning_effort"] == "none"


def test_copilot_enterprise_chat_tools_force_gpt5_reasoning_effort_none():
    payload = {"tools": [_tool()], "reasoning_effort": "medium"}

    llm_core._scrub_openai_chat_tool_reasoning(
        payload,
        "https://copilot-api.example-enterprise.com/chat/completions",
        "gpt-5.6-sol",
    )

    assert payload["reasoning_effort"] == "none"
