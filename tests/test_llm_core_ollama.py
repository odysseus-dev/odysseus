"""Regression tests for native Ollama Cloud provider handling."""
import httpx

from src import llm_core


def test_detects_ollama_cloud_native_provider():
    assert llm_core._detect_provider("https://ollama.com/api") == "ollama"
    assert llm_core._detect_provider("https://ollama.com/api/chat") == "ollama"


def test_llm_call_posts_native_ollama_payload(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["json"] = json
        seen["timeout"] = timeout
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"message": {"content": "OK"}, "done": True},
        )

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)

    result = llm_core.llm_call(
        "https://ollama.com/api",
        "gpt-oss:120b-test",
        [{"role": "user", "content": "Say OK"}],
        temperature=0.2,
        max_tokens=7,
        headers={"Authorization": "Bearer ollama-key"},
        timeout=11,
    )

    assert result == "OK"
    assert seen["url"] == "https://ollama.com/api/chat"
    assert seen["headers"]["Authorization"] == "Bearer ollama-key"
    assert seen["json"]["stream"] is False
    assert seen["json"]["options"] == {"temperature": 0.2, "num_predict": 7}


# ---------------------------------------------------------------------------
# Tool-call argument serialization for native Ollama
#
# Odysseus carries assistant tool calls in the OpenAI shape, where
# `function.arguments` is a JSON *string*. Native Ollama /api/chat expects a
# JSON *object* and rejects the string form with HTTP 400 ("Value looks like
# object, but can't find closing '}' symbol"), aborting every follow-up
# (tool-result) round. _build_ollama_payload must parse it back to an object.
# ---------------------------------------------------------------------------

def _assistant_tool_call_msgs():
    """A canonical OpenAI-style assistant tool call + tool result, as produced by
    agent_loop._append_tool_results (arguments are a JSON string)."""
    return [
        {"role": "user", "content": "what do you know about me?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "app_api", "arguments": '{"action": "get_memory"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_0", "content": "Memory: user is James."},
    ]


def test_ollama_payload_parses_string_arguments_to_object():
    payload = llm_core._build_ollama_payload(
        "gpt-oss:120b", _assistant_tool_call_msgs(), temperature=0.0, max_tokens=0,
    )
    asst = payload["messages"][1]
    args = asst["tool_calls"][0]["function"]["arguments"]
    # The whole point: arguments must be a dict, not the JSON string.
    assert args == {"action": "get_memory"}
    assert not isinstance(args, str)
    assert asst["tool_calls"][0]["function"]["name"] == "app_api"
    assert asst["tool_calls"][0]["id"] == "call_0"


def test_ollama_payload_drops_gemini_thought_signature():
    """A cross-provider fallback can hand Ollama a tool call that still carries
    Gemini's opaque extra_content; it is meaningless to Ollama and must not leak."""
    msgs = _assistant_tool_call_msgs()
    msgs[1]["tool_calls"][0]["extra_content"] = {"google": {"thought_signature": "AAAA"}}
    payload = llm_core._build_ollama_payload(
        "gpt-oss:120b", msgs, temperature=0.0, max_tokens=0,
    )
    tc = payload["messages"][1]["tool_calls"][0]
    assert "extra_content" not in tc
    assert tc["function"]["arguments"] == {"action": "get_memory"}


def test_ollama_payload_leaves_plain_messages_untouched():
    msgs = [{"role": "user", "content": "hello"}]
    payload = llm_core._build_ollama_payload("m", msgs, temperature=0.0, max_tokens=0)
    assert payload["messages"][0] == {"role": "user", "content": "hello"}


def test_ollama_payload_tolerates_malformed_arguments():
    msgs = [{
        "role": "assistant",
        "tool_calls": [{"function": {"name": "x", "arguments": "{not json"}}],
    }]
    payload = llm_core._build_ollama_payload("m", msgs, temperature=0.0, max_tokens=0)
    # Falls back to an empty object rather than raising.
    assert payload["messages"][0]["tool_calls"][0]["function"]["arguments"] == {}
