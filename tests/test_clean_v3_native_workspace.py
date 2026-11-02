import base64
import io

from PIL import Image

from src.clean_agent_preview import (
    NATIVE_WORKSPACE_TOOLS,
    bounded_visual_result_blocks,
    native_input_files_clause,
    preview_call_allowed,
    preview_http_timeout,
    protocol_safe_tool_calls,
    scope_preview_contract,
)
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract


def test_native_workspace_tools_require_native_authority():
    arguments = {"path": "/workspace/input.png"}
    assert not preview_call_allowed("inspect_media", arguments, "inspect the file")
    assert preview_call_allowed(
        "inspect_media",
        arguments,
        "inspect the file",
        allow_native_workspace=True,
    )


def test_native_workspace_contract_includes_pdf_extraction_for_document_tasks():
    assert "pdf_extract" in NATIVE_WORKSPACE_TOOLS


def test_native_workspace_contract_includes_transcription_for_media_tasks():
    assert "transcribe_media" in NATIVE_WORKSPACE_TOOLS


def test_native_input_files_are_named_in_trusted_runtime_clause():
    clause = native_input_files_clause({
        "surface": "odysseus-native",
        "terminal_agent": True,
        "input_files": ["/workspace/fixtures/tutorial.mp4"],
    })

    assert "/workspace/fixtures/tutorial.mp4" in clause
    assert native_input_files_clause({"input_files": ["/etc/passwd"]}) == ""


def test_native_workspace_allows_scoped_write_and_python_only_when_enabled():
    write = {"path": "/workspace/output.html", "content": "<html></html>"}
    python = {"code": "1 + 1"}

    assert not preview_call_allowed("write_file", write, "write the output")
    assert not preview_call_allowed("python", python, "analyze the file")
    assert preview_call_allowed(
        "python", python, "analyze the file", allow_execute_code=True
    )
    assert preview_call_allowed(
        "write_file", write, "write the output", allow_native_workspace=True
    )
    assert preview_call_allowed(
        "python",
        python,
        "analyze the file",
        allow_execute_code=True,
        allow_native_workspace=True,
    )


def test_native_workspace_exposes_exact_edit_and_search_without_interactive_access():
    for name, args in [
        ('edit_file', {'path': '/workspace/a.txt', 'old_string': 'alpha', 'new_string': 'beta'}),
        ('glob', {'pattern': '*.txt', 'path': '/workspace'}),
        ('grep', {'pattern': 'alpha', 'path': '/workspace'}),
    ]:
        assert name in NATIVE_WORKSPACE_TOOLS
        assert preview_call_allowed(name, args, 'Use the workspace tool', allow_native_workspace=True)
        assert not preview_call_allowed(name, args, 'Use the workspace tool')


def test_native_followup_tool_floor_respects_explicit_denials():
    names = {'read_file', 'edit_file', 'glob', 'grep'}
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in names]
    for policy, expected in [
        (ToolPolicy(disabled_tools=frozenset({'edit_file'})), names - {'edit_file'}),
        (ToolPolicy(block_all_tool_calls=True), set()),
    ]:
        preview = resolve_full_inventory_contract(schemas=schemas, policy=policy)
        routed = resolve_full_inventory_contract(schemas=[], policy=policy)
        scoped = scope_preview_contract(preview, routed, set(), extra_tools=NATIVE_WORKSPACE_TOOLS)
        assert scoped.offered == expected


def test_native_visual_evidence_packs_every_frame_within_three_images():
    colors = [(240, 10, 10), (10, 240, 10), (10, 10, 240),
              (240, 240, 10), (240, 10, 240)]
    images = []
    for color in colors:
        buffer = io.BytesIO()
        Image.new("RGB", (16, 8), color).save(buffer, "PNG")
        images.append({
            "mimeType": "image/png",
            "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
        })
    result = {"images": images, "frame_timestamps": list(range(len(images)))}

    blocks = bounded_visual_result_blocks(result, max_images=3)

    assert len(blocks) == 3
    observed = set()
    for block in blocks:
        encoded = block["image_url"]["url"].split(",", 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as packed:
            for pixel in packed.convert("RGB").getdata():
                if pixel in colors:
                    observed.add(pixel)
    assert observed == set(colors)


def test_native_multimodal_stream_gets_long_read_timeout_only_in_native_mode():
    assert preview_http_timeout().read == 90
    assert preview_http_timeout(native_workspace_enabled=True).read == 600
    assert preview_http_timeout(native_workspace_enabled=True).connect == 10


def test_malformed_tool_arguments_are_safe_in_provider_history():
    calls = [{
        "id": "call-1",
        "type": "function",
        "function": {"name": "write_file", "arguments": '{"path":"/workspace/output.html"'},
    }]

    safe_calls = protocol_safe_tool_calls(calls)

    assert safe_calls[0]["function"]["arguments"] == "{}"
    assert calls[0]["function"]["arguments"].startswith('{"path"')


def test_native_artifact_scope_adds_only_local_browser():
    names = {"write_file", "private_browser", "web_search", "web_fetch"}
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] in names]
    preview = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    routed = resolve_full_inventory_contract(
        schemas=[s for s in schemas if s["function"]["name"] == "write_file"],
        policy=ToolPolicy(),
    )
    scoped = scope_preview_contract(
        preview, routed, {"shell_files"}, extra_tools={"private_browser"}
    )
    assert scoped.offered == {"write_file", "private_browser"}
    assert "web_search" not in scoped.offered
    assert "web_fetch" not in scoped.offered


def test_native_workspace_floor_keeps_pdf_extraction_without_public_web_tools():
    names = {"inspect_media", "write_file", "python", "pdf_extract", "web_search"}
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] in names]
    preview = resolve_full_inventory_contract(schemas=schemas, policy=ToolPolicy())
    routed = resolve_full_inventory_contract(
        schemas=[s for s in schemas if s["function"]["name"] == "write_file"],
        policy=ToolPolicy(),
    )

    scoped = scope_preview_contract(
        preview,
        routed,
        {"shell_files"},
        extra_tools=NATIVE_WORKSPACE_TOOLS,
    )

    assert scoped.offered == {"inspect_media", "write_file", "python", "pdf_extract"}
    assert "web_search" not in scoped.offered
