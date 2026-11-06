"""Sticky prefix: stable tool order, DeepSeek cache key, summary off the system role."""
from core.models import ChatMessage
from src.llm_core import _apply_local_cache_affinity, _stable_sort_tools


def test_tools_sort_by_name():
    tools = [
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "bash"}},
    ]
    names = [item["function"]["name"] for item in _stable_sort_tools(tools)]
    assert names == ["bash", "write_file"]


def test_native_deepseek_gets_the_cache_key():
    payload = {}
    _apply_local_cache_affinity(payload, "https://api.deepseek.com/v1/chat/completions", "sess-1")
    assert payload["user"] == payload["prompt_cache_key"]
    assert payload["user"].startswith("ody_")


def test_openrouter_sticky_id_is_separate_from_a_deepseek_measurement():
    payload = {}
    _apply_local_cache_affinity(payload, "https://openrouter.ai/api/v1/chat/completions", "sess-1")
    assert payload["prompt_cache_key"].startswith("ody_")


def test_summary_message_is_not_a_system_role():
    message = ChatMessage(
        role="user",
        content="[Conversation summary]\nkept",
        metadata={"harness_control": True, "compacted": True},
    )
    dumped = message.to_dict()
    assert dumped["role"] == "user"
    assert dumped["_harness_control"] is True
