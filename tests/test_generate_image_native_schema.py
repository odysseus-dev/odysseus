"""Issue #5520 — generate_image had no native function schema.

The tool exists in the fenced-block prompt, the executor, and the image_gen MCP
server, but not in FUNCTION_TOOL_SCHEMAS. API models (native function calling)
are only sent schemas from that list, so they could never call it and improvised
malformed text calls instead.
"""

import json


def _schemas():
    import src.agent_tools  # noqa: F401  (tool_schemas <-> agent_tools import cycle)
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    return {s["function"]["name"]: s["function"] for s in FUNCTION_TOOL_SCHEMAS}


def _convert(arguments):
    import src.agent_tools  # noqa: F401
    from src.tool_schemas import function_call_to_tool_block

    return function_call_to_tool_block("generate_image", json.dumps(arguments))


def test_generate_image_is_offered_to_native_function_calling_models():
    schemas = _schemas()
    assert "generate_image" in schemas, (
        "generate_image has no native schema, so it is filtered out of the tools "
        "sent to API models even when the selector picks it"
    )
    assert schemas["generate_image"]["parameters"]["required"] == ["prompt"]


def test_native_call_reaches_the_executor_with_its_arguments_intact():
    from src.tool_execution import _build_mcp_args

    args = {"prompt": "a cat riding a bicycle", "model": "gpt-image-1",
            "size": "1024x1024", "quality": "high"}
    block = _convert(args)
    assert block is not None and block.tool_type == "generate_image"
    assert _build_mcp_args("generate_image", block.content) == args


def test_call_without_a_prompt_is_rejected_instead_of_drawing_its_own_arguments():
    """No prompt key means the line parser takes the whole JSON blob as the prompt,
    so the model gets an image of its own arguments."""
    assert _convert({"size": "512x512"}) is None
    assert _convert({}) is None
    assert _convert({"prompt": "   "}) is None


def test_advertised_parameters_are_the_ones_the_image_server_accepts():
    """Anything the schema advertises but the executor drops is a silent no-op."""
    from mcp_servers.image_gen_server import list_tools
    import asyncio

    server_schema = asyncio.run(list_tools())[0].inputSchema
    assert set(_schemas()["generate_image"]["parameters"]["properties"]) == set(
        server_schema["properties"]
    )
