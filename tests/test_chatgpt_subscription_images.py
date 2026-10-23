"""Regression: image attachments must survive the Responses-API conversion.

build_responses_input flattened every content part into one text string. An
image part carries neither "text" nor "content", so it collapsed to "" and the
attachment was dropped before the request left the box — the ChatGPT
Subscription (Codex) model then answered "No image was provided."

The Responses API wants a separate `input_image` part whose `image_url` is a
bare string. Passing the chat-completions `{"url": ...}` object instead is
rejected with:
    Invalid type for 'input[0].content[1].image_url': expected an image URL,
    but got an object instead.
"""
from src import chatgpt_subscription as cs

DATA_URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def _parts(out, i=0):
    return out[i]["content"]


def test_image_url_object_becomes_input_image_string():
    # The shape build_user_content actually produces.
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": DATA_URI}},
    ]}]
    parts = _parts(cs.build_responses_input(msgs))
    assert parts[0] == {"type": "input_text", "text": "what is this?"}
    assert parts[1] == {"type": "input_image", "image_url": DATA_URI}


def test_accepts_already_flat_image_url_and_bare_image():
    for part in (
        {"type": "input_image", "image_url": DATA_URI},
        {"type": "image", "image": DATA_URI},
    ):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}, part]}]
        parts = _parts(cs.build_responses_input(msgs))
        assert parts[1] == {"type": "input_image", "image_url": DATA_URI}, part


def test_multiple_images_all_survive():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "compare"},
        {"type": "image_url", "image_url": {"url": DATA_URI + "A"}},
        {"type": "image_url", "image_url": {"url": DATA_URI + "B"}},
    ]}]
    parts = _parts(cs.build_responses_input(msgs))
    assert [p["type"] for p in parts] == ["input_text", "input_image", "input_image"]
    assert parts[1]["image_url"].endswith("A")
    assert parts[2]["image_url"].endswith("B")


def test_image_part_does_not_pollute_the_text():
    # The old code appended "" for the image part, padding the text with a
    # stray newline. Text should be exactly the text parts.
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "only this"},
        {"type": "image_url", "image_url": {"url": DATA_URI}},
    ]}]
    assert _parts(cs.build_responses_input(msgs))[0]["text"] == "only this"


def test_malformed_image_parts_are_dropped_not_crashed():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {}},        # no url key
        {"type": "image_url", "image_url": None},      # null
        {"type": "image_url", "image_url": {"url": "   "}},  # blank
    ]}]
    parts = _parts(cs.build_responses_input(msgs))
    assert [p["type"] for p in parts] == ["input_text"]


def test_assistant_turn_never_carries_input_image():
    # An assistant turn's content must stay output_text; an input_image there
    # is an invalid shape for the API.
    msgs = [{"role": "assistant", "content": [
        {"type": "text", "text": "here you go"},
        {"type": "image_url", "image_url": {"url": DATA_URI}},
    ]}]
    parts = _parts(cs.build_responses_input(msgs))
    assert [p["type"] for p in parts] == ["output_text"]


def test_tool_role_is_remapped_to_user_and_keeps_images():
    msgs = [{"role": "tool", "content": [
        {"type": "text", "text": "tool said"},
        {"type": "image_url", "image_url": {"url": DATA_URI}},
    ]}]
    out = cs.build_responses_input(msgs)
    assert out[0]["role"] == "user"
    assert out[0]["content"][1] == {"type": "input_image", "image_url": DATA_URI}


def test_plain_string_content_still_works():
    out = cs.build_responses_input([{"role": "user", "content": "hello"}])
    assert out[0]["content"] == [{"type": "input_text", "text": "hello"}]
