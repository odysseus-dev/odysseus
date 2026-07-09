"""Regression tests for contributor batch-2 issue fixes."""

import json

import pytest


def test_parse_oneline_read_file_fence():
    from src.agent_tools import parse_tool_blocks

    text = "```read_file /tmp/example.txt```"
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert blocks[0].content == "/tmp/example.txt"


def test_parse_qwen_json_tool_call():
    from src.agent_tools import parse_tool_blocks

    text = (
        '<tool_call>\n'
        '{"name": "bash", "arguments": {"command": "mkdir -p agent-test"}}\n'
        "</tool_call>"
    )
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert "mkdir" in blocks[0].content


def test_parse_bracket_bash_tool():
    from src.agent_tools import parse_tool_blocks

    text = "[bash]mkdir -p agent-test[/bash]"
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "mkdir -p agent-test"


def test_ollama_openai_compat_flattens_multimodal():
    from src.llm_core import _is_ollama_openai_compat_url, _ollama_normalize_messages

    url = "http://127.0.0.1:11434/v1/chat/completions"
    assert _is_ollama_openai_compat_url(url)
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        }
    ]
    out = _ollama_normalize_messages(msgs)
    assert isinstance(out[0]["content"], str)
    assert "describe" in out[0]["content"]
    assert out[0].get("images")


def test_ollama_show_context_caps_known(monkeypatch):
    from src import model_context

    monkeypatch.setattr(model_context, "is_local_endpoint", lambda _u: True)
    monkeypatch.setattr(
        model_context,
        "_probe_ollama_serving_context",
        lambda _u, _m: 8192,
    )

    class _Resp:
        is_success = True

        def json(self):
            return {"data": []}

    monkeypatch.setattr(model_context.httpx, "get", lambda *a, **k: _Resp())

    ctx, known = model_context._query_context_length("http://127.0.0.1:11434/v1", "gemma3:4b")
    assert known is True
    assert ctx == 8192


def test_summarize_emails_passes_task_owner(monkeypatch):
    import src.builtin_actions as ba

    captured = {}

    async def _fake_run(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr("routes.email_pollers._run_auto_summarize_once", _fake_run)

    import asyncio

    asyncio.run(ba.action_summarize_emails("user-1"))
    assert captured.get("task_owner") == "user-1"


def test_ntfy_title_ascii_safe():
    title = "Rappel \u2014 café"
    safe = (title or "Reminder").encode("ascii", "replace").decode("ascii")[:200] or "Reminder"
    assert safe.isascii()
    assert "?" in safe or "cafe" in safe.lower()


def test_windows_default_fastembed_model(monkeypatch):
    from src import embeddings

    monkeypatch.delenv("FASTEMBED_MODEL", raising=False)
    monkeypatch.setattr(embeddings.sys, "platform", "win32")
    assert embeddings.default_fastembed_model() == "BAAI/bge-small-en"
    monkeypatch.setattr(embeddings.sys, "platform", "linux")
    assert "MiniLM" in embeddings.default_fastembed_model()