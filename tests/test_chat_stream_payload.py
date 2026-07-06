import pytest

from src.chat_stream_payload import parse_chat_stream_payload


def test_json_payload_normalizes_stream_fields():
    payload = parse_chat_stream_payload(
        {},
        {
            "message": " hello ",
            "session": " s1 ",
            "attachments": [" att-1 "],
            "use_web": "false",
            "use_research": True,
            "time_filter": " week ",
            "preset_id": " p1 ",
            "allow_bash": True,
            "allow_web_search": "false",
            "use_rag": "0",
            "search_context": " cached results ",
            "compare_mode": "yes",
            "incognito": "on",
            "mode": "agent",
            "workspace": " C:/work ",
            "approved_plan": " step ",
            "active_doc_id": " doc-1 ",
            "no_memory": "true",
        },
    )

    assert payload.message == "hello"
    assert payload.session == "s1"
    assert payload.attachments == ["att-1"]
    assert payload.use_web is False
    assert payload.use_research is True
    assert payload.time_filter == "week"
    assert payload.preset_id == "p1"
    assert payload.allow_bash is True
    assert payload.allow_web_search is False
    assert payload.use_rag is False
    assert payload.search_context == "cached results"
    assert payload.compare_mode is True
    assert payload.incognito is True
    assert payload.chat_mode == "agent"
    assert payload.workspace == "C:/work"
    assert payload.approved_plan == "step"
    assert payload.active_doc_id == "doc-1"
    assert payload.no_memory is True


def test_form_data_takes_precedence_over_json_body():
    payload = parse_chat_stream_payload(
        {"message": " form ", "session": " form-session ", "allow_bash": "false"},
        {"message": "json", "session": "json-session", "allow_bash": True},
    )

    assert payload.message == "form"
    assert payload.session == "form-session"
    assert payload.allow_bash is False


def test_missing_mode_defaults_to_chat():
    payload = parse_chat_stream_payload({"message": "hello", "session": "s1"}, None)

    assert payload.chat_mode == "chat"


def test_rejects_non_object_json_body():
    with pytest.raises(ValueError, match="JSON body must be an object"):
        parse_chat_stream_payload({}, ["not", "an", "object"])


@pytest.mark.parametrize(
    "attachments",
    [
        "not-json",
        "{}",
        ["ok", ""],
        ["ok", 123],
    ],
)
def test_rejects_malformed_attachments(attachments):
    with pytest.raises(ValueError):
        parse_chat_stream_payload({"attachments": attachments}, {"message": "hello", "session": "s1"})


def test_parses_form_attachment_json_array():
    payload = parse_chat_stream_payload(
        {"message": "hello", "session": "s1", "attachments": '[" att-1 ", "att-2"]'},
        None,
    )

    assert payload.attachments == ["att-1", "att-2"]


def test_rejects_invalid_mode():
    with pytest.raises(ValueError, match="mode must be chat or agent"):
        parse_chat_stream_payload({"message": "hello", "session": "s1", "mode": "tools"}, None)


def test_invalid_time_filter_degrades_to_none():
    payload = parse_chat_stream_payload(
        {"message": "hello", "session": "s1", "time_filter": "decade"},
        None,
    )

    assert payload.time_filter is None
