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


# ---------------------------------------------------------------------------
# Multimodal (vision) content shaping for native Ollama (issue #3313)
#
# A vision turn arrives as an OpenAI-style content *array*
# ([{type:text,...}, {type:image_url, image_url:{url:"data:image/png;base64,XXX"}}]).
# Native Ollama /api/chat wants `content` as a plain string with images carried
# in a sibling `images` array of raw base64; given the array it 400s with
# "cannot unmarshal array into Go struct field ChatRequest.messages.content of
# type string", so _build_ollama_payload must flatten it.
# ---------------------------------------------------------------------------

def test_ollama_payload_flattens_multimodal_to_string_and_images():
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "What is in this image?"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,AAAABBBB"}},
        ],
    }]
    payload = llm_core._build_ollama_payload(
        "qwen3-vl", msgs, temperature=0.0, max_tokens=0,
    )
    msg = payload["messages"][0]
    # content must be a plain string, not the OpenAI array.
    assert msg["content"] == "What is in this image?"
    assert not isinstance(msg["content"], list)
    # image moves to a sibling `images` array, data-URI prefix stripped.
    assert msg["images"] == ["AAAABBBB"]


def test_ollama_payload_joins_multiple_text_blocks_keeps_images():
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "first"},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64,IMG"}},
            {"type": "text", "text": "second"},
        ],
    }]
    payload = llm_core._build_ollama_payload("m", msgs, temperature=0.0, max_tokens=0)
    msg = payload["messages"][0]
    assert msg["content"] == "first\nsecond"
    assert msg["images"] == ["IMG"]


def test_ollama_payload_image_only_message_has_empty_content():
    msgs = [{
        "role": "user",
        "content": [
            {"type": "image_url",
             "image_url": {"url": "data:image/webp;base64,ONLYIMG"}},
        ],
    }]
    payload = llm_core._build_ollama_payload("m", msgs, temperature=0.0, max_tokens=0)
    msg = payload["messages"][0]
    assert msg["content"] == ""
    assert msg["images"] == ["ONLYIMG"]


def test_ollama_payload_text_only_array_collapses_without_images_key():
    # A content array carrying only text collapses to a string; no images key is
    # added (Ollama rejects an empty images array on some server versions).
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    payload = llm_core._build_ollama_payload("m", msgs, temperature=0.0, max_tokens=0)
    msg = payload["messages"][0]
    assert msg["content"] == "hi"
    assert "images" not in msg


def test_ollama_payload_tolerates_malformed_arguments():
    msgs = [{
        "role": "assistant",
        "tool_calls": [{"function": {"name": "x", "arguments": "{not json"}}],
    }]
    payload = llm_core._build_ollama_payload("m", msgs, temperature=0.0, max_tokens=0)
    # Falls back to an empty object rather than raising.
    assert payload["messages"][0]["tool_calls"][0]["function"]["arguments"] == {}


# ---------------------------------------------------------------------------
# num_ctx threading (issue #909)
#
# Ollama defaults num_ctx to 2048 when the option is omitted, so prompts
# going to any Ollama backend are silently truncated there regardless of
# the model's actual capability. The builder must accept a discovered
# context length and emit options.num_ctx — but only when the value is
# trusted and larger than 2048.
# ---------------------------------------------------------------------------


def test_build_ollama_payload_emits_num_ctx_when_known_and_large():
    """num_ctx passes through when the caller supplies a trusted value
    larger than Ollama's 2048 default."""
    payload = llm_core._build_ollama_payload(
        "kimi-k2", [{"role": "user", "content": "x"}],
        temperature=0.5, max_tokens=100, num_ctx=131072,
    )
    assert payload["options"]["num_ctx"] == 131072


def test_build_ollama_payload_emits_num_ctx_for_small_known_models():
    """A model with a real context smaller than Ollama's 2048 default
    would OOM if Ollama used its own default. Pass the real value."""
    payload = llm_core._build_ollama_payload(
        "tiny-llm", [{"role": "user", "content": "x"}],
        temperature=0.5, max_tokens=100, num_ctx=1024,
    )
    assert payload["options"]["num_ctx"] == 1024


def test_build_ollama_payload_omits_none_and_zero():
    """None means the caller didn't look it up; 0 is nonsensical.
    Both should be dropped, not emitted as a 0-context request."""
    for ctx in (None, 0):
        payload = llm_core._build_ollama_payload(
            "m", [{"role": "user", "content": "x"}],
            temperature=0.5, max_tokens=100, num_ctx=ctx,
        )
        assert "num_ctx" not in payload.get("options", {}), (
            f"num_ctx={ctx} should not be emitted"
        )


def test_build_ollama_payload_omits_default_context_fallback():
    """get_context_length returns DEFAULT_CONTEXT (128000) when it can't
    discover the model's actual window. Emitting that as num_ctx would
    lie to Ollama for unknown models, so the builder filters it out."""
    from src.model_context import DEFAULT_CONTEXT
    payload = llm_core._build_ollama_payload(
        "unknown-llm-9001", [{"role": "user", "content": "x"}],
        temperature=0.5, max_tokens=100, num_ctx=DEFAULT_CONTEXT,
    )
    assert "num_ctx" not in payload.get("options", {})


def test_llm_call_threads_discovered_num_ctx(monkeypatch):
    """When get_context_length returns a real, large value, it ends up
    in the outgoing Ollama request as options.num_ctx (issue #909)."""
    monkeypatch.setattr(llm_core, "get_context_length",
                        lambda url, model: 32768)

    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(
            200, request=request,
            json={"message": {"content": "OK"}, "done": True},
        )

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)

    llm_core.llm_call(
        "https://ollama.com/api",
        "kimi-k2",
        [{"role": "user", "content": "Say OK"}],
        temperature=0.2,
        max_tokens=7,
    )

    assert seen["json"]["options"]["num_ctx"] == 32768


def test_stream_llm_threads_discovered_num_ctx(monkeypatch):
    """stream_llm goes through the same ollama branch and must also
    pass num_ctx through to the streaming request body."""
    import asyncio

    seen = {}

    def spy_build_ollama_payload(*args, **kwargs):
        seen["num_ctx"] = kwargs.get("num_ctx")
        seen["stream"] = kwargs.get("stream")
        return {
            "model": "kimi-k2",
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
        }

    monkeypatch.setattr(llm_core, "get_context_length",
                        lambda url, model: 32768)
    monkeypatch.setattr(llm_core, "_build_ollama_payload",
                        spy_build_ollama_payload)

    # Short-circuit before the actual HTTP call: host is "dead" → yields
    # an error SSE chunk and returns. The call to _build_ollama_payload
    # still happens before the host check, so we can inspect it.
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: True)

    async def collect():
        return [chunk async for chunk in llm_core.stream_llm(
            "https://ollama.com/api",
            "kimi-k2",
            [{"role": "user", "content": "Say OK"}],
            temperature=0.2,
            max_tokens=7,
        )]

    out = asyncio.run(collect())

    assert seen["num_ctx"] == 32768
    assert seen["stream"] is True
    assert out  # we got the SSE error chunk
