"""Cloudflare Workers AI rejects assistant messages with content=null (HTTP 400
"Type mismatch of '/messages/N/content', 'string' not in 'null'"), which broke the
Cloudflare fallback on every agent round after a tool call. Only Cloudflare gets
content="" — other providers keep the spec-correct null."""
from src.llm_core import _adapt_messages_for_url, _sanitize_llm_messages, _format_upstream_error

TC = [{"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]
MSGS = [
    {"role": "user", "content": "find"},
    {"role": "assistant", "content": None, "tool_calls": TC},
    {"role": "tool", "tool_call_id": "c1", "content": "result"},
]
CF = "https://api.cloudflare.com/client/v4/accounts/abc/ai/v1"


def test_cloudflare_gets_empty_string_content():
    out = _adapt_messages_for_url(CF, _sanitize_llm_messages(MSGS))
    assistant = [m for m in out if m["role"] == "assistant"][0]
    assert assistant["content"] == ""
    assert assistant["tool_calls"] == TC


def test_other_providers_keep_null_content():
    for url in ("https://api.novita.ai/v3/openai", "https://router.huggingface.co/v1", "https://generativelanguage.googleapis.com/v1beta/openai"):
        out = _adapt_messages_for_url(url, _sanitize_llm_messages(MSGS))
        assistant = [m for m in out if m["role"] == "assistant"][0]
        assert assistant["content"] is None, url


def test_input_not_mutated():
    src = _sanitize_llm_messages(MSGS)
    _adapt_messages_for_url(CF, src)
    assert [m for m in src if m["role"] == "assistant"][0]["content"] is None


def test_cloudflare_error_detail_extracted():
    body = '{"errors":[{"message":"AiError: Bad input: oneOf not met","code":5006}],"success":false}'
    msg = _format_upstream_error(400, body, CF + "/chat/completions")
    assert "AiError: Bad input" in msg
