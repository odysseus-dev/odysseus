import json

from src.ai_interaction import _manage_memory_lines
from src.tool_schemas import function_call_to_tool_block


def lines(**payload):
    return _manage_memory_lines(json.dumps(payload))


def test_add_preserves_public_schema_category():
    assert lines(action="add", text="I prefer quiet rooms", category="preference") == [
        "add", "I prefer quiet rooms", "preference",
    ]


def test_edit_keeps_identifier_separate_from_replacement_text():
    assert lines(action="edit", memory_id="abc123", text="I prefer daylight") == [
        "edit", "abc123", "I prefer daylight",
    ]


def test_list_uses_category_and_search_uses_text():
    assert lines(action="list", category="event") == ["list", "event"]
    assert lines(action="search", text="daylight") == ["search", "daylight"]


def test_command_form_remains_supported_without_structured_action():
    assert lines(command="add\nI use metric units\npreference") == [
        "add", "I use metric units", "preference",
    ]


def test_command_payload_is_supported_alongside_structured_action():
    assert lines(action="search", command="project Aurora") == [
        "search", "project Aurora",
    ]
    assert lines(action="delete", command="abc123") == ["delete", "abc123"]
    assert lines(action="edit", command="abc123\nI prefer daylight") == [
        "edit", "abc123", "I prefer daylight",
    ]
    assert lines(action="add", command="I use metric units\npreference") == [
        "add", "I use metric units", "preference",
    ]


def test_list_does_not_treat_command_as_mutation_payload():
    assert lines(action="list", command="delete\nall") == ["list", ""]


def test_function_converter_keeps_action_when_command_is_only_payload():
    block = function_call_to_tool_block(
        "manage_memory",
        json.dumps({"action": "search", "command": "project Aurora"}),
    )
    assert block is not None
    assert block.tool_type == "manage_memory"
    assert block.content == "search\nproject Aurora"


def test_function_converter_does_not_duplicate_complete_command_action():
    block = function_call_to_tool_block(
        "manage_memory",
        json.dumps({"action": "search", "command": "search\nproject Aurora"}),
    )
    assert block is not None
    assert block.content == "search\nproject Aurora"
