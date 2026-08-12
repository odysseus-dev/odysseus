from pathlib import Path


SRC = Path("static/js/document.js")


def _send_intent_body():
    source = SRC.read_text(encoding="utf-8")
    start = source.index("const handleSendIntent = (e) => {")
    end = source.index("window.odysseusEmailSendIntent = handleSendIntent;", start)
    return source[start:end]


def test_email_send_intent_ignores_hidden_zero_rect_buttons():
    body = _send_intent_body()

    rect_idx = body.index("const rect = candidate.getBoundingClientRect();")
    visible_idx = body.index("candidate.offsetParent !== null")
    width_idx = body.index("rect.width > 0")
    height_idx = body.index("rect.height > 0")
    hit_idx = body.index("_eventInsideElement(e, candidate)")

    assert rect_idx < visible_idx < hit_idx
    assert rect_idx < width_idx < hit_idx
    assert rect_idx < height_idx < hit_idx
