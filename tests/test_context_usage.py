"""Tests for src/context_usage.py categorized token breakdown."""

import json

from src.context_usage import compute_context_breakdown
from src.model_context import estimate_tokens


def test_source_messages_are_bucketed():
    messages = [
        {"role": "user", "content": "skill procedure", "metadata": {"source": "skills"}},
        {"role": "user", "content": "fact", "metadata": {"source": "saved memory: user name"}},
        {"role": "user", "content": "doc text", "metadata": {"source": "active editor document"}},
        {"role": "user", "content": "search text", "metadata": {"source": "web search results"}},
    ]
    breakdown = compute_context_breakdown(messages, is_agent=False)
    ids = {c["id"] for c in breakdown["categories"]}

    assert "skills" in ids
    assert "memory" in ids
    assert "documents" in ids
    assert "web" in ids
    assert "conversation" not in ids


def test_summarized_message_bucket():
    messages = [
        {
            "role": "user",
            "content": "[Conversation summary] older messages",
            "metadata": {"compacted": True},
        },
    ]
    breakdown = compute_context_breakdown(messages, is_agent=False)
    ids = {c["id"] for c in breakdown["categories"]}
    assert "summarized_conversation" in ids


def test_agent_system_split():
    system = (
        "You are an AI assistant with tool access.\n\n"
        "```bash\n<shell command>\n```\n\n"
        "## Additional tools\n- ```list_models``` — list models\n\n"
        "## Rules\n- Only use tools when needed.\n"
    )
    messages = [{"role": "system", "content": system}]
    breakdown = compute_context_breakdown(messages, is_agent=True)
    ids = {c["id"] for c in breakdown["categories"]}

    assert "system_prompt" in ids
    assert "tool_definitions" in ids
    assert "rules" in ids

    system_tokens = next(c["tokens"] for c in breakdown["categories"] if c["id"] == "system_prompt")
    assert system_tokens > 0


def test_mcp_block_split_from_system():
    system = (
        "You are an AI assistant.\n\n"
        "You also have access to external MCP tool servers.\n"
        "**GitHub:**\n  - mcp__github__foo: does things\n"
    )
    messages = [{"role": "system", "content": system}]
    breakdown = compute_context_breakdown(messages, is_agent=True)
    ids = {c["id"] for c in breakdown["categories"]}
    assert "mcp" in ids
    assert "system_prompt" in ids


def test_tool_schemas_contribute_to_categories():
    schemas = [
        {"type": "function", "function": {"name": "bash", "description": "Run shell", "parameters": {}}},
        {"type": "function", "function": {"name": "mcp__github__foo", "description": "GH tool", "parameters": {}}},
    ]
    messages = [{"role": "user", "content": "hi"}]
    breakdown = compute_context_breakdown(messages, tool_schemas=schemas, is_agent=False)
    ids = {c["id"] for c in breakdown["categories"]}

    assert "tool_definitions" in ids
    assert "mcp" in ids


def test_zero_categories_omitted_and_total_approximates_estimate():
    messages = [
        {"role": "system", "content": "system prompt text"},
        {"role": "user", "content": "user message"},
        {"role": "assistant", "content": "assistant reply"},
    ]
    schemas = [
        {"type": "function", "function": {"name": "bash", "description": "x" * 100, "parameters": {}}},
    ]
    breakdown = compute_context_breakdown(messages, tool_schemas=schemas, is_agent=False)

    for category in breakdown["categories"]:
        assert category["tokens"] > 0

    schema_payload = json.dumps(schemas, separators=(",", ":"), sort_keys=True)
    schema_tokens = 4 * len(schemas) + int(len(schema_payload) * 0.3)
    estimated_total = estimate_tokens(messages) + schema_tokens
    assert abs(breakdown["total_tokens"] - estimated_total) <= 20
