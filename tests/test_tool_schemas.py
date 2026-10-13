import jsonschema

from src.tool_schemas import (
    FUNCTION_TOOL_SCHEMAS,
    function_call_to_tool_block,
    normalize_native_function_args,
    normalized_native_function_argument_error,
)
import json


def test_inspect_media_schema_matches_twelve_page_runtime_limit() -> None:
    schema = next(
        item["function"]
        for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    variants = schema["parameters"]["properties"]["pages"]["oneOf"]
    page_list = next(item for item in variants if item["type"] == "array")
    assert page_list["maxItems"] == 12


def test_host_shell_repairs_legacy_test_action_shape() -> None:
    block = function_call_to_tool_block(
        "host_shell",
        '{"action":"pytest","path":"tests/test_host_patch.py"}',
    )

    assert block is not None
    assert block.tool_type == "host_shell"
    assert block.content == '{"action": "pytest", "path": "tests/test_host_patch.py", "command": "pytest -q -- tests/test_host_patch.py"}'


def test_host_shell_rejects_unknown_action_shape() -> None:
    block = function_call_to_tool_block(
        "host_shell",
        '{"action":"delete_everything","path":"."}',
    )

    assert block is None


def test_web_search_native_command_arg_repairs_to_query_before_required_check() -> None:
    block = function_call_to_tool_block(
        "web_search",
        '{"command":"PISA 2022 OECD report Japan Sweden scores mathematics reading science"}',
    )

    assert block is not None
    assert block.tool_type == "web_search"
    assert block.content == "PISA 2022 OECD report Japan Sweden scores mathematics reading science"


def test_web_search_native_query_wins_over_redundant_tool_name_command() -> None:
    block = function_call_to_tool_block(
        "web_search",
        '{"command":"web_search","query":"gpt-4 official source site:openai.com","time_filter":"year"}',
    )

    assert block is not None
    assert block.tool_type == "web_search"
    assert json.loads(block.content) == {
        "query": "gpt-4 official source site:openai.com",
        "time_filter": "year",
    }


def test_create_document_human_language_code_does_not_pollute_body() -> None:
    block = function_call_to_tool_block(
        "create_document",
        '{"title":"Repair Notes","language":"en","content":"The harness is fixed."}',
    )

    assert block is not None
    assert block.tool_type == "create_document"
    assert block.content == "Repair Notes\nThe harness is fixed."


def test_web_search_native_q_arg_repairs_to_query_before_required_check() -> None:
    block = function_call_to_tool_block("web_search", '{"q":"latest Apple Mac mini specs"}')

    assert block is not None
    assert block.tool_type == "web_search"
    assert block.content == "latest Apple Mac mini specs"


def test_web_fetch_native_command_arg_repairs_to_url_before_required_check() -> None:
    block = function_call_to_tool_block("web_fetch", '{"command":"https://www.apple.com/mac-mini/"}')

    assert block is not None
    assert block.tool_type == "web_fetch"
    assert json.loads(block.content)["url"] == "https://www.apple.com/mac-mini/"


def test_inspect_media_native_legacy_paths_alias_repairs_to_path() -> None:
    block = function_call_to_tool_block(
        "inspect_media",
        '{"paths":"[\\"/workspace/fixtures/market_archive.mp4\\"]","frames":20}',
    )

    assert block is not None
    assert block.tool_type == "inspect_media"
    assert json.loads(block.content)["path"] == "/workspace/fixtures/market_archive.mp4"


def test_inspect_media_single_clip_export_shape_normalizes_before_schema_validation() -> None:
    schema = next(
        item for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    raw = {
        "path": "/workspace/fixtures/market_archive.mp4",
        "exports": [{
            "start": "00:00:10",
            "end": "00:00:16",
            "output_path": "/workspace/clip.mp4",
        }],
    }

    tool_type, normalized = normalize_native_function_args("inspect_media", raw)

    assert tool_type == "inspect_media"
    assert normalized == {
        "path": "/workspace/fixtures/market_archive.mp4",
        "start": "00:00:10",
        "end": "00:00:16",
        "output_path": "/workspace/clip.mp4",
    }
    jsonschema.validate(normalized, schema["function"]["parameters"])


def test_inspect_media_json_encoded_single_clip_export_normalizes() -> None:
    tool_type, normalized = normalize_native_function_args(
        "inspect_media",
        {
            "path": "/workspace/fixtures/market_archive.mp4",
            "exports": json.dumps([{
                "start": "10",
                "end": "16",
                "output_path": "/workspace/clip.mp4",
            }]),
        },
    )

    assert tool_type == "inspect_media"
    assert normalized["start"] == "10"
    assert normalized["end"] == "16"
    assert normalized["output_path"] == "/workspace/clip.mp4"
    assert "exports" not in normalized


def test_inspect_media_rejects_still_export_without_timestamp() -> None:
    block = function_call_to_tool_block(
        "inspect_media",
        json.dumps({
            "path": "/workspace/fixtures/video.mp4",
            "exports": [{
                "output_path": "/workspace/frame.png",
                "type": "diagram",
            }],
        }),
    )

    assert block is None


def test_read_file_binary_visual_media_routes_to_inspect_media() -> None:
    block = function_call_to_tool_block(
        "read_file",
        '{"path":"/workspace/fixtures/market_archive.mp4"}',
    )

    assert block is not None
    assert block.tool_type == "inspect_media"
    assert json.loads(block.content) == {
        "path": "/workspace/fixtures/market_archive.mp4",
    }


def test_read_file_text_and_structured_documents_remain_read_file() -> None:
    for path in ("/workspace/notes.txt", "/workspace/report.pdf"):
        block = function_call_to_tool_block("read_file", json.dumps({"path": path}))
        assert block is not None
        assert block.tool_type == "read_file"
        assert block.content == path


def test_common_directory_listing_aliases_route_to_ls() -> None:
    for name in ("list_files", "list_directory"):
        block = function_call_to_tool_block(name, '{"path":"/workspace/fixtures"}')
        assert block is not None
        assert block.tool_type == "ls"
        assert json.loads(block.content) == {"path": "/workspace/fixtures"}


def test_truncated_readonly_media_call_keeps_complete_segments() -> None:
    arguments = (
        '{"path":"/workspace/fixtures/video.mp4","segments":['
        '{"start":"00:00:00","end":"00:00:15"},'
        '{"start":"00:00:15","end":"00:00:30"}],'
        '"fr\n</parameter":'
    )

    block = function_call_to_tool_block("inspect_media", arguments)

    assert block is not None
    assert block.tool_type == "inspect_media"
    assert json.loads(block.content) == {
        "path": "/workspace/fixtures/video.mp4",
        "segments": [
            {"start": "00:00:00", "end": "00:00:15"},
            {"start": "00:00:15", "end": "00:00:30"},
        ],
    }


def test_truncated_media_mutation_call_remains_fail_closed() -> None:
    arguments = (
        '{"path":"/workspace/fixtures/video.mp4",'
        '"output_path":"/workspace/clip.mp4","start":"0","end":"5",'
        '"broken":'
    )

    assert function_call_to_tool_block("inspect_media", arguments) is None


def test_missing_still_timestamp_has_actionable_semantic_error() -> None:
    tool_type, arguments = normalize_native_function_args("inspect_media", {
        "path": "/workspace/fixtures/video.mp4",
        "exports": [{
            "output_path": "/workspace/floorplan.png",
            "type": "diagram",
        }],
    })

    error = normalized_native_function_argument_error(tool_type, arguments)

    assert error is not None
    assert "explicit timestamp plus output_path" in error
    assert "write_file or python" in error
