"""Explicit quality choices must take precedence over the configured default."""
import asyncio
import json

import pytest
from PIL import Image


@pytest.mark.parametrize("caller", ["json", "lines", "mcp", "edit"])
@pytest.mark.parametrize("requested,configured,expected", [
    ("medium", "high", "medium"),
    ("medium", "low", "medium"),
    ("low", "high", "low"),
    ("high", "low", "high"),
    ("auto", "high", "auto"),
    (None, "high", "high"),
    (None, None, "medium"),
])
def test_image_quality_precedence(monkeypatch, tmp_path, caller, requested, configured, expected):
    from src import ai_interaction, settings
    from mcp_servers.image_gen_server import call_tool

    config = {"image_gen_enabled": True, "image_model": "gpt-image-1"}
    if configured is not None:
        config["image_quality"] = configured
    monkeypatch.setattr(settings, "load_settings", lambda: config)
    monkeypatch.setattr(ai_interaction, "_resolve_model", lambda *args, **kwargs:
                        ("http://fixture.invalid/v1/chat/completions", "gpt-image-1", {}))
    captured = []

    class Response:
        status_code = 418
        text = "Fixture captured request"

        def json(self):
            return {"error": {"message": self.text}}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            captured.append(kwargs.get("json") or kwargs.get("data"))
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", Client)
    args = {"prompt": "Fixture image", "model": "gpt-image-1"}
    if requested is not None:
        args["quality"] = requested
    if caller == "json":
        operation = ai_interaction.do_generate_image(json.dumps(args), incognito=True)
    elif caller == "lines":
        content = "Fixture image\ngpt-image-1\n1024x1024"
        if requested is not None:
            content += "\n" + requested
        operation = ai_interaction.do_generate_image(content, incognito=True)
    elif caller == "mcp":
        operation = call_tool("generate_image", args)
    else:
        source = tmp_path / "fixture.png"
        Image.new("RGB", (2, 2), "white").save(source)
        kwargs = {} if requested is None else {"quality": requested}
        operation = ai_interaction.do_edit_image("Fixture image", str(source), size="1024x1024",
                                               incognito=True, **kwargs)
    result = asyncio.run(operation)
    assert "418" in str(result), result
    assert len(captured) == 1
    assert captured[0]["quality"] == expected
