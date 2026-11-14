"""Explicit zero values must survive the photo tools' default handling."""
import base64
import io
import json

import pytest
from PIL import Image

from src.agent_tools import editor_tools


OMITTED = object()
VALUES = [OMITTED, None, 0, 0.0, "0", 0.25, 1.0]
VALUE_IDS = ["omitted", "null", "zero", "float-zero", "string-zero", "fraction", "one"]


@pytest.fixture
def image_url():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["edit", "generate"])
@pytest.mark.parametrize("value", VALUES, ids=VALUE_IDS)
async def test_placement_preserves_explicit_zero(monkeypatch, image_url, tool_name, value):
    requests = []

    async def post(path, payload, owner, **kwargs):
        requests.append((path, payload, owner))
        if path == "/api/image/denoise":
            return {"image": image_url}
        assert path == "/api/editor/bridge/command"
        return {"ok": True, "completed": True}

    async def generate(content, *, session_id, owner, incognito):
        assert json.loads(content)["prompt"] == "Owned fixture"
        assert (session_id, owner, incognito) == ("bound-editor", "alice", True)
        return {"image_url": image_url}

    monkeypatch.setattr(editor_tools, "_post", post)
    monkeypatch.setattr(editor_tools, "_provider_available", lambda owner: True)
    monkeypatch.setattr("src.ai_interaction.do_generate_image", generate)
    args = {"op": "denoise", "image": image_url, "prompt": "Owned fixture"}
    if value is not OMITTED:
        args["opacity"] = value
    tool = editor_tools.AiEditImageTool() if tool_name == "edit" else editor_tools.GenerateImageLayerTool()
    _, result = await tool.execute(json.dumps(args), {
        "owner": "alice", "editor_session": "bound-editor", "incognito": True,
    })

    assert result["exit_code"] == 0 and result["placed"] is True
    path, payload, owner = requests[-1]
    assert path == "/api/editor/bridge/command" and owner == "alice"
    assert payload["session"] == "bound-editor" and payload["op"] == "layers.placeImage"
    assert payload["params"]["dataUrl"] == image_url
    expected = 100.0 if value is OMITTED or value is None else float(value)
    assert payload["params"]["opacity"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("value", VALUES, ids=VALUE_IDS)
async def test_denoise_preserves_explicit_zero(monkeypatch, image_url, value):
    requests = []

    async def post(path, payload, owner, **kwargs):
        requests.append((path, payload, owner))
        return {"image": image_url}

    monkeypatch.setattr(editor_tools, "_post", post)
    args = {"op": "denoise", "image": image_url, "place": False}
    if value is not OMITTED:
        args["strength"] = value
    _, result = await editor_tools.AiEditImageTool().execute(
        json.dumps(args), {"owner": "alice", "incognito": True},
    )

    assert result["exit_code"] == 0 and "placed" not in result
    assert len(requests) == 1
    path, payload, owner = requests[0]
    assert path == "/api/image/denoise" and owner == "alice"
    assert payload["image"] == image_url
    expected = 0.5 if value is OMITTED or value is None else float(value)
    assert payload["strength"] == expected
