"""The thread parser must treat the Gmail-style "---------- Forwarded message
---------" divider as a quote boundary, like "----- Original Message -----".

`_ORIG_RE` already recognised the Japanese forward marker (転送) but not the
English "Forwarded message" one, so forwarded mail produced by Odysseus itself
(static/js/emailInbox.js emits exactly `---------- Forwarded message ----------`)
leaked the divider into the level-0 reply bubble — or, with no Outlook header
block to fall back on, was not split into turns at all.
"""
from src.email_thread_parser import parse_thread


def test_forwarded_divider_not_leaked_into_reply_body():
    text = (
        "See below.\n\n"
        "---------- Forwarded message ---------\n"
        "From: Alice <alice@example.com>\n"
        "Date: Thu, May 7, 2026 at 11:33 AM\n"
        "Subject: Original subject\n"
        "To: Bob <bob@x.com>\n\n"
        "Forwarded body content.\n"
    )
    turns = parse_thread(None, text)
    assert turns is not None

    # The reply turn must be clean — the divider is noise, not reply content.
    assert turns[0]["level"] == 0
    assert "Forwarded message" not in turns[0]["body_html"]
    # No turn at all should carry the raw divider in its rendered body.
    assert all("Forwarded message" not in t["body_html"] for t in turns)

    # The forwarded content becomes a deeper turn with sender meta.
    deeper = [t for t in turns if t["level"] >= 1]
    assert deeper, "forwarded body should split into a deeper turn"
    assert "alice@example.com" in (deeper[0]["meta"] or "")
    assert "Forwarded body content." in deeper[0]["body_html"]


def test_forwarded_divider_alone_triggers_split():
    # No Outlook header block — only the divider marks the forward. Before the
    # fix this returned None (no split), folding the forward into the reply.
    text = (
        "See the message below.\n\n"
        "---------- Forwarded message ----------\n"
        "Forwarded body with no header block.\n"
    )
    turns = parse_thread(None, text)
    assert turns is not None
    assert any(t["level"] >= 1 for t in turns)
    assert all("Forwarded message" not in t["body_html"] for t in turns)


def test_forwarded_words_without_delimiters_do_not_split():
    # Negative control: the bare words "forwarded message" in normal prose,
    # with no [-_=]{3,} delimiters, must NOT be treated as a divider.
    text = "I forwarded message after message to the team but heard nothing back."
    assert parse_thread(None, text) is None
