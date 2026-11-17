from email import message_from_string

from mcp_servers.email_server import _extract_text as mcp_extract_text
from routes.email_helpers import _extract_text as route_extract_text
from src.agent_loop import (
    _email_fact_lookup_requested,
    _email_lookup_needs_post_synthesis,
    _email_lookup_request_from_messages,
    _email_read_evidence_from_tool_output,
)


def test_nonmultipart_html_email_is_plain_text_at_both_boundaries():
    message = message_from_string(
        "Content-Type: text/html; charset=utf-8\n\n"
        "<html><body><div>Property address:</div>"
        "<div><b>598-7 Nagakura</b>, Karuizawa</div></body></html>"
    )

    for extract in (mcp_extract_text, route_extract_text):
        body = extract(message)
        assert "Property address:" in body
        assert "598-7 Nagakura" in body
        assert "<div>" not in body


def test_email_lookup_recovers_request_before_terse_followup():
    messages = [
        {
            "role": "user",
            "content": "Find the address of the Karuizawa property in my emails",
        },
        {"role": "assistant", "content": "Let me check."},
        {"role": "user", "content": "did you find it yet?"},
    ]

    assert _email_lookup_request_from_messages(messages, "did you find it yet?") == (
        "Find the address of the Karuizawa property in my emails"
    )


def test_email_fact_lookup_does_not_capture_explicit_read_request():
    assert _email_fact_lookup_requested("Find the address in that property email")
    assert _email_fact_lookup_requested("What did Lisa say about the deadline?")
    assert not _email_fact_lookup_requested("Open that email")
    assert not _email_fact_lookup_requested("Read the email to me")


def test_successful_draft_suppresses_redundant_email_lookup_synthesis():
    events = [
        {"tool": "mcp__email__read_email", "exit_code": 0, "output": "Price: $42"},
        {"tool": "mcp__email__draft_email", "exit_code": 0, "output": "Draft created"},
    ]

    assert not _email_lookup_needs_post_synthesis(
        "Read the bid, determine the winner, and draft a recommendation", events
    )
    assert _email_lookup_needs_post_synthesis(
        "What price was quoted in the email?", events[:1]
    )


def test_email_lookup_evidence_strips_html_and_is_bounded():
    raw = (
        "**Subject:** Property settlement\n"
        "**UID:** 2277\n\n---\n\n"
        "<html><body><div>Address: 598-7 Nagakura</div>"
        + ("<p>extra</p>" * 100)
        + "</body></html>"
    )

    evidence = _email_read_evidence_from_tool_output(raw, max_body_chars=180)
    assert "Address: 598-7 Nagakura" in evidence
    assert "<html>" not in evidence
    assert len(evidence) < 230


def test_read_email_tool_header_is_clickable_live_and_after_refresh():
    for path in ("static/js/chat.js", "static/js/chatRenderer.js"):
        source = open(path, encoding="utf-8").read()
        assert "'Open this email'" in source
        assert "_toolHeaderHashLinkHtml(" in source
