"""Targeted tests for MCP image generation delegation and error sanitization."""

import asyncio

import pytest

pytest.importorskip("mcp")

import mcp_servers.image_gen_server as igs


def _run(monkeypatch, arguments, *, fake_generate):
    monkeypatch.setattr("src.settings.get_setting", lambda k, d=True: d)
    monkeypatch.setattr("src.ai_interaction.do_generate_image", fake_generate)
    return asyncio.run(igs.call_tool("generate_image", arguments))


def test_delegates_to_do_generate_image(monkeypatch):
    captured = {}

    async def fake(content, owner=None, **kwargs):
        captured["content"] = content
        captured["owner"] = owner
        return {
            "image_url": "/api/generated-image/test.png",
            "image_model": "qwen-image",
            "image_size": "512x512",
        }

    out = _run(
        monkeypatch,
        {"prompt": "a serene lake", "size": "512x512"},
        fake_generate=fake,
    )

    assert captured["owner"] is None
    lines = captured["content"].split("\n")
    assert lines == ["a serene lake", "", "512x512", "medium"]
    assert "/api/generated-image/test.png" in out[0].text


def test_multiline_prompt_normalized(monkeypatch):
    captured = {}

    async def fake(content, owner=None, **kwargs):
        captured["content"] = content
        return {"image_url": "/api/generated-image/test.png", "image_model": "m", "image_size": "1024x1024"}

    _run(
        monkeypatch,
        {"prompt": "line one\nline two", "model": "flux-comfy", "size": "768x768", "quality": "high"},
        fake_generate=fake,
    )

    lines = captured["content"].split("\n")
    assert lines == ["line one line two", "flux-comfy", "768x768", "high"]


def test_exception_path_hides_raw_error(monkeypatch):
    async def boom(content, owner=None, **kwargs):
        raise RuntimeError("connect failed http://10.0.0.1:8188 /Users/me/secret-key sk-abc123")

    out = _run(monkeypatch, {"prompt": "a cat"}, fake_generate=boom)

    text = out[0].text
    assert text == f"Error: {igs._GENERIC_MCP_ERROR}"
    assert "10.0.0.1" not in text
    assert "/Users/me" not in text
    assert "sk-abc123" not in text


def test_unsafe_provider_error_is_generic(monkeypatch):
    async def fake(content, owner=None, **kwargs):
        return {"error": "Image generation failed (502): upstream http://evil/api body"}

    out = _run(monkeypatch, {"prompt": "a dog"}, fake_generate=fake)

    assert out[0].text == f"Error: {igs._GENERIC_MCP_ERROR}"
    assert "evil" not in out[0].text


def test_safe_provider_error_is_preserved(monkeypatch):
    async def fake(content, owner=None, **kwargs):
        return {"error": "No endpoint found with image model 'missing'. Configure an OpenAI-compatible endpoint."}

    out = _run(monkeypatch, {"prompt": "a bird"}, fake_generate=fake)

    assert "No endpoint found with image model" in out[0].text
    assert out[0].text.startswith("Error: ")


def test_degraded_results_preserved(monkeypatch):
    degraded = (
        "Image generation is available as a tool, but no image model "
        "is currently configured."
    )

    async def fake(content, owner=None, **kwargs):
        return {"results": degraded, "image_url": None, "available": False}

    out = _run(monkeypatch, {"prompt": "a fox"}, fake_generate=fake)

    assert degraded in out[0].text
    assert not out[0].text.startswith("Error:")


def test_build_image_content_empty_model_line():
    content = igs._build_image_content("sunset", size="512x512", quality="medium")
    assert content.split("\n") == ["sunset", "", "512x512", "medium"]
