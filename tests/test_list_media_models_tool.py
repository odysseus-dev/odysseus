"""Tests for the list_media_models agent tool (Slice 4A).

Covers registration across the tool surfaces (schema, tags, native-call arg
mapping, name aliases, RAG index), the owner-aware dispatch wiring, and the
implementation's contract: enabled models are returned without leaking
endpointUrl / workflowPath, and the shared degraded-state message is returned
when nothing is configured. No ComfyUI generation is involved.
"""

import asyncio
from pathlib import Path

# Import the agent_tools facade first: src.tool_schemas imports from it, and
# importing tool_schemas standalone before the facade triggers a circular
# import (see tests/test_unknown_tool_calls.py for the same ordering).
import src.agent_tools  # noqa: F401
from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS
from src.agent_tools import media_tools as mt
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block
from src.tool_parsing import _TOOL_NAME_MAP
from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS, ToolIndex
import src.settings as settings_mod


def _image_model(model_id="qwen-image-comfy", **over):
    base = {
        "id": model_id,
        "label": "Qwen-Image",
        "provider": "comfyui",
        "kind": "image",
        "capabilities": ["text-to-image", "image-edit"],
        "endpointUrl": "http://localhost:8188",
        "workflowPath": "/local/workflows/qwen.json",
        "enabled": True,
    }
    base.update(over)
    return base


def _patch_settings(monkeypatch, models, **extra):
    cfg = {
        "media_models": models,
        "default_image_media_model": "",
        "comfyui_endpoint_url": "",
    }
    cfg.update(extra)
    monkeypatch.setattr(settings_mod, "load_settings", lambda: cfg)


# ── Registration surfaces ──

def test_schema_registered_with_kind_param():
    schema = next(
        (s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == "list_media_models"),
        None,
    )
    assert schema is not None, "list_media_models missing from FUNCTION_TOOL_SCHEMAS"
    props = schema["function"]["parameters"]["properties"]
    assert "kind" in props
    assert props["kind"]["enum"] == ["image", "video"]


def test_tool_tag_registered():
    assert "list_media_models" in TOOL_TAGS


def test_list_media_models_registered_in_tool_handlers():
    assert "list_media_models" in TOOL_HANDLERS


def test_native_call_maps_kind_to_content():
    block = function_call_to_tool_block("list_media_models", '{"kind": "image"}')
    assert block is not None
    assert block.tool_type == "list_media_models"
    assert block.content == "image"


def test_native_call_no_kind_defaults_empty():
    block = function_call_to_tool_block("list_media_models", "{}")
    assert block is not None
    assert block.tool_type == "list_media_models"
    assert block.content == ""


def test_name_map_aliases_resolve():
    for alias in ("list_media_models", "media_models", "list_image_models"):
        assert _TOOL_NAME_MAP[alias] == "list_media_models"


def test_tool_index_description_present():
    assert "list_media_models" in BUILTIN_TOOL_DESCRIPTIONS


def test_keyword_hint_capability_queries_surface_discovery_only():
    from src.tool_index import IMAGE_CAPABILITY_FORBIDDEN_TOOLS

    ti = ToolIndex.__new__(ToolIndex)
    ti.retrieve = lambda query, k=8: []
    for q in (
        "Can you make images?",
        "Can you draw?",
        "Do you support image generation?",
        "Can you generate pictures?",
    ):
        tools = ti.get_tools_for_query(q)
        assert "list_media_models" in tools, q
        leaked = IMAGE_CAPABILITY_FORBIDDEN_TOOLS & tools
        assert not leaked, f"{q} leaked {sorted(leaked)}"


def test_keyword_hint_creation_intent_surfaces_both_tools():
    ti = ToolIndex.__new__(ToolIndex)
    ti.retrieve = lambda query, k=8: []
    tools = ti.get_tools_for_query("generate an image of a mountain at sunset")
    assert "list_media_models" in tools
    assert "generate_image" in tools


# ── Implementation contract ──

async def test_returns_enabled_models_without_leaking_paths(monkeypatch):
    _patch_settings(
        monkeypatch,
        [_image_model(isDefault=True), _image_model("second", isDefault=False)],
    )
    result = await mt.list_media_models("", owner="alice")

    assert result["available"] is True
    assert result["default_model_id"] == "qwen-image-comfy"
    ids = [m["id"] for m in result["models"]]
    assert ids == ["qwen-image-comfy", "second"]
    for m in result["models"]:
        assert "endpointUrl" not in m
        assert "workflowPath" not in m
    assert result["models"][0]["isDefault"] is True
    assert result["models"][1]["isDefault"] is False


async def test_returns_enabled_models_text_has_no_url(monkeypatch):
    _patch_settings(monkeypatch, [_image_model()])
    result = await mt.list_media_models("", owner="alice")
    assert "8188" not in result["results"]
    assert "/local/workflows" not in result["results"]


async def test_degraded_when_no_models(monkeypatch):
    _patch_settings(monkeypatch, [])
    result = await mt.list_media_models("", owner="alice")

    assert result["available"] is False
    assert result["status"] == "no_models"
    assert result["models"] == []
    assert "no image model" in result["results"].lower()
    assert "Next steps:" in result["results"]
    assert "8188" not in result["results"]
    assert "://" not in result["results"]
    assert "Configure a local ComfyUI endpoint in settings." in result["results"]


async def test_disabled_models_excluded(monkeypatch):
    _patch_settings(monkeypatch, [_image_model("on", enabled=True),
                                  _image_model("off", enabled=False)])
    result = await mt.list_media_models("", owner="alice")
    assert [m["id"] for m in result["models"]] == ["on"]


# ── Registry dispatch wiring ──

def test_list_media_models_threads_owner_and_session():
    seen = {}

    async def fake_impl(content, session_id=None, owner=None):
        seen["content"] = content
        seen["session_id"] = session_id
        seen["owner"] = owner
        return {"available": True, "models": []}

    original = mt.list_media_models
    mt.list_media_models = fake_impl
    try:
        result = asyncio.run(mt.ListMediaModelsTool().execute(
            "image", {"session_id": "sid1", "owner": "alice"},
        ))
    finally:
        mt.list_media_models = original

    assert result == {"available": True, "models": []}
    assert seen["owner"] == "alice"
    assert seen["session_id"] == "sid1"
    assert seen["content"] == "image"


def test_list_media_models_not_routed_via_dispatch_ai_tool():
    """Mirror test_model_interaction_registry: registry path, not legacy elif."""
    source = (Path(__file__).resolve().parent.parent / "src" / "tool_execution.py").read_text(encoding="utf-8")
    assert '"list_media_models"' in source
    assert 'elif tool in ("chat_with_model", "ask_teacher", "list_models", "list_media_models"):' in source

    marker = "from src.ai_interaction import dispatch_ai_tool"
    idx = source.index(marker)
    branch_head = source.rfind("elif tool in (", 0, idx)
    legacy_tuple = source[branch_head:idx]
    assert '"list_media_models"' not in legacy_tuple
